"""store 层：建表、成员池 upsert、按群查询。"""

from __future__ import annotations

import sqlite3
import time


def test_init_creates_all_tables(tmp_store):
    conn = sqlite3.connect(tmp_store)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {
        "waifu_members",
        "waifu_daily",
        "waifu_marriage",
        "waifu_pending",
        "waifu_divorce_cd",
        "waifu_locked",
        "waifu_drawn",
    } <= names


def test_touch_member_inserts_then_updates(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    rows = store.list_members(100)
    assert len(rows) == 1
    assert rows[0]["nickname"] == "甲"

    # 二次 upsert 只更新时间与昵称，不新增行
    store.touch_member(group_id=100, user_id=1, nickname="甲改名")
    rows = store.list_members(100)
    assert len(rows) == 1
    assert rows[0]["nickname"] == "甲改名"


def test_list_members_is_scoped_to_group(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    store.touch_member(group_id=200, user_id=2, nickname="乙")
    assert [r["user_id"] for r in store.list_members(100)] == [1]
    assert [r["user_id"] for r in store.list_members(200)] == [2]


def test_touch_member_does_not_clobber_nickname_with_empty(tmp_store):
    """昵称取不到时不要用空串覆盖已有的好昵称。"""
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    store.touch_member(group_id=100, user_id=1, nickname="")
    assert store.list_members(100)[0]["nickname"] == "甲"


def test_last_seen_advances(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    first = store.list_members(100)[0]["last_seen"]
    time.sleep(0.01)
    store.touch_member(group_id=100, user_id=1, nickname="甲")
    second = store.list_members(100)[0]["last_seen"]
    assert second >= first


def test_set_and_get_daily(tmp_store):
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    row = store.get_daily(100, 1, "2026-10-10")
    assert row is not None
    assert row["partner_id"] == 2
    assert row["locked"] == 0
    assert row["draws_used"] == 1  # 首次抽取已计数


def test_set_daily_overwrites_partner_and_keeps_counter(tmp_store):
    """换一只：换人但**累加**计数（不是重置）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=3)
    row = store.get_daily(100, 1, "2026-10-10")
    assert row["partner_id"] == 3
    assert row["draws_used"] == 2


def test_lock_daily(tmp_store):
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    assert store.lock_daily(100, 1, "2026-10-10") is True
    assert store.get_daily(100, 1, "2026-10-10")["locked"] == 1


def test_lock_daily_returns_false_without_record(tmp_store):
    from nonebot_plugin_waifu import store

    assert store.lock_daily(100, 1, "2026-10-10") is False


def test_locked_partners_today(tmp_store):
    """今天**我**确认过的 partner 集合（按 user 隔离，用来从我自己的抽取池里移除）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.set_daily(group_id=100, user_id=3, date_key="2026-10-10", partner_id=4)
    store.lock_daily(100, 1, "2026-10-10")  # user 1 锁了 2
    assert store.locked_partners(100, 1, "2026-10-10") == {2}
    # user 3 没确认 → 空（锁只对确认者生效）
    assert store.locked_partners(100, 3, "2026-10-10") == set()
    # 跨日 → 空（每日恢复）
    assert store.locked_partners(100, 1, "2026-10-11") == set()


def test_confirm_survives_redraw(tmp_store):
    """**本 bug 的回归守卫**：确认 X 后换一只（重抽到 Y）不能把 X 的锁冲掉。"""
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-10"
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=2)
    assert store.lock_daily(g, me, day) is True
    # 换一只：重抽到 3 → 旧列 locked 被清零，但 waifu_locked 那一行不动
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=3)
    assert store.get_daily(g, me, day)["partner_id"] == 3
    assert store.locked_partners(g, me, day) == {2}, "确认效果被下一次抽取冲掉了"


def test_lock_only_affects_me(tmp_store):
    """锁只对我生效：我确认了 X，别人抽签时 X 仍是候选（不锁对方）。"""
    from nonebot_plugin_waifu import store

    g, me, other, day = 100, 1, 9, "2026-10-10"
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=2)
    store.lock_daily(g, me, day)
    assert store.locked_partners(g, me, day) == {2}
    assert store.locked_partners(g, other, day) == set(), "锁泄漏给了别人"


