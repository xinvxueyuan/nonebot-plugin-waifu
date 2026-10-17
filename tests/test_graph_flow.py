"""老婆关系图命令的收发流程：出图 / 文本回退 / 空状态 / @聚焦。"""

from __future__ import annotations

import threading
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

    async def _fake_render_graph(
        clusters, *, nicknames, avatars=None, focus=None, missing=()
    ):
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


# --------------------------------------------------------------------------- #
# 用户 2026-10-17：「老婆关系图 @A @B」——**允许 @ 多个**，出一张合并图
#   （每个人的 1 度关系都画上；有人没关系的只注明；全都没关系才回提示卡）
# --------------------------------------------------------------------------- #
def _multi(**kw) -> Message:
    """构造 `@A @B ...` 的命令参数（用 MessageSegment.at）。"""
    from nonebot.adapters.onebot.v11 import MessageSegment

    msg = Message()
    for uid in kw["ats"]:
        msg += MessageSegment.at(uid)
    return msg


@pytest.mark.asyncio
async def test_graph_command_merges_multiple_at_into_one_image(monkeypatch):
    """真数据：`@A @B` → **一张**图；两段关系（A→B 的喜欢 + C→A 的喜欢）都在簇里。"""
    from nonebot_plugin_waifu import handle, render, store

    d = 40004  # 与 A 毫无关系的第四人：这样「只画第一个中心」的变异体才藏不住
    store.touch_member(group_id=GROUP, user_id=d, nickname=f"n{d}")
    store.set_daily(group_id=GROUP, user_id=A, date_key=DAY, partner_id=B)  # A 喜欢 B
    store.set_daily(group_id=GROUP, user_id=C, date_key=DAY, partner_id=d)  # C 喜欢 d

    captured: dict = {}

    async def _fake(clusters, *, nicknames, avatars=None, focus=None, missing=()):
        captured["clusters"] = clusters
        captured["focus"] = focus
        captured["missing"] = missing
        return b"PNG"

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", _fake)
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=_multi(ats=(A, C)))

    assert [s.type for s in bot.send_group_msg.await_args.kwargs["message"]] == [
        "image"
    ]
    uids = {n.uid for c in captured["clusters"] for n in c.nodes}
    assert uids == {A, B, C, d}, f"合并图没把两个人的关系都画上：{uids}"
    assert captured["focus"] == (A, C)
    assert captured["missing"] == ()
    html = render.build_graph_html(
        captured["clusters"],
        nicknames={"n": ""},
        avatars={},
        focus=(A, C),
        missing=(),
    )
    assert "以「" in html and "」为中心" in html  # 副标题写多中心


@pytest.mark.asyncio
async def test_graph_command_multi_notes_the_ones_without_relations(monkeypatch):
    """`@A @C`（C 没有任何关系）→ 照画 A 的那部分，页脚注明「C 还没有任何关系」。"""
    from nonebot_plugin_waifu import handle, render, store

    store.marry(group_id=GROUP, a=A, b=B)  # 只有 A-B 有关系；C 没有
    captured: dict = {}

    async def _fake(clusters, *, nicknames, avatars=None, focus=None, missing=()):
        captured["missing"] = missing
        captured["clusters"] = clusters
        return b"PNG"

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", _fake)
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=_multi(ats=(A, C)))

    assert [s.type for s in bot.send_group_msg.await_args.kwargs["message"]] == [
        "image"
    ]
    assert captured["missing"] == (f"n{C}",)  # 页脚那条注记带着 ta 的名字
    html = render.build_graph_html(
        captured["clusters"],
        nicknames={C: f"n{C}"},
        avatars={},
        missing=captured["missing"],
    )
    assert f"n{C} 还没有任何关系" in html


@pytest.mark.asyncio
async def test_graph_command_multi_all_without_relations_falls_back_to_notice(
    monkeypatch,
):
    """`@C @D`（全都没关系）→ 不画图，回「这些人还没有任何老婆关系」的提示卡。"""
    from nonebot_plugin_waifu import handle, render, store

    store.marry(group_id=GROUP, a=A, b=B)  # C 与 D 都没有关系
    d = 40004
    store.touch_member(group_id=GROUP, user_id=d, nickname=f"n{d}")
    bot = _bot()
    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_notice", AsyncMock(return_value=None))
    await handle._h_graph(bot=bot, event=_event(), args=_multi(ats=(C, d)))

    msg = bot.send_group_msg.await_args.kwargs["message"]
    assert [s.type for s in msg] == ["at", "text"]
    assert "还没有任何老婆关系" in msg[1].data["text"]


