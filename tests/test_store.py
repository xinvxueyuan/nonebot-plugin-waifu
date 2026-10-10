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
    """今日所有 locked=1 的 partner 集合（用来从抽取池里移除）。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=100, user_id=1, date_key="2026-10-10", partner_id=2)
    store.set_daily(group_id=100, user_id=3, date_key="2026-10-10", partner_id=4)
    store.lock_daily(100, 1, "2026-10-10")  # 2 被锁
    assert store.locked_partners(100, "2026-10-10") == {2}
    assert store.locked_partners(100, "2026-10-11") == set()


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
    married_ids = store.married_ids(100)
    assert married_ids == {7, 8}


def test_divorce_clears_both_directions(tmp_store):
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    store.divorce(group_id=100, a=1, b=2)
    assert store.get_partner(100, 1) is None
    assert store.get_partner(100, 2) is None
    assert not store.is_married(100, 1)


def test_divorce_clears_marriage_and_sets_cd_for_requester_only(tmp_store):
    """离婚后：关系清空，**只有发起方**有冷却。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    store.divorce(group_id=100, a=1, b=2)
    store.set_divorce_cd(group_id=100, user_id=1, until=999999.0)
    assert store.get_divorce_cd(100, 1) == 999999.0
    assert store.get_divorce_cd(100, 2) is None


def test_get_partner_none_when_never_married(tmp_store):
    from nonebot_plugin_waifu import store

    assert store.get_partner(100, 1) is None
    assert not store.is_married(100, 1)


def test_marriage_scoped_to_group(tmp_store):
    from nonebot_plugin_waifu import store

    store.marry(group_id=100, a=1, b=2)
    assert store.get_partner(200, 1) is None
