"""「指婚」：超管白名单 + 字面绕过（无需确认 / 不看上限 / 不挡机器人）。"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender
import pytest

GROUP = 1094538078
BOSS = 1330509996  # 生产 WIFE_SUPERUSERS 里的那一个
ME = 10001
OTHER = 20002
THIRD = 30003


def _event(user_id: int = BOSS) -> GroupMessageEvent:
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
        group_id=GROUP,
    )


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.self_id = 3128682634
    bot.send_group_msg = AsyncMock()
    bot.call_api = AsyncMock(return_value=[])
    return bot


@pytest.fixture(autouse=True)
def _whitelist(monkeypatch):
    """白名单指向生产那一个 QQ（每个测试都显式给，别依赖 .env）。"""
    from nonebot_plugin_waifu import handle

    monkeypatch.setattr(handle.plugin_config, "wife_superusers", "1330509996")


# --------------------------------------------------------------------------- #
# 白名单解析 + Permission
# --------------------------------------------------------------------------- #
def test_parse_superusers_tolerates_formats():
    from nonebot_plugin_waifu import rules

    assert rules.parse_superusers("1330509996") == frozenset({1330509996})
    assert rules.parse_superusers("1330509996, 2846018938") == frozenset({
        1330509996,
        2846018938,
    })
    assert rules.parse_superusers('["1330509996"]') == frozenset({1330509996})
    assert rules.parse_superusers("") == frozenset()
    assert rules.parse_superusers("写错了") == frozenset()  # 不抛：只是白名单里没人


def test_arrange_permission_checks_the_whitelist():
    """**只有白名单能过**（Permission 在响应器层拦人）。"""
    from nonebot_plugin_waifu import handle

    assert asyncio.run(handle._is_wife_superuser(_event(BOSS))) is True
    assert asyncio.run(handle._is_wife_superuser(_event(ME))) is False


def test_arrange_command_is_registered_with_the_permission():
    from nonebot_plugin_waifu import handle

    assert handle.CMD_ARRANGE == "指婚"
    assert handle.CMD_ARRANGE in handle.CMD_NAMES
    assert handle.arrange_cmd.permission is not None


# --------------------------------------------------------------------------- #
# apply_arrange：字面绕过
# --------------------------------------------------------------------------- #
def test_arrange_binds_head_with_every_target():
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_arrange(group_id=GROUP, head=ME, partners=[OTHER, THIRD])
    assert out.bound is True
    assert out.error == ""
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}
    assert store.get_partner(GROUP, OTHER) == ME  # 对方那侧也当已婚（双向两行）
    assert [uid for uid, _ in out.lines] == [OTHER, THIRD]


def test_arrange_skips_self_and_dedupes():
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_arrange(
        group_id=GROUP, head=ME, partners=[ME, OTHER, OTHER, THIRD]
    )
    assert [uid for uid, _ in out.lines] == [OTHER, THIRD]
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}


def test_arrange_without_other_people_is_an_error():
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_arrange(group_id=GROUP, head=ME, partners=[ME])
    assert out.bound is False
    assert out.error
    assert store.list_partners(GROUP, ME) == []


def test_arrange_bypasses_the_partner_cap():
    """单配（`WAIFU_MAX_PARTNERS=1`）下也能「a 娶 b 和 c」：指婚绕过上限。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(
        group_id=GROUP, requester=ME, partner=OTHER, now=1.0, max_partners=1
    )
    handle.apply_marry(
        group_id=GROUP, requester=OTHER, partner=ME, now=2.0, max_partners=1
    )
    assert store.count_partners(GROUP, ME) == 1
    blocked = handle.apply_marry(
        group_id=GROUP, requester=ME, partner=THIRD, now=3.0, max_partners=1
    )
    assert blocked.error and store.count_partners(GROUP, ME) == 1  # 普通路径被上限挡住

    out = handle.apply_arrange(group_id=GROUP, head=ME, partners=[THIRD])
    assert out.bound is True
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}


def test_arrange_binds_even_if_partner_is_already_married():
    """字面绕过：对方已婚也照样绑（「视为 b、c 同意」）。"""
    from nonebot_plugin_waifu import handle, store

    handle.apply_marry(
        group_id=GROUP, requester=OTHER, partner=THIRD, now=1.0, max_partners=1
    )
    handle.apply_marry(
        group_id=GROUP, requester=THIRD, partner=OTHER, now=2.0, max_partners=1
    )

    out = handle.apply_arrange(group_id=GROUP, head=ME, partners=[THIRD])
    assert out.bound is True
    assert ME in store.list_partners(GROUP, THIRD)


def test_arrange_clears_a_stale_pending():
    """旧求婚请求当场作废（否则 ta 之后回发「娶老婆 @我」语义矛盾）。"""
    from nonebot_plugin_waifu import handle, store

    store.set_pending(
        group_id=GROUP, requester=ME, partner=OTHER, kind="marry", requested_at=1.0
    )
    assert (
        store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry") is not None
    )

    handle.apply_arrange(group_id=GROUP, head=ME, partners=[OTHER])
    assert store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry") is None


# --------------------------------------------------------------------------- #
# 回执 + 元数据
# --------------------------------------------------------------------------- #
def test_arrange_notice_tones_and_rows():
    from nonebot_plugin_waifu import notices

    ok = notices.arrange_notice(
        head_name="甲", head_id=ME, results=((OTHER, "已绑定"),)
    )
    assert ok.tone == "ok"
    assert "指婚" in ok.title
    assert [it.user_id for it in ok.items] == [OTHER]

    bad = notices.arrange_notice(
        head_name="甲", head_id=ME, results=(), failed="指婚至少要 @ 两个人"
    )
    assert bad.tone == "deny"
    assert bad.headline == "指婚至少要 @ 两个人"


def test_meta_advertises_arrange_for_the_info_plugin():
    """Info 类插件读 `__plugin_meta__`：指婚必须出现在描述/用法里。"""
    from nonebot_plugin_waifu import __plugin_meta__

    assert "指婚" in __plugin_meta__.description
    assert any("指婚" in line for line in __plugin_meta__.usage.splitlines())


@pytest.mark.asyncio
async def test_h_arrange_usage_reply_is_an_image(monkeypatch):
    from nonebot_plugin_waifu import handle, render

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=b"PNG"))
    bot = _bot()
    await handle._h_arrange(bot=bot, event=_event(), args=Message())
    ctx = render.render_notice.await_args.args[0]
    assert ctx.title == "「指婚」怎么用"
    assert bot.send_group_msg.await_args.kwargs["message"][0].data["qq"] == str(BOSS)


@pytest.mark.asyncio
async def test_h_arrange_binds_and_replies_with_one_image(monkeypatch):
    from nonebot_plugin_waifu import handle, render, store

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=b"PNG"))
    bot = _bot()
    # 第一位 @ 的是**发起人**（a），其余是对象 —— 「a 娶 b、c」的字面顺序
    args = Message([
        MessageSegment.at(ME),
        MessageSegment.at(OTHER),
        MessageSegment.at(THIRD),
    ])
    await handle._h_arrange(bot=bot, event=_event(), args=args)

    ctx = render.render_notice.await_args.args[0]
    assert ctx.title == "指婚完成"
    assert [it.user_id for it in ctx.items] == [OTHER, THIRD]
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}
    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "image"]