# --------------------------------------------------------------------------- #
# 关系图落点（v0.8.0，契约）：读缓存 → 未命中短预算求解（to_thread）→ 落库 →
# 后台满预算精修（(群, 指纹) 单飞）→ `layout.layout_graph(assignments=...)`。
#
# 用户 2026-10-17 口径：A–B–C 近似共线时 A–C 的连线必然压 B —— 这是「节点摆在
# 哪儿」的问题，所以要**重排落点**（不是画线）；解按「群 + 图指纹」缓存下来。
# --------------------------------------------------------------------------- #
SOLVE_CALLS: list[dict] = []


@pytest.fixture(autouse=True)
def _fast_solver(monkeypatch):
    """把 `slots.solve` 换成**快而确定**的替身（本文件每条用例都生效）。

    为什么必须换：真求解是 8~90s 预算的模拟退火（纯计算，A 侧有自己的单测），
    在这里的命令级用例里真跑一遍毫无意义，只会让整套测试慢到不可用。
    替身：**返回 members 的反转**（长度一致、是合法置换），且**长短预算返回同一个解**
    —— 断言就不会被「后台精修覆盖落库」这一步搅乱（专测精修的用例自己另装替身）。

    同时把后台精修的任务登记表清干净：它是模块级全局，不清会把上一轮的
    「同名任务在跑」带进下一条用例（单飞判定会误跳过）。
    """
    from nonebot_plugin_waifu import handle, slots

    SOLVE_CALLS.clear()
    for task in tuple(handle._refine_tasks.values()):
        task.cancel()
    handle._refine_tasks.clear()

    def _fake(slot_xy, *, members, hub, edges, budget_s, seed, gap_min=16.0):
        SOLVE_CALLS.append({
            "members": tuple(members),
            "edges": tuple(edges),
            "hub": hub,
            "budget_s": float(budget_s),
            "seed": seed,
            "n_slots": len(tuple(slot_xy)),
            "gap_min": gap_min,
            # 「跑在事件循环里吗」——`to_thread` 必须让它为 False
            "in_loop": threading.current_thread() is threading.main_thread(),
        })
        return tuple(reversed(tuple(members)))

    monkeypatch.setattr(slots, "solve", _fake)
    yield
    for task in tuple(handle._refine_tasks.values()):
        task.cancel()
    handle._refine_tasks.clear()


def _short_budget() -> float:
    from nonebot_plugin_waifu.config import plugin_config

    return float(plugin_config.waifu_layout_solve_budget)


def _refine_budget() -> float:
    from nonebot_plugin_waifu.config import plugin_config

    return float(plugin_config.waifu_layout_refine_budget)


def _short_calls() -> list[dict]:
    return [c for c in SOLVE_CALLS if c["budget_s"] == _short_budget()]


def _refine_calls() -> list[dict]:
    return [c for c in SOLVE_CALLS if c["budget_s"] == _refine_budget()]


