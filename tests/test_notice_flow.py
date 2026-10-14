"""回执图片化流程（Task 8A）：**每条命令的回复都是「@ + 图」**。

用户口径：此插件其他地方也都用图片代替纯文本 —— 纯文本回执在群聊里会被后续
消息冲掉，等于没回。所以这里**逐条命令**断言「真的发出了图」，并单列一条：
渲染不可用时回退**信息等价**文本（命令绝不能表现成「没反应」）。

替身策略：只换掉**渲染层**（`render.render_notice` / `render.fetch_avatars`）与
bot 的发送动作；业务判定、纯函数、消息组装全部跑真代码。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
import pytest

GROUP = 1094538078
ME = 10001
OTHER = 20002
THIRD = 30003


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


def _bot() -> MagicMock:
    """假 bot：只实现本流程真的会碰的三个动作。"""
    bot = MagicMock()
    bot.self_id = 3128682634
    bot.send_group_msg = AsyncMock()
    bot.call_api = AsyncMock(return_value=[])  # 机器人区间：走内置兜底，不打网络
    return bot


@pytest.fixture(autouse=True)
def _members():
    """池子里放 3 个人（都要在，否则抽取路径直接落到空状态回执）。"""
    from nonebot_plugin_waifu import store

    for uid in (ME, OTHER, THIRD):
        store.touch_member(group_id=GROUP, user_id=uid, nickname=f"n{uid}")


@pytest.fixture(autouse=True)
def _fake_render(monkeypatch):
    """渲染层换成替身：出图成功、头像不取（真 htmlkit 可能没装）。"""
    from nonebot_plugin_waifu import render

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=b"PNG"))
    return render


def _message(bot: MagicMock) -> Message:
    """取这次发送的消息体（send_group_msg 只允许被调用一次）。"""
    assert bot.send_group_msg.await_count == 1, "应当恰好发一条回执"
    return bot.send_group_msg.await_args.kwargs["message"]


def _assert_image_reply(bot: MagicMock) -> Message:
    """回执必须是 `[@发起人, 图]` —— **不是纯文本**。"""
    msg = _message(bot)
    assert [seg.type for seg in msg] == ["at", "image"], f"回执不是图：{msg!r}"
    assert msg[0].data["qq"] == str(ME)  # @ 到命令发起人
    assert str(msg[1].data["file"]).startswith("base64://")  # 真图字节，不是链接
    return msg


def _ctx_of() -> Any:
    """取交给渲染层的回执上下文（断言「图里到底有什么」）。

    fixture 已把 `render.render_notice` 换成替身，所以这里拿到的是本次调用的入参
    （**不能**在测试体里直接引用 fixture 名 —— 那是函数对象，不是返回值）。
    """
    from nonebot_plugin_waifu import render

    return render.render_notice.await_args.args[0]


# --------------------------------------------------------------------------- #
# 配额：唯一能走到配额回执的命令是「换一只」（今日已抽过 → 展示卡片 → 换人才计次）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_change_quota_reply_is_an_image():
    from nonebot_plugin_waifu import handle, rules, store

    for _ in range(20):
        store.set_daily(
            group_id=GROUP, user_id=ME, date_key=handle._today(), partner_id=OTHER
        )
    bot = _bot()
    await handle._h_change(bot=bot, event=_event())

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == rules.QUOTA_MESSAGE  # 用户逐字指定，且必须是回执标题
    assert ctx.tone == "warn"


# --------------------------------------------------------------------------- #
# 确认老婆：成功 = 资料密集回执；无当日记录 = 用法回执（都不是纯文本）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_confirm_reply_is_an_image_with_partner_profile():
    from nonebot_plugin_waifu import handle, store

    store.set_daily(
        group_id=GROUP, user_id=ME, date_key=handle._today(), partner_id=OTHER
    )
    bot = _bot()
    await handle._h_confirm(bot=bot, event=_event())

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == "已确认今日老婆"
    assert ctx.tone == "ok"
    assert ctx.avatar_user_id == OTHER  # 当事人头像在
    keys = {k for k, _v in ctx.fields}
    assert {"昵称", "QQ号", "最后发言", "状态", "影响范围", "今日抽取"} <= keys
    assert store.locked_partners(GROUP, ME, handle._today()) == {OTHER}


@pytest.mark.asyncio
async def test_confirm_without_record_reply_is_an_image():
    """没抽就确认 → 用法回执（含具体步骤），**不建锁**。"""
    from nonebot_plugin_waifu import handle, store

    bot = _bot()
    await handle._h_confirm(bot=bot, event=_event())

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == f"「{handle.CMD_TODAY}」怎么用"
    assert len(ctx.fields) >= 2  # 步骤一条一行，不是一句话
    assert not store.has_lock(GROUP, ME, handle._today())


# --------------------------------------------------------------------------- #
# 娶老婆：用法 / 结果 / 失败 —— 三条路径都要出图
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_marry_usage_reply_is_an_image():
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_marry(bot=bot, event=_event(), args=Message())

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == f"「{handle.CMD_MARRY}」怎么用"
    assert any("@某人" in str(v) for _k, v in ctx.fields)  # 可照做的步骤


@pytest.mark.asyncio
async def test_marry_reply_is_an_image_and_lists_the_target():
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_marry(bot=bot, event=_event(), args=Message(f" {OTHER}"))

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == handle.CMD_MARRY
    assert [it.user_id for it in ctx.items] == [OTHER]  # 名单里有目标本人
    assert ctx.items[0].line.strip()


@pytest.mark.asyncio
async def test_marry_multi_target_reply_is_one_image_with_everyone():
    """一次 @ 多人（高于上限 → 整条拒绝）也必须**一张图里每个人都有一行**。"""
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_marry(bot=bot, event=_event(), args=Message(f" {OTHER} {THIRD}"))

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == "娶老婆没成功"  # 默认上限 1，两个目标 → 整条拒绝
    assert [it.user_id for it in ctx.items] == [OTHER, THIRD]
    assert all(it.line.strip() for it in ctx.items)


@pytest.mark.asyncio
async def test_marry_failure_reply_is_an_image():
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_marry(bot=bot, event=_event(), args=Message(f" {ME}"))

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == "娶老婆没成功"
    assert ctx.tone == "deny"
    assert "不能和自己" in ctx.headline  # 失败原因在图上，不许丢


# --------------------------------------------------------------------------- #
# 离婚：用法 / 一发即离 / 失败（用户 2026-10-14：无需对方同意，单人即时生效）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_divorce_usage_reply_is_an_image():
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_divorce(bot=bot, event=_event(), args=Message())

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == f"「{handle.CMD_DIVORCE}」怎么用"
    assert any("@某人" in str(v) for _k, v in ctx.fields)
    assert any("无需对方同意" in str(v) for _k, v in ctx.fields)


@pytest.mark.asyncio
async def test_divorce_is_effective_on_single_request():
    """**一发即离**：单条命令（对方全程没发任何消息）就出「已离婚」图并当场解除。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(group_id=GROUP, requester=ME, partner=OTHER, now=100.0)
    handle.apply_marry(group_id=GROUP, requester=OTHER, partner=ME, now=101.0)
    assert store.get_partner(GROUP, ME) == OTHER

    bot = _bot()
    await handle._h_divorce(bot=bot, event=_event(), args=Message(f" {OTHER}"))

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == "已离婚"
    assert ctx.tone == "ok"
    assert [it.user_id for it in ctx.items] == [OTHER]
    # 当场生效 + 不留 divorce pending（OLD 双向确认会先回「申请已登记」）
    assert store.get_partner(GROUP, ME) is None
    assert store.get_partner(GROUP, OTHER) is None
    assert store.get_pending(GROUP, requester=ME, partner=OTHER, kind="divorce") is None


@pytest.mark.asyncio
async def test_divorce_failure_reply_is_an_image():
    from nonebot_plugin_waifu import handle

    bot = _bot()
    await handle._h_divorce(bot=bot, event=_event(), args=Message(f" {OTHER}"))

    _assert_image_reply(bot)
    ctx = _ctx_of()
    assert ctx.title == "离婚没成功"
    assert ctx.tone == "deny"
    assert ctx.headline.strip()  # 失败原因在图上


# --------------------------------------------------------------------------- #
# 兜底：渲染不可用 → **信息等价**的纯文本（绝不允许「没反应」）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_notice_falls_back_to_information_equivalent_text(monkeypatch):
    from nonebot_plugin_waifu import handle, render, store

    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=None))
    store.set_daily(
        group_id=GROUP, user_id=ME, date_key=handle._today(), partner_id=OTHER
    )
    bot = _bot()
    await handle._h_confirm(bot=bot, event=_event())

    msg = _message(bot)
    assert [seg.type for seg in msg] == ["at", "text"]
    text = msg[1].data["text"]
    for needle in ("已确认今日老婆", "QQ号", str(OTHER), "已定人"):
        assert needle in text, f"兜底文本缺内容：{needle}"