def test_lock_is_one_per_day(tmp_store):
    """一天一个：再确认一次是**覆盖**（改为锁新的那位），不是累加。"""
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-10"
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=2)
    store.lock_daily(g, me, day)
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=3)
    store.lock_daily(g, me, day)  # 再确认一次 → 锁改成 3
    assert store.locked_partners(g, me, day) == {3}


def test_locked_partners_isolated_by_group_and_day(tmp_store):
    """群隔离 + 跨日恢复。"""
    from nonebot_plugin_waifu import store

    me = 1
    store.set_daily(group_id=100, user_id=me, date_key="2026-10-10", partner_id=2)
    store.lock_daily(100, me, "2026-10-10")
    store.set_daily(group_id=200, user_id=me, date_key="2026-10-10", partner_id=3)
    store.lock_daily(200, me, "2026-10-10")
    # 同一天、同一人、不同群互不影响
    assert store.locked_partners(100, me, "2026-10-10") == {2}
    assert store.locked_partners(200, me, "2026-10-10") == {3}
    # 跨日：昨天的锁不影响今天
    assert store.locked_partners(100, me, "2026-10-11") == set()


def test_has_lock_false_without_lock(tmp_store):
    """没确认过 → False。"""
    from nonebot_plugin_waifu import store

    assert store.has_lock(100, 1, "2026-10-10") is False


def test_has_lock_true_after_lock_daily(tmp_store):
    """`lock_daily` 后 → True。（确认即定死，用这个判「今天还能不能换」。）"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    assert store.has_lock(100, 1, "2026-10-10") is False
    assert store.lock_daily(100, 1, "2026-10-10") is True
    assert store.has_lock(100, 1, "2026-10-10") is True


def test_has_lock_false_on_other_day(tmp_store):
    """跨日 → False（每日恢复）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.lock_daily(100, 1, "2026-10-10")
    assert store.has_lock(100, 1, "2026-10-11") is False


def test_has_lock_isolated_by_group(tmp_store):
    """群隔离：我在 A 群确认了，B 群不算已确认。"""
    from nonebot_plugin_waifu import store

    me = 1
    store.set_daily(group_id=100, user_id=me, date_key="2026-10-10", partner_id=2)
    store.lock_daily(100, me, "2026-10-10")
    assert store.has_lock(100, me, "2026-10-10") is True
    assert store.has_lock(200, me, "2026-10-10") is False


def test_get_locked_partner_returns_confirmed_target(tmp_store):
    """A3 数据入口：我的「确认对象」；未确认 / 别人的锁 / 跨天 → None。"""
    from nonebot_plugin_waifu import store

    g, me, other, day = 100, 1, 9, "2026-10-10"
    assert store.get_locked_partner(g, me, day) is None  # 没确认过
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=2)
    store.lock_daily(g, me, day)
    assert store.get_locked_partner(g, me, day) == 2  # 值 = 确认对象
    assert store.get_locked_partner(g, other, day) is None  # 锁不泄漏给别人
    assert store.get_locked_partner(g, me, "2026-10-11") is None  # 跨天恢复
    assert store.get_locked_partner(200, me, day) is None  # 群隔离


def test_daily_is_scoped_by_date(tmp_store):
    """跨天：昨天的记录不影响今天（每日恢复）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-09", partner_id=2)
    assert store.get_daily(100, 1, "2026-10-10") is None


def test_marry_writes_both_directions(tmp_store):
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    assert store.get_partner(100, 1) == 2
    assert store.get_partner(100, 2) == 1
    assert store.is_married(100, 1) and store.is_married(100, 2)


def test_marry_is_atomic_both_rows_present(tmp_store):
    """两行必须在同一事务：任一行缺失都会让「单向已婚」这种脏状态出现。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=7, b=8)
    assert store.is_married(100, 7) and store.is_married(100, 8)


def test_divorce_clears_both_directions(tmp_store):
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    store.divorce(group_id=100, a=1, b=2)
    assert store.get_partner(100, 1) is None
    assert store.get_partner(100, 2) is None
    assert not store.is_married(100, 1)