def _chain() -> None:
    """A–B–C 链（B 是度最大的 hub）：3 人一簇，槽位重排能**明显**改坐标。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=GROUP, a=A, b=B)
    store.marry(group_id=GROUP, a=B, b=C)


def _view(*, targets: tuple[int, ...] = ()):
    """复刻 `_h_graph` 入图那一步（同一份库、同一参数）→ `(view, focus)`。

    拿它算「历史落点序」的对照布局；`focus` 与 `_h_graph` 的传法一致。
    """
    from nonebot_plugin_waifu import relations, store
    from nonebot_plugin_waifu.config import plugin_config

    g = relations.build_relations(
        marriages=store.list_marriages(GROUP),
        crushes=store.latest_locked_partners(GROUP),
        likes=store.latest_drawn_partners(GROUP),
        max_edges=plugin_config.waifu_graph_max_edges,
    )
    if not targets:
        return g, None
    if len(targets) == 1:
        return g.ego(targets[0]), targets[0]
    return g.ego_many(targets), tuple(targets)


def _spectator(monkeypatch, runs: list):
    """把出图收下：`runs` 里按顺序记下每次渲染拿到的 clusters。"""
    from nonebot_plugin_waifu import render

    async def _fake(clusters, *, nicknames, avatars=None, focus=None, missing=()):
        runs.append(clusters)
        return b"PNG"

    monkeypatch.setattr(render, "fetch_avatars", AsyncMock(return_value={}))
    monkeypatch.setattr(render, "render_graph", _fake)


def _positions(clusters) -> dict[int, tuple[int, int]]:
    return {n.uid: (n.x, n.y) for c in clusters for n in c.nodes}


async def _drain_refines() -> None:
    """等后台精修跑完（用例收尾必须先收干净，否则写库会跨到下一轮）。"""
    from nonebot_plugin_waifu import handle

    for task in tuple(handle._refine_tasks.values()):
        await task


async def _until(predicate, *, tries: int = 200) -> None:
    """等一个**线程里**跑出来的条件成立（替身在线程池里，必须让出事件循环）。"""
    import asyncio

    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等不到条件成立")


def _sig_of(spec) -> str:
    from nonebot_plugin_waifu import slots

    return slots.signature_of(spec.members, spec.edges)


@pytest.mark.asyncio
async def test_graph_first_render_solves_and_stores_the_solution(monkeypatch):
    """首次出图：**调一次短预算求解**（线程里跑）→ 解落库（键 = 簇的图指纹）。"""
    from nonebot_plugin_waifu import handle, layout, store

    _chain()
    _spectator(monkeypatch, [])
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    assert _short_calls(), "首次出图没有落点求解"
    assert len(_short_calls()) == 1, "A–B–C 是一个簇 → 短预算只该解一次"
    call = _short_calls()[0]
    assert call["n_slots"] == len(call["members"]), "槽位数必须等于成员数"
    assert call["hub"] == B, "hub 应是度最大的 B（连线中心）"
    assert call["seed"] == 20261017, "种子必须来自配置"
    assert not call["in_loop"], "求解跑在事件循环里 —— 8~90s 的同步计算会冻住整个 bot"

    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    assert store.get_layout_solution(group_id=GROUP, sig=_sig_of(spec)) == tuple(
        reversed(spec.members)
    ), "求解结果没有落库"

    # 后台精修用的是**满预算**（并覆盖落库；替身长短预算同一个解 → 值不变）
    assert _refine_calls(), "没有派后台精修"
    assert _refine_calls()[0]["budget_s"] == _refine_budget()


@pytest.mark.asyncio
async def test_graph_relation_change_invalidates_the_cache(monkeypatch):
    """指纹**含边**：成员没变、只是多了一条关系 → 也要重新求解（不许拿旧解画新图）。"""
    from nonebot_plugin_waifu import handle, store

    _chain()
    _spectator(monkeypatch, [])
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()
    assert len(_short_calls()) == 1

    store.marry(group_id=GROUP, a=A, b=C)  # 同一个三人簇，多一条边（成员没变）
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    assert len(_short_calls()) == 2, "关系变了还拿旧指纹的那份解去画新图"


@pytest.mark.asyncio
async def test_graph_uses_the_cached_solution_without_any_solve(monkeypatch):
    """缓存里**已经有**解 → 不求解、直接按它摆（这就是「第二次起」的那条路）。"""
    from nonebot_plugin_waifu import handle, layout, store

    _chain()
    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    cand = (spec.members[-1], *spec.members[1:-1], spec.members[0])  # 首尾互换
    store.save_layout_solution(
        group_id=GROUP,
        sig=_sig_of(spec),
        uids=cand,
        cost=-1.0,
        budget_s=8.0,
    )

    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    assert SOLVE_CALLS == [], "缓存命中还在求解"
    assert _positions(runs[0]) == _positions(
        layout.layout_graph(view, focus=None, assignments={_sig_of(spec): cand})
    ), "缓存里的解没有被用上"


@pytest.mark.asyncio
async def test_graph_focus_path_also_goes_through_the_cache(monkeypatch):
    """`@某人`（焦点簇）走的是同一条落点路径：求解 → 落库 → 照常出图。"""
    from nonebot_plugin_waifu import handle, layout, store

    _chain()
    runs: list = []
    _spectator(monkeypatch, runs)
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=Message(" " + str(C)))
    await _drain_refines()

    assert [s.type for s in bot.send_group_msg.await_args.kwargs["message"]] == [
        "image"
    ]
    assert len(_short_calls()) == 1, "焦点簇也要解一次"
    view, _focus = _view(targets=(C,))
    (spec,) = layout.cluster_specs(view)
    assert store.get_layout_solution(group_id=GROUP, sig=_sig_of(spec)) == tuple(
        reversed(spec.members)
    )


@pytest.mark.asyncio
async def test_graph_second_render_hits_the_cache(monkeypatch):
    """第二次出图**命中缓存**：短预算求解全程只被调一次，两次摆出的坐标一致。"""
    from nonebot_plugin_waifu import handle

    _chain()
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())

    assert len(_short_calls()) == 1, f"第二次出图又解了一遍：{_short_calls()}"
    assert len(runs) == 2
    assert _positions(runs[0]) == _positions(runs[1]), "同一份解应摆出同一张图"


@pytest.mark.asyncio
async def test_graph_solve_switch_off_keeps_yesterday_behaviour(monkeypatch):
    """`WAIFU_LAYOUT_SOLVE=false` → 不求解、也不用缓存（= 今天的历史落点序）。"""
    from nonebot_plugin_waifu import handle, layout, store
    from nonebot_plugin_waifu.config import plugin_config

    _chain()
    view, focus = _view()
    (spec,) = layout.cluster_specs(view)
    # 库里先塞一个**明显不同**的解：开关关掉时【不许】用它
    store.save_layout_solution(
        group_id=GROUP,
        sig=_sig_of(spec),
        uids=tuple(reversed(spec.members)),
        cost=-1.0,
        budget_s=8.0,
    )

    monkeypatch.setattr(plugin_config, "waifu_layout_solve", False)
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())

    assert SOLVE_CALLS == [], "开关关掉了还在求解"
    assert _positions(runs[0]) == _positions(layout.layout_graph(view, focus=focus))


@pytest.mark.asyncio
async def test_graph_actually_uses_the_solution(monkeypatch):
    """解**真的被用上**：打开开关后的落点 ≠ 历史落点（hub 不动、环上的两人换位）。"""
    from nonebot_plugin_waifu import handle, layout

    _chain()
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()
    solved = _positions(runs[0])

    view, focus = _view()
    history = _positions(layout.layout_graph(view, focus=focus))

    assert solved != history, "assignments 被吞了：落点与历史序逐像素一样"
    assert solved[B] == history[B], "hub 不该动（它在簇中心）"
    assert solved[A] != history[A], "环上的 A 没有按解挪到另一个槽位"


@pytest.mark.asyncio
async def test_graph_survives_a_broken_solver(monkeypatch):
    """求解**抛异常** → 照常出图（回退历史落点序），且不往缓存里写脏东西。"""
    from nonebot_plugin_waifu import handle, layout, slots, store

    _chain()

    def _boom(*_args, **_kw):
        raise RuntimeError("求解器炸了")

    monkeypatch.setattr(slots, "solve", _boom)
    runs: list = []
    _spectator(monkeypatch, runs)
    bot = _bot()
    await handle._h_graph(bot=bot, event=_event(), args=Message())
    await _drain_refines()

    assert [s.type for s in bot.send_group_msg.await_args.kwargs["message"]] == [
        "image"
    ], "求解失败把出图命令也带崩了"
    view, focus = _view()
    assert _positions(runs[0]) == _positions(layout.layout_graph(view, focus=focus))
    (spec,) = layout.cluster_specs(view)
    assert store.get_layout_solution(group_id=GROUP, sig=_sig_of(spec)) is None


@pytest.mark.asyncio
async def test_graph_ignores_a_non_permutation_solution(monkeypatch):
    """求解器回了**不是成员置换**的东西（丢人 / 重复）→ 该簇退回历史序、也不落库。"""
    from nonebot_plugin_waifu import handle, layout, slots, store

    _chain()
    monkeypatch.setattr(slots, "solve", lambda *_a, **_kw: (A, A, B))
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    view, focus = _view()
    assert _positions(runs[0]) == _positions(layout.layout_graph(view, focus=focus))
    (spec,) = layout.cluster_specs(view)
    assert store.get_layout_solution(group_id=GROUP, sig=_sig_of(spec)) is None


@pytest.mark.asyncio
async def test_graph_refine_is_single_flight_and_overwrites_the_cache(monkeypatch):
    """后台精修：**满预算**重解 → 覆盖落库；同一张图连发几次只跑一个（单飞）。"""
    import threading

    from nonebot_plugin_waifu import handle, layout, slots, store

    _chain()
    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    sig = _sig_of(spec)
    gate = threading.Event()

    def _fake(slot_xy, *, members, hub, edges, budget_s, seed, gap_min=16.0):
        SOLVE_CALLS.append({
            "members": tuple(members),
            "edges": tuple(edges),
            "hub": hub,
            "budget_s": float(budget_s),
            "in_loop": threading.current_thread() is threading.main_thread(),
        })
        if float(budget_s) == _refine_budget():
            assert gate.wait(timeout=10.0), "精修替身没等到开闸"
            # 精修解与短预算解【不同】：hub 挪到最前面（证明覆盖落库真的换了值）
            mem = tuple(members)
            return (mem[1], *(u for u in mem if u != mem[1]))
        return tuple(reversed(tuple(members)))

    monkeypatch.setattr(slots, "solve", _fake)
    _spectator(monkeypatch, [])
    try:
        await handle._h_graph(bot=_bot(), event=_event(), args=Message())
        await _until(lambda: len(_refine_calls()) == 1)
        assert len(handle._refine_tasks) == 1
        # 精修还堵着：再发两次，不许再派精修（(群, 指纹) 单飞）
        await handle._h_graph(bot=_bot(), event=_event(), args=Message())
        await handle._h_graph(bot=_bot(), event=_event(), args=Message())
        assert len(_refine_calls()) == 1, "同一张图叠出了多个精修任务"
        assert len(handle._refine_tasks) == 1
        assert store.get_layout_solution(group_id=GROUP, sig=sig) == tuple(
            reversed(spec.members)
        ), "精修还没回来就先生效，或者短预算的解没落库"
    finally:
        gate.set()
    await _drain_refines()

    assert handle._refine_tasks == {}, "精修跑完了但任务登记没摘掉"
    assert store.get_layout_solution(group_id=GROUP, sig=sig) == (
        spec.members[1],
        *(u for u in spec.members if u != spec.members[1]),
    ), "精修完成没有覆盖落库"


@pytest.mark.asyncio
async def test_graph_refine_failure_keeps_the_short_budget_solution(monkeypatch):
    """精修炸了：**只记日志**（命令侧无感），库里留着短预算那一版。"""
    from nonebot_plugin_waifu import handle, layout, slots, store

    _chain()
    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    sig = _sig_of(spec)

    def _fake(slot_xy, *, members, hub, edges, budget_s, seed, gap_min=16.0):
        SOLVE_CALLS.append({
            "members": tuple(members),
            "edges": tuple(edges),
            "hub": hub,
            "budget_s": float(budget_s),
            "in_loop": threading.current_thread() is threading.main_thread(),
        })
        if float(budget_s) == _refine_budget():
            raise RuntimeError("精修炸了")
        return tuple(reversed(tuple(members)))

    monkeypatch.setattr(slots, "solve", _fake)
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    assert store.get_layout_solution(group_id=GROUP, sig=sig) == tuple(
        reversed(spec.members)
    )
    assert _positions(runs[0]) == {
        n.uid: (n.x, n.y)
        for c in layout.layout_graph(
            view, focus=None, assignments={sig: tuple(reversed(spec.members))}
        )
        for n in c.nodes
    }, "出图没有用上短预算的那一版解"


# --------------------------------------------------------------------------- #
# 预算 0 的语义（审查 §5.3，F2）：solve 预算 0 = 完全不搜；精修预算 0 = 不派；
# 精修只允许**更强预算**的解覆盖缓存（budget_s 守卫）。
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_graph_solve_budget_zero_means_no_solve_and_no_refine(monkeypatch):
    """`WAIFU_LAYOUT_SOLVE_BUDGET=0` → **完全不搜**：不调 solve、不派精修，落点=历史序

    README/config 写的就是「0 = 不搜索、直接用历史序」—— 审查 §5.3 实测过旧行为不成立
    （仍会派 90s 后台精修，于是下一次出图用的就是排过序的解）。
    """
    import asyncio

    from nonebot_plugin_waifu import handle, layout, store
    from nonebot_plugin_waifu.config import plugin_config

    _chain()
    monkeypatch.setattr(plugin_config, "waifu_layout_solve_budget", 0.0)
    view, focus = _view()
    (spec,) = layout.cluster_specs(view)
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await asyncio.sleep(0)  # 给「万一被派出的精修任务」一个起跑机会
    await _drain_refines()

    assert SOLVE_CALLS == [], "预算 0 还在求解"
    assert handle._refine_tasks == {}, "预算 0 还派了后台精修"
    assert store.get_layout_solution(group_id=GROUP, sig=_sig_of(spec)) is None, (
        "预算 0 不该写缓存"
    )
    assert _positions(runs[0]) == _positions(layout.layout_graph(view, focus=focus))


@pytest.mark.asyncio
async def test_graph_refine_budget_zero_spawns_nothing_and_keeps_the_solution(
    monkeypatch,
):
    """`WAIFU_LAYOUT_REFINE_BUDGET=0` → 不派精修，短预算的好解**不被历史序覆盖**。

    审查 §5.3 的坑：0 预算的「精修」会返回历史序，派出去等于每张新图都把好解换回历史序。
    """
    from nonebot_plugin_waifu import handle, layout, store
    from nonebot_plugin_waifu.config import plugin_config

    _chain()
    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    sig = _sig_of(spec)
    monkeypatch.setattr(plugin_config, "waifu_layout_refine_budget", 0.0)
    runs: list = []
    _spectator(monkeypatch, runs)
    await handle._h_graph(bot=_bot(), event=_event(), args=Message())
    await _drain_refines()

    assert len(_short_calls()) == 1, "短预算求解应照常跑"
    assert _refine_calls() == [], "精修预算 0 还是派了精修"
    assert handle._refine_tasks == {}
    assert store.get_layout_solution(group_id=GROUP, sig=sig) == tuple(
        reversed(spec.members)
    ), "精修预算 0 把好解覆盖成了历史序"


@pytest.mark.asyncio
async def test_graph_refine_never_overwrites_a_stronger_solution(monkeypatch):
    """守卫：精修预算不高于缓存里那条解的预算（`budget_s`）→ 放弃，不重解不覆盖。

    场景：缓存里先有一份 90s 的解，后来 `WAIFU_LAYOUT_REFINE_BUDGET` 被调小 ——
    弱预算的解不许把强解换掉（否则就是「好解 → 差解」）。
    """
    from nonebot_plugin_waifu import handle, layout, store
    from nonebot_plugin_waifu.config import plugin_config

    _chain()
    view, _focus = _view()
    (spec,) = layout.cluster_specs(view)
    sig = _sig_of(spec)
    strong = tuple(reversed(spec.members))
    store.save_layout_solution(
        group_id=GROUP, sig=sig, uids=strong, cost=-1.0, budget_s=90.0
    )
    monkeypatch.setattr(plugin_config, "waifu_layout_refine_budget", 8.0)
    await handle._layout_refine(GROUP, sig, spec)
    assert SOLVE_CALLS == [], "弱预算精修还重解了一遍（白烧 CPU）"
    assert store.get_layout_solution(group_id=GROUP, sig=sig) == strong, (
        "弱预算精修覆盖了强预算的解"
    )

    # 预算持平也不覆盖（同样的步数只会解出同一个解，覆盖纯属白写）
    store.save_layout_solution(
        group_id=GROUP, sig=sig, uids=strong, cost=-1.0, budget_s=8.0
    )
    monkeypatch.setattr(plugin_config, "waifu_layout_refine_budget", 8.0)
    await handle._layout_refine(GROUP, sig, spec)
    assert SOLVE_CALLS == []
    assert store.get_layout_solution(group_id=GROUP, sig=sig) == strong
