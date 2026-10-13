"""边界：跨天恢复、配额耗尽、锁定后无候选、性别过滤。"""

from __future__ import annotations

from test_draw_flow import GROUP, ME, OTHER


def test_daily_resets_across_days(tmp_store):
    """每日恢复：昨天的锁定不影响今天。"""
    from nonebot_plugin_waifu import store

    store.set_daily(group_id=GROUP, user_id=ME, date_key="2026-10-09", partner_id=OTHER)
    store.lock_daily(GROUP, ME, "2026-10-09")
    assert store.locked_partners(GROUP, ME, "2026-10-10") == set()
    assert store.get_daily(GROUP, ME, "2026-10-10") is None


def test_quota_is_per_person_per_day(tmp_store):
    from nonebot_plugin_waifu import rules, store

    for _ in range(20):
        store.set_daily(
            group_id=GROUP, user_id=ME, date_key="2026-10-10", partner_id=OTHER
        )
    row = store.get_daily(GROUP, ME, "2026-10-10")
    assert row["draws_used"] == 20
    assert not rules.can_draw(draws_used=int(row["draws_used"]), daily_limit=20).ok


def test_locked_partner_removed_from_own_draw_today(tmp_store):
    """锁定后**自己**不会再抽到 ta；别人不受影响（不锁对方）。"""
    from nonebot_plugin_waifu import rules, store

    store.touch_member(group_id=GROUP, user_id=ME, nickname="我")
    store.touch_member(group_id=GROUP, user_id=OTHER, nickname="他")
    store.set_daily(group_id=GROUP, user_id=ME, date_key="2026-10-10", partner_id=OTHER)
    store.lock_daily(GROUP, ME, "2026-10-10")

    pool = [
        rules.Candidate(user_id=ME, last_seen=1.0),
        rules.Candidate(user_id=OTHER, last_seen=1.0),
    ]
    got = rules.pick_target(
        pool,
        me=ME,
        my_sex="unknown",
        mode="all",
        excluded=store.locked_partners(GROUP, ME, "2026-10-10"),
        married=set(),
    )
    assert got is None


def test_same_sex_can_be_drawn_in_all_mode(tmp_store):
    """**默认档「所有」**：同性也能当老婆（不筛）。"""
    from nonebot_plugin_waifu import rules

    pool = [rules.Candidate(user_id=2, last_seen=1.0, sex="male")]
    got = rules.pick_target(
        pool, me=1, my_sex="male", mode="all", excluded=set(), married=set()
    )
    assert got is not None and got.user_id == 2


def test_unknown_sex_can_be_drawn_in_any_mode(tmp_store):
    """**硬规则**：性别未知的人在三个档下都能被抽到。"""
    from nonebot_plugin_waifu import rules

    for mode in ("normal", "all", "reverse"):
        pool = [rules.Candidate(user_id=2, last_seen=1.0, sex="unknown")]
        got = rules.pick_target(
            pool, me=1, my_sex="male", mode=mode, excluded=set(), married=set()
        )
        assert got is not None, f"mode={mode} 下性别未知者被静默排除了"


def test_orientation_config_three_modes_and_default(tmp_store):
    """`WAIFU_ORIENTATION` 三档、默认「所有」（用户 2026-10-10 拍板）。"""
    from nonebot_plugin_waifu.config import Config, plugin_config

    assert plugin_config.waifu_orientation in {"normal", "all", "reverse"}
    assert Config().waifu_orientation == "all"  # 默认 = 所有 = 不筛
    # 中文写法也必须能起（已在 test_rules.py 里逐档断言行为）


def test_marry_requires_same_group(tmp_store):
    """A 群结的婚不影响 B 群（群隔离）。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(group_id=100, requester=1, partner=2, now=0.0)
    handle.apply_marry(group_id=100, requester=2, partner=1, now=1.0)
    assert store.get_partner(100, 1) == 2
    assert store.get_partner(200, 1) is None