def test_divorce_clears_marriage_rows(tmp_store):
    """离婚后：关系行清空、双方都不再「已婚」（冷却已随 2026-10-14 口径取消）。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    store.divorce(group_id=100, a=1, b=2)
    assert store.count_partners(100, 1) == 0
    assert store.count_partners(100, 2) == 0
    assert not store.is_married(100, 1) and not store.is_married(100, 2)


def test_get_partner_none_when_never_married(tmp_store):
    from nonebot_plugin_waifu import store

    assert store.get_partner(100, 1) is None
    assert not store.is_married(100, 1)


def test_marriage_scoped_to_group(tmp_store):
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    assert store.get_partner(200, 1) is None


def test_pending_roundtrip(tmp_store):
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=100.0
    )
    row = store.get_pending(100, requester=1, partner=2, kind="marry")
    assert row is not None
    assert row["requested_at"] == 100.0


def test_pending_missing_returns_none(tmp_store):
    from nonebot_plugin_waifu import store

    assert store.get_pending(100, requester=1, partner=2, kind="marry") is None


def test_pending_clear(tmp_store):
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=1.0
    )
    store.clear_pending(100, requester=1, partner=2, kind="marry")
    assert store.get_pending(100, requester=1, partner=2, kind="marry") is None


def test_pending_kinds_are_independent(tmp_store):
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=1.0
    )
    assert store.get_pending(100, requester=1, partner=2, kind="divorce") is None


def test_pending_direction_is_directional(tmp_store):
    """A→B 的请求不等于 B→A 的请求（双方各发一次才算成立）。"""
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=1.0
    )
    assert store.get_pending(100, requester=2, partner=1, kind="marry") is None


def test_purge_stale_pending(tmp_store):
    """清理超出窗口的请求（防止表无限增长）。"""
    from nonebot_plugin_waifu import store

    store.set_pending(
        group_id=100, requester=1, partner=2, kind="marry", requested_at=1.0
    )
    store.set_pending(
        group_id=100, requester=3, partner=4, kind="marry", requested_at=9e9
    )
    removed = store.purge_pending_before(now=1000.0, window=180)
    assert removed == 1
    assert store.get_pending(100, requester=3, partner=4, kind="marry") is not None


# --------------------------------------------------------------------------- #
# 当日抽取历史（waifu_drawn）：换一只不再重复抽到同一人
# --------------------------------------------------------------------------- #
def test_record_drawn_is_idempotent(tmp_store):
    """**幂等**：同一天同一人写两次 → 表里只有 1 行（`INSERT OR IGNORE`）。"""
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-11"
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=2)
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=2)

    conn = sqlite3.connect(tmp_store)
    n = conn.execute(
        "SELECT COUNT(*) FROM waifu_drawn"
        " WHERE group_id = ? AND user_id = ? AND date_key = ?",
        (g, me, day),
    ).fetchone()[0]
    conn.close()
    assert n == 1, "同一人同一天写两次应当只留 1 行（主键含 partner_id）"


def test_drawn_partners_basic_roundtrip(tmp_store):
    """基本读/写：抽过谁就记谁。"""
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-11"
    assert store.drawn_partners(g, me, day) == set()
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=2)
    assert store.drawn_partners(g, me, day) == {2}


def test_drawn_partners_accumulates(tmp_store):
    """**核心回归**：多次抽取**累积**（首次 + 每次换一只都计入）。"""
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-11"
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=2)
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=3)
    store.record_drawn(group_id=g, user_id=me, date_key=day, partner_id=4)
    assert store.drawn_partners(g, me, day) == {2, 3, 4}


def test_drawn_partners_isolated_by_user(tmp_store):
    """**按用户隔离**：A 抽过的人不出现在 B 的名单里。"""
    from nonebot_plugin_waifu import store

    g, a, b, day = 100, 1, 9, "2026-10-11"
    store.record_drawn(group_id=g, user_id=a, date_key=day, partner_id=2)
    assert store.drawn_partners(g, a, day) == {2}
    assert store.drawn_partners(g, b, day) == set(), "抽取历史泄漏给了别人"


def test_drawn_partners_isolated_by_group_and_day(tmp_store):
    """**按群隔离 + 跨日为空**（每日恢复）。"""
    from nonebot_plugin_waifu import store

    me = 1
    store.record_drawn(group_id=100, user_id=me, date_key="2026-10-11", partner_id=2)
    store.record_drawn(group_id=200, user_id=me, date_key="2026-10-11", partner_id=3)
    assert store.drawn_partners(100, me, "2026-10-11") == {2}
    assert store.drawn_partners(200, me, "2026-10-11") == {3}
    # 跨日：昨天抽过的人不影响今天
    assert store.drawn_partners(100, me, "2026-10-12") == set()


def test_set_daily_records_drawn(tmp_store):
    """**写入口唯一**：`set_daily`（首次与换一只都走它）会顺手记历史。

    防止以后有人在别处重写写入逻辑、漏掉 `record_drawn` 而换一只又重复抽到人。
    """
    from nonebot_plugin_waifu import store

    g, me, day = 100, 1, "2026-10-11"
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=7)
    # 换一只：重抽到 8
    store.set_daily(group_id=g, user_id=me, date_key=day, partner_id=8)
    assert store.drawn_partners(g, me, day) == {7, 8}
    # 记录历史**不改变**配额语义：仍旧累加 2 次
    assert store.get_daily(g, me, day)["draws_used"] == 2


def test_latest_locked_partners_keeps_newest_per_user(tmp_store):
    """每人只留**最近一次**确认的对象（`date_key` 字典序即时间序）→ 倾心数据源。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.lock_daily(100, 1, "2026-10-10")
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=3)
    store.lock_daily(100, 1, "2026-10-11")
    store.set_daily(group_id=100, user_id=4, date_key="2026-10-11", partner_id=5)
    store.lock_daily(100, 4, "2026-10-11")

    assert store.latest_locked_partners(100) == {1: 3, 4: 5}
    assert store.latest_locked_partners(999) == {}


