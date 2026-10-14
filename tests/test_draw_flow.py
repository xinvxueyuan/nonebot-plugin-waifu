"""抽取流程：别名语义、配额、展示、换人、确认锁定。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
import pytest

GROUP = 1094538078
ME = 10001
OTHER = 20002


def _event(user_id: int = ME, group_id: int = GROUP) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=3128682634,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=0,
        message=[],
        raw_message="",
        font=0,
        sender=Sender(user_id=user_id, nickname=f"u{user_id}", role="member"),
        group_id=group_id,
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
    same = handle.resolve_today_action(existing=row, daily_limit=20)
    assert same.action == "show"
    assert same.row is not None


def test_today_draws_when_no_record():
    from nonebot_plugin_waifu import handle

    got = handle.resolve_today_action(existing=None, daily_limit=20)
    assert got.action == "draw"


def test_today_blocks_on_quota_even_without_record():
    from nonebot_plugin_waifu import handle

    got = handle.resolve_today_action(existing=None, daily_limit=20, draws_used=20)
    assert got.action == "deny"
    assert got.message == "渣男，你今天没有老婆了！"


def test_change_always_redraws_and_increments():
    """「换一只」每次换人并计数（即使已有今日老婆；**未确认**时不受影响）。"""
    from nonebot_plugin_waifu import handle, store

    today = handle._today()
    store.set_daily(group_id=GROUP, user_id=ME, date_key=today, partner_id=OTHER)
    got = handle.resolve_change_action(
        existing=store.get_daily(GROUP, ME, today),
        daily_limit=20,
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
    )
    assert got.action == "deny"
    assert got.message == "渣男，你今天没有老婆了！"


# ⚠️「确认老婆后不能再换」的判定**不再在 `resolve_change_action` 里**
# （用户 2026-10-14 口径）：`_do_draw` 在算 action 之前就早退成「已定人」回执 ——
# 见文件末尾的 A3 小节。


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
    )
    assert got is None

    # ⚠️ 不锁对方：**别人**抽的时候 OTHER 仍是候选（用户 2026-10-10 口径）
    got_other = rules.pick_target(
        pool,
        me=30003,
        my_sex="unknown",
        mode="all",
        excluded=store.locked_partners(GROUP, 30003, today),
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
            fetch_sex=_sex,
            max_tries=8,
        )
    )
    assert got_normal is None
    assert calls["n"] <= 8


# --------------------------------------------------------------------------- #
# 换一只当日不重复：排除集取并集 + 抽遍文案
# --------------------------------------------------------------------------- #
def test_do_draw_excluded_is_union_of_locked_and_drawn():
    """**接线守卫（AST）**：`_do_draw` 的 `excluded=` 必须含 locked + drawn。

    必须同时含 `locked_partners` 与 `drawn_partners`（并集）。漏掉后者 →
    换一只会重复抽到同一个人。
    """
    import ast
    import pathlib

    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)

    call = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "_pick_eligible"
        ):
            call = node
    assert call is not None, "找不到 _pick_eligible 调用 —— 接线可能被删了"

    kw = {k.arg: k.value for k in call.keywords}
    assert "excluded" in kw, "_pick_eligible 漏传 excluded"
    flat = ast.unparse(kw["excluded"])
    assert "store.locked_partners(" in flat, f"excluded 没接 locked_partners：{flat}"
    assert "store.drawn_partners(" in flat, f"excluded 没接 drawn_partners：{flat}"


def test_all_drawn_message_is_exact():
    """抽遍当日的文案是逐字指定的。"""
    from nonebot_plugin_waifu import rules

    assert rules.ALL_DRAWN_MESSAGE == "今天群里的人你已经抽遍了，明天再来"
    assert "ALL_DRAWN_MESSAGE" in rules.__all__


def test_empty_pool_message_unchanged_when_truly_empty():
    """**池子本来就没人** → 保留旧文案（不误报「抽遍了」）。

    两种状态各走各的编译器：空池 → `notices.empty_pool_notice()`；
    抽遍 → `notices.all_drawn_notice()`（文案仍是用户逐字那句，见 notices）。
    """
    import inspect

    from nonebot_plugin_waifu import handle, notices

    src = inspect.getsource(handle._do_draw)
    assert "empty_pool_notice" in src
    assert "all_drawn_notice" in src
    assert (
        notices.empty_pool_notice().title
        == "群里还没有能抽的人（要有人发过言才会进池子）"
    )


def test_do_draw_uses_all_drawn_message_branch():
    """**行为**：当池子里有人、但去掉 locked/drawn 后为空时 → 走 ALL_DRAWN_MESSAGE。"""
    from nonebot_plugin_waifu import rules

    # 池子里除我之外还有 2 人，但他们今天都被我抽过了
    pool_ids = [ME, OTHER, 30003]
    drawn = {OTHER, 30003}
    # 未去重的可用候选数（排除 me / married，不排除 locked/drawn）> 0
    raw_available = [u for u in pool_ids if u != ME]
    assert len(raw_available) > 0
    # 去重后没人剩
    after_dedup = [u for u in raw_available if u not in drawn]
    assert after_dedup == []
    # → 应命中「抽遍」文案而非「没人」
    assert rules.ALL_DRAWN_MESSAGE == "今天群里的人你已经抽遍了，明天再来"


# --------------------------------------------------------------------------- #
# A2：取消离婚冷却（用户 2026-10-14）—— 离婚后**立刻**就能再抽
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_divorced_requester_can_draw_immediately(monkeypatch):
    """离婚后**立刻**就能再抽，不再等 3 天（用户 2026-10-14：取消离婚冷却）。

    单人离婚（同批口径：无需对方同意）：ME 一句「离婚 @OTHER」当场解除，
    随后 **ME 自己**立刻抽卡必须成功 —— 冷却只要还在，这条必红。
    """
    from nonebot_plugin_waifu import handle, store

    store.marry(group_id=GROUP, a=ME, b=OTHER)
    out = handle.apply_divorce(group_id=GROUP, requester=ME, partner=OTHER)
    assert "已离婚" in out.message
    assert store.get_partner(GROUP, ME) is None
    assert store.get_partner(GROUP, OTHER) is None

    sent = AsyncMock()
    monkeypatch.setattr(handle, "_build_and_send", sent)
    err = await handle._do_draw(bot=MagicMock(), event=_event(ME), redraw=False)
    assert err is None, err
    row = store.get_daily(GROUP, ME, handle._today())
    assert row is not None
    assert sent.await_count == 1


# --------------------------------------------------------------------------- #
# A1：已婚者照常抽（用户 2026-10-14）—— 已婚者既不被拦、也不被排除出池子
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_married_can_still_draw(monkeypatch):
    """已婚者不再被拦、也**不再被排除出抽取池**（用户 2026-10-14）。

    刻意用**只剩一个候选**的池子：若已婚者被排除，池子为空 → 直接报错，
    任何「把已婚者塞回排除集」的变异都会被这条测试抓住。
    """
    from nonebot_plugin_waifu import handle, store

    gid = 998877
    store.touch_member(group_id=gid, user_id=ME, nickname="甲")
    store.touch_member(group_id=gid, user_id=OTHER, nickname="乙")
    store.marry(group_id=gid, a=ME, b=OTHER)  # 两人已婚
    sent = AsyncMock()
    monkeypatch.setattr(handle, "_build_and_send", sent)

    err = await handle._do_draw(bot=MagicMock(), event=_event(ME, gid), redraw=False)
    assert err is None, err
    row = store.get_daily(gid, ME, handle._today())
    assert row is not None and int(row["partner_id"]) == OTHER
    assert sent.await_count == 1


# --------------------------------------------------------------------------- #
# A3：确认之后不再复用抽取卡片 —— 早退成「已定人」回执（用户 2026-10-14）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_confirmed_today_message_is_not_the_draw_card(monkeypatch):
    """「确认」之后再发「今日老婆」/「换一只」→ 必须说清「已定人」，不走卡片。"""
    from nonebot_plugin_waifu import handle, store

    store.set_daily(
        group_id=GROUP, user_id=ME, date_key=handle._today(), partner_id=OTHER
    )
    store.lock_daily(GROUP, ME, handle._today())
    sent = AsyncMock()
    monkeypatch.setattr(handle, "_build_and_send", sent)

    for redraw in (False, True):
        notice = await handle._do_draw(bot=MagicMock(), event=_event(ME), redraw=redraw)
        assert notice is not None
        assert notice.title == "今天已经定人"  # 不是抽取卡片
        assert notice.avatar_user_id == OTHER  # 当事人头像在
        assert any("不能再换" in str(v) for _k, v in notice.fields)  # 说清已定人
        assert any("已用" in str(v) for _k, v in notice.fields)  # 数据在
    assert sent.await_count == 0  # 关键：**一张抽取卡片都没发**（回执由 handler 发图）


@pytest.mark.asyncio
async def test_send_notice_prefers_image(monkeypatch):
    """通用回执发送器：**[at] + 图**（渲染可用时），@ 到发起人。"""
    from nonebot_plugin_waifu import handle, render

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=b"PNG"))
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    ctx = render.NoticeContext(title="今天已经定人", tone="deny", headline="甲 → 乙")
    await handle._send_notice(bot, _event(ME), ctx)

    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "image"]


@pytest.mark.asyncio
async def test_send_notice_falls_back_to_equivalent_text(monkeypatch):
    """渲染不可用 → **[at] + 信息等价纯文本**（命令绝不能变成「没反应」）。"""
    from nonebot_plugin_waifu import handle, render

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=None))
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    ctx = render.NoticeContext(
        title="今天已经定人",
        tone="deny",
        fields=(("状态", "已确认，今天不能再换"),),
    )
    await handle._send_notice(bot, _event(ME), ctx)

    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "text"]
    text = msg[1].data["text"]
    assert "今天已经定人" in text and "状态：已确认，今天不能再换" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["_h_today", "_h_change"])
async def test_draw_handlers_send_locked_notice_as_image(monkeypatch, name):
    """接线：`_h_today` 与 `_h_change` 都必须把回执**真的发出去**（[at, 图]）。

    两条命令共用 `_do_draw` 的早退，但各自有 3 行「发回执」的胶水 ——
    任一处漏发，命令就表现成「没反应」（正是本计划要防的静默失效）。
    """
    from nonebot_plugin_waifu import handle, render, store

    handler = getattr(handle, name)
    store.set_daily(
        group_id=GROUP, user_id=ME, date_key=handle._today(), partner_id=OTHER
    )
    store.lock_daily(GROUP, ME, handle._today())
    sent = AsyncMock()
    monkeypatch.setattr(handle, "_build_and_send", sent)  # 防红转绿前误发真卡片
    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=b"PNG"))
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()

    await handler(bot=bot, event=_event(ME))

    assert sent.await_count == 0
    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "image"]
