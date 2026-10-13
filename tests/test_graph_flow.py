"""老婆关系图命令的收发流程：出图 / 文本回退 / 空状态 / @聚焦。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message
from nonebot.adapters.onebot.v11.event import Sender
import pytest

GROUP = 1094538078
A, B, C = 10001, 20002, 30003


def _event() -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=3128682634,
        post_type="message",
        sub_type="normal",
        user_id=A,
        message_id=0,
        message_type="group",
        message=[],
        raw_message="",
        font=0,
        sender=Sender(user_id=A, nickname="u", role="member"),
        group_id=GROUP,
    )


@pytest.fixture(autouse=True)
def _members():
    from nonebot_plugin_waifu import store

    for uid in (A, B, C):
        store.touch_member(group_id=GROUP, user_id=uid, nickname=f"n{uid}")


def _bot() -> MagicMock:
    bot = MagicMock()
    bot.send_group_msg = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_graph_command_sends_image(monkeypatch):
    from nonebot_plugin_waifu import handle, render, store

    store.marry(group_id=GROUP, a=A, b=B)
    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", AsyncMock(return_value=b"PNG"))
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    (msg,) = (bot.send_group_msg.await_args.kwargs["message"],)
    assert [s.type for s in msg] == ["image"]


@pytest.mark.asyncio
async def test_graph_command_falls_back_to_text(monkeypatch):
    from nonebot_plugin_waifu import handle, render, store

    store.marry(group_id=GROUP, a=A, b=B)
    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", AsyncMock(return_value=None))
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [s.type for s in msg] == ["text"]
    assert "夫妻" in msg[0].data["text"]


@pytest.mark.asyncio
async def test_graph_command_empty_state(monkeypatch):
    from nonebot_plugin_waifu import handle, render

    bot = _bot()
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=None))
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "text"]  # 回执：@ + 等价文本
    assert "还没有老婆关系" in msg[1].data["text"]


@pytest.mark.asyncio
async def test_graph_command_focus_without_relations(monkeypatch):
    from nonebot_plugin_waifu import handle, render, store

    store.marry(group_id=GROUP, a=A, b=B)  # C 无关系
    bot = _bot()
    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=None))
    await handle._h_graph(bot=bot, event=_event(), args=Message(" " + str(C)))
    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [seg.type for seg in msg] == ["at", "text"]
    assert "还没有任何老婆关系" in msg[1].data["text"]