def test_latest_drawn_partners_keeps_newest_per_user(tmp_store):
    """每人只留**最近一次抽取**的 partner（`date_key` 字典序即时间序）→ 喜欢数据源。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=3)
    store.set_daily(group_id=100, user_id=4, date_key="2026-10-11", partner_id=5)

    assert store.latest_drawn_partners(100) == {1: 3, 4: 5}
    assert store.latest_drawn_partners(999) == {}


def test_latest_drawn_partners_isolated_by_group(tmp_store):
    """按群隔离：别的群的抽取不进本群结果。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=2)
    store.set_daily(group_id=200, user_id=1, date_key="2026-10-11", partner_id=9)

    assert store.latest_drawn_partners(100) == {1: 2}
    assert store.latest_drawn_partners(200) == {1: 9}


def test_latest_drawn_partners_follows_last_redraw(tmp_store):
    """同一天「换一只」后，喜欢对象跟着变成**最后一次**抽到的人。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=2)
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=8)

    assert store.latest_drawn_partners(100) == {1: 8}


def test_latest_drawn_partners_is_not_the_locked_source(tmp_store):
    """抽取即喜欢、确认即倾心：两个来源互不覆盖。

    昨天抽到 2 并确认（倾心 2）、今天抽到 3 但没确认 → 喜欢是 3、倾心仍是 2。
    """
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.lock_daily(100, 1, "2026-10-10")
    store.set_daily(group_id=100, user_id=1, date_key="2026-10-11", partner_id=3)

    assert store.latest_drawn_partners(100) == {1: 3}
    assert store.latest_locked_partners(100) == {1: 2}


def test_clear_drawn_partner_keeps_the_counter_and_blocks_self_lock(tmp_store):
    """用户 2026-10-17：清掉当天抽到的那位 —— 计数原样、也不能把自己锁成「倾心」。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-17", partner_id=2)
    assert store.clear_drawn_partner(group_id=100, user_id=1, date_key="2026-10-17")

    row = store.get_daily(100, 1, "2026-10-17")
    assert int(row["partner_id"]) == 1, "该自指向（图上的自环会被过滤掉）"
    assert int(row["draws_used"]) == 1, "计数被动了 = 白送一次抽取"
    assert store.latest_drawn_partners(100) == {1: 1}

    # 没有可确认的对象：别把自指向锁成「倾心」
    assert store.lock_daily(100, 1, "2026-10-17") is False
    assert store.get_locked_partner(100, 1, "2026-10-17") is None
    assert store.latest_locked_partners(100) == {}

    # 当天没有记录 → False（幂等，别报错）
    assert (
        store.clear_drawn_partner(group_id=100, user_id=1, date_key="1999-01-01")
        is False
    )


def test_clear_drawn_partner_keeps_the_drawn_exclusion_set(tmp_store):
    """清掉**不**影响「今天已经抽到过谁」的排除集（下次不会再抽到他）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-17", partner_id=2)
    store.record_drawn(group_id=100, user_id=1, date_key="2026-10-17", partner_id=2)
    store.clear_drawn_partner(group_id=100, user_id=1, date_key="2026-10-17")

    assert store.drawn_partners(100, 1, "2026-10-17") == {2}
