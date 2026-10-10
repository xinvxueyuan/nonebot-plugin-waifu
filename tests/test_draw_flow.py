"""抽取流程：别名语义、配额、展示、换人、确认锁定。"""

from __future__ import annotations

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
import pytest

GROUP = 1094538078
ME = 10001
OTHER = 20002


def _event(user_id: int = ME) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=3128682634,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message=[],
        raw_message="",
        font=0,
        sender=Sender(user_id=user_id, nickname=f"u{user_id}", role="member"),
        group_id=GROUP,
    )


@pytest.fixture(autouse=True)
def _three_members():
    """池子里放 3 个人（都要在）。"""
    from nonebot_plugin_waifu import store

    for uid in (ME, OTHER, 30003):
        store.touch_member(group_id=GROUP, user_id=uid, nickname=f"n{uid}")


def test_today_uses_today_then_shows_existing():
    """**核心口径**：「今日老婆」当天首次抽，之后只展示（不换人、不计数）。"""
    from nonebot_plugin_waifu import handle, store

    today = handle._today()
    store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)

    row = store.get_daily(GROUP, ME, today)
    assert row["partner_id"] == OTHER
    assert row["draws_used"] == 1
    # 再执行一次「今日老婆」不应改变记录
    same = handle.resolve_today_action(
        existing=row, daily_limit=20, cd_until=None, now=1.0
    )
    assert same.action == "show"
    assert same.row is not None


def test_today_draws_when_no_record():
    from nonebot_plugin_waifu import handle

    got = handle.resolve_today_action(
        existing=None, daily_limit=20, cd_until=None, now=1.0
    )
    assert got.action == "draw"


def test_today_blocks_on_quota_even_without_record():
    from nonebot_plugin_waifu import handle

    got = handle.resolve_today_action(
        existing=None, daily_limit=20, cd_until=None, now=1.0, draws_used=20
    )
    assert got.action == "deny"
    assert got.message == "渣男，你今天没有老婆了！"


def test_today_blocks_on_divorce_cd():
    from nonebot_plugin_waifu import handle

    got = handle.resolve_today_action(
        existing=None, daily_limit=20, cd_until=500.0, now=100.0
    )
    assert got.action == "deny"
    assert got.reason == "cd"


def test_change_always_redraws_and_increments():
    """「换一只」每次换人并计数（即使已有今日老婆；**未确认**时不受影响）。"""
    from nonebot_plugin_waifu import handle, store

    today = handle._today()
    store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    got = handle.resolve_change_action(
        existing=store.get_daily(GROUP, ME, today),
        daily_limit=20,
        cd_until=None,
        now=1.0,
        locked_today=False,
    )
    assert got.action == "draw"


def test_change_blocked_at_quota():
    from nonebot_plugin_waifu import handle, store

    today = handle._today()
    # 用满 20 次
    for _ in range(20):
        store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    got = handle.resolve_change_action(
        existing=store.get_daily(GROUP, ME, today),
        daily_limit=20,
        cd_until=None,
        now=1.0,
        locked_today=False,
    )
    assert got.action == "deny"
    assert got.message == "渣男，你今天没有老婆了！"


def test_change_denied_when_confirmed_today():
    """**新口径（用户 2026-10-11 拍板）**：确认老婆后「换一只」被拒，不消耗次数。"""
    from nonebot_plugin_waifu import handle, rules, store

    today = handle._today()
    store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    store.lock_daily(GROUP, ME, today)
    got = handle.resolve_change_action(
        existing=store.get_daily(GROUP, ME, today),
        daily_limit=20,
        cd_until=None,
        now=1.0,
        locked_today=True,
    )
    assert got.action == "deny"
    assert got.reason == "confirmed"
    assert got.message == rules.CONFIRMED_MESSAGE
    assert got.message == "你今天已确认老婆，不能再换"


def test_change_confirmed_takes_priority_over_quota():
    """判定顺序守卫：**「已确认」优先于配额**（配额满时仍回「已确认」而非配额文案）。"""
    from nonebot_plugin_waifu import handle, rules, store

    today = handle._today()
    # 配额也用满 20 次 —— locked_today 仍必须抢先返回 confirmed
    for _ in range(20):
        store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    store.lock_daily(GROUP, ME, today)
    got = handle.resolve_change_action(
        existing=store.get_daily(GROUP, ME, today),
        daily_limit=20,
        cd_until=None,
        now=1.0,
        locked_today=True,
    )
    assert got.action == "deny"
    assert got.reason == "confirmed", "已确认必须优先于配额（判定顺序被破坏）"
    assert got.message == rules.CONFIRMED_MESSAGE


def test_draw_never_returns_locked_partner():
    """「确认老婆」后，**我自己**不会再抽到 ta（对方仍可被别人抽到）。"""
    from nonebot_plugin_waifu import handle, store

    today = handle._today()
    store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    store.lock_daily(GROUP, ME, today)

    # 只有 ME 与 OTHER 在池子里，OTHER 被我锁定 → 我这边无候选
    from nonebot_plugin_waifu import rules

    pool = [
        rules.Candidate(user_id=ME, last_seen=1.0),
        rules.Candidate(user_id=OTHER, last_seen=1.0),
    ]
    got = rules.pick_target(
        pool,
        me=ME,
        my_sex="unknown",
        mode="all",
        excluded=store.locked_partners(GROUP, ME, today),
        married=store.married_ids(GROUP),
    )
    assert got is None

    # ⚠️ 不锁对方：**别人**抽的时候 OTHER 仍是候选（用户 2026-10-10 口径）
    got_other = rules.pick_target(
        pool,
        me=30003,
        my_sex="unknown",
        mode="all",
        excluded=store.locked_partners(GROUP, 30003, today),
        married=store.married_ids(GROUP),
    )
    assert got_other is not None and got_other.user_id in {ME, OTHER}


def test_confirm_locks_and_returns_false_without_record():
    from nonebot_plugin_waifu import store

    assert store.lock_daily(GROUP, ME, "2020-01-01") is False


def test_pick_eligible_all_mode_skips_sex_and_normal_is_bounded():
    """`_pick_eligible`：**all 档零 API 调用**；normal 档**尝试有界**（不死循环）。"""
    import asyncio

    from nonebot_plugin_waifu import handle, rules

    pool = [
        rules.Candidate(user_id=ME, last_seen=1.0),
        rules.Candidate(user_id=OTHER, last_seen=1.0),
        rules.Candidate(user_id=30003, last_seen=1.0),
    ]
    calls = {"n": 0}

    async def _sex(user_id: int) -> str:
        calls["n"] += 1
        return "male"  # 候选全是男 → normal（我=male）全部不合格

    # all 档：一次 fetch_sex 都不调，直接给候选
    got_all = asyncio.run(
        handle._pick_eligible(
            pool=pool,
            me=ME,
            my_sex=None,
            mode="all",
            excluded=set(),
            married=set(),
            fetch_sex=_sex,
        )
    )
    assert got_all is not None and got_all.user_id != ME
    assert calls["n"] == 0

    # normal 档：取向不符时收敛到 None，且尝试次数有界（<= max_tries）
    got_normal = asyncio.run(
        handle._pick_eligible(
            pool=pool,
            me=ME,
            my_sex="male",
            mode="normal",
            excluded=set(),
            married=set(),
            fetch_sex=_sex,
            max_tries=8,
        )
    )
    assert got_normal is None
    assert calls["n"] <= 8
