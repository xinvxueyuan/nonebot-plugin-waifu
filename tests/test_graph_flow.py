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


# --------------------------------------------------------------------------- #
# 「喜欢」接进关系图命令（用户 2026-10-14：抽取即喜欢，**无需**确认）
# --------------------------------------------------------------------------- #
DAY = "2026-10-14"


def _graph_text(bot: MagicMock) -> str:
    """收一条回执的纯文本（出图失败时的回退路径用）。"""
    msg = bot.send_group_msg.await_args.kwargs["message"]
    return "".join(seg.data["text"] for seg in msg if seg.type == "text")


def _fallback_bot(monkeypatch) -> MagicMock:
    """让关系图走「渲染失败 → 纯文本回退」路径的 bot。"""
    from nonebot_plugin_waifu import render

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", AsyncMock(return_value=None))
    return _bot()


@pytest.mark.asyncio
async def test_graph_command_wires_draws_as_like_edges(monkeypatch):
    """抽过卡（没「确认老婆」）→ 图里出现 **like 有向边**：src=抽取者，dst=被抽到的人。

    真数据入图：`store.set_daily`（生产「成功抽到一个人」的唯一落库入口）
    → 真命令 `_h_graph` → 渲染失败 → 纯文本回退里带「喜欢」标签与方向。
    """
    from nonebot_plugin_waifu import handle, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    bot = _fallback_bot(monkeypatch)
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    text = _graph_text(bot)
    assert any(line.startswith("喜欢：") for line in text.splitlines())
    assert f"n{A} → n{B}" in text  # src=抽取者 A，dst=被抽到的 B


@pytest.mark.asyncio
async def test_graph_command_draw_edge_reaches_the_rendered_html(monkeypatch):
    """真数据入图后，渲染层拿到的簇里「喜欢」边在：HTML 带标签 + 自己的蓝色。"""
    from nonebot_plugin_waifu import handle, render, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    captured = {}

    async def _fake_render_graph(clusters, *, nicknames, avatars=None, focus=None):
        captured["clusters"] = clusters
        captured["nicknames"] = nicknames
        return b"PNG"

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", _fake_render_graph)
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    assert [s.type for s in bot.send_group_msg.await_args.kwargs["message"]] == [
        "image"
    ]
    html = render.build_graph_html(
        captured["clusters"], nicknames=captured["nicknames"], avatars={}
    )
    assert "喜欢" in html and "#0ea5e9" in html


@pytest.mark.asyncio
async def test_graph_command_keeps_only_the_latest_draw_per_member(monkeypatch):
    """换一只：同一人先抽到 B、后抽到 C → 只留 **C** 的喜欢边。"""
    from nonebot_plugin_waifu import handle, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=C)  # 换一只
    bot = _fallback_bot(monkeypatch)
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    text = _graph_text(bot)
    assert f"n{A} → n{C}" in text
    assert f"n{A} → n{B}" not in text


@pytest.mark.asyncio
async def test_graph_command_crush_supersedes_like_for_the_same_direction(monkeypatch):
    """同一对象已「确认老婆」（倾心）→ 只画倾心，同方向不再重复画喜欢。"""
    from nonebot_plugin_waifu import handle, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    assert store.lock_daily(GROUP, A, DAY) is True  # 「确认老婆」→ 倾心
    bot = _fallback_bot(monkeypatch)
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    text = _graph_text(bot)
    assert f"倾心：n{A} → n{B}" in text
    assert not any(line.startswith("喜欢：") for line in text.splitlines())


@pytest.mark.asyncio
async def test_graph_command_marriage_supersedes_like_for_the_same_pair(monkeypatch):
    """同一对已是夫妻 → 只画夫妻（最强），喜欢不重复画。"""
    from nonebot_plugin_waifu import handle, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    store.marry(group_id=GROUP, a=A, b=B)
    bot = _fallback_bot(monkeypatch)
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    text = _graph_text(bot)
    assert f"夫妻：n{A} 与 n{B}" in text
    assert not any(line.startswith("喜欢：") for line in text.splitlines())


@pytest.mark.asyncio
async def test_graph_command_keeps_both_directions_of_mutual_likes(monkeypatch):
    """A 抽到 B、B 也抽到 A → 反向两条喜欢边**都保留**（有向边，不是夫妻一条）。"""
    from nonebot_plugin_waifu import handle, store

    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)
    store.set_daily(group_id=GROUP, user_id=B, date_key=DAY, partner_id=A)
    bot = _fallback_bot(monkeypatch)
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    text = _graph_text(bot)
    like_line = [line for line in text.splitlines() if line.startswith("喜欢：")]
    assert len(like_line) == 1
    assert f"n{A} → n{B}" in like_line[0]
    assert f"n{B} → n{A}" in like_line[0]
