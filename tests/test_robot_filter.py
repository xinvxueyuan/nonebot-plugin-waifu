"""机器人守卫：`in_ranges` 纯函数、公开 API 取区间 + 兜底 + TTL 缓存 + 失败降级。

**生产实证**：`娶老婆 @4014989500` 曾把官方机器人当真人求婚成功。
本模块的公开面（`in_ranges` / `fetch_robot_ranges` / `is_robot` / `reset_cache`）
由 `handle.apply_marry` + `_h_marry` 消费。
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.exception import ActionFailed, ApiNotAvailable
import pytest

GROUP = 1094538078
ME = 10001
REAL_HUMAN = 1330509996

# 生产 bundle 里实测到的官方机器人区间（内置兜底）
BOT_4014 = 4014989500  # 4010000000-4019999999
BOT_3889 = 3889006601  # 3889000000-3889999999
BOT_3328 = 3328144510  # 3328144510-3328144510
BOT_2854 = 2854196301  # 2854196301-2854216399
BOT_6660 = 66600000  # 66600000-66600000


def _event(user_id: int = ME) -> GroupMessageEvent:
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


# --------------------------------------------------------------------------- #
# 1. in_ranges 边界（含端点）
# --------------------------------------------------------------------------- #
def test_in_ranges_boundaries():
    from nonebot_plugin_waifu import robot_filter

    ranges = [(4010000000, 4019999999)]
    assert robot_filter.in_ranges(4010000000, ranges)  # 下端点含
    assert robot_filter.in_ranges(4019999999, ranges)  # 上端点含
    assert robot_filter.in_ranges(4015000000, ranges)
    assert not robot_filter.in_ranges(4009999999, ranges)  # 区间外
    assert not robot_filter.in_ranges(4020000000, ranges)
    # 空 ranges → 恒 False（不是崩）
    assert not robot_filter.in_ranges(4014989500, [])


def test_in_ranges_accepts_min_max_dict_shape():
    """也接受 `{"minUin":"..","maxUin":".."}` 形状的 list（API 原始行）。"""
    from nonebot_plugin_waifu import robot_filter

    rows = [{"minUin": "4010000000", "maxUin": "4019999999"}]
    assert robot_filter.in_ranges(4014989500, rows)
    assert not robot_filter.in_ranges(1330509996, rows)


# --------------------------------------------------------------------------- #
# 2. 内置兜底区间覆盖已知机器人
# --------------------------------------------------------------------------- #
def test_fallback_ranges_cover_known_bots():
    from nonebot_plugin_waifu import robot_filter

    for bot in (BOT_4014, BOT_3889, BOT_3328, BOT_2854, BOT_6660):
        assert robot_filter.in_ranges(bot, robot_filter._FALLBACK_RANGES), bot
    assert not robot_filter.in_ranges(REAL_HUMAN, robot_filter._FALLBACK_RANGES)


# --------------------------------------------------------------------------- #
# 3. 契约守卫：适配器解包后的形状 / 信封形状都吃
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fetch_robot_ranges_unwraps_data_contract():
    from nonebot_plugin_waifu import robot_filter

    rows = [{"minUin": "4010000000", "maxUin": "4019999999"}]

    # ① 适配器 `handle_api_result` 会 `return result.get("data")` → 到手就是这个 list
    robot_filter.reset_cache()
    bot = MagicMock()
    bot.call_api = AsyncMock(return_value=rows)
    got = await robot_filter.fetch_robot_ranges(bot)
    assert got == [(4010000000, 4019999999)]

    # ② 也容错仍是信封的形状
    robot_filter.reset_cache()
    bot2 = MagicMock()
    bot2.call_api = AsyncMock(
        return_value={
            "status": "ok",
            "retcode": 0,
            "data": rows,
            "message": "",
            "wording": "",
        }
    )
    got2 = await robot_filter.fetch_robot_ranges(bot2)
    assert got2 == [(4010000000, 4019999999)]

    # ③ 契约守卫：钉住**适配器本身**的解包行为，而不是钉住我们的 mock
    from nonebot.adapters.onebot.v11.utils import handle_api_result

    env = {"status": "ok", "retcode": 0, "data": rows}
    assert handle_api_result(env) is rows


# --------------------------------------------------------------------------- #
# 4. 失败降级：任何异常 → 内置兜底、绝不抛
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        ActionFailed("OneBot V11", "boom"),
        ApiNotAvailable("boom"),
        RuntimeError("boom"),
    ],
)
async def test_fetch_robot_ranges_degrades_on_exception(exc):
    from nonebot_plugin_waifu import robot_filter

    robot_filter.reset_cache()
    bot = MagicMock()
    bot.call_api = AsyncMock(side_effect=exc)
    got = await robot_filter.fetch_robot_ranges(bot)  # 绝不抛
    assert got == list(robot_filter._FALLBACK_RANGES)
    # 兜底区间真的能判机器人
    assert robot_filter.in_ranges(BOT_4014, got)


# --------------------------------------------------------------------------- #
# 5. TTL 缓存
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fetch_robot_ranges_caches():
    from nonebot_plugin_waifu import robot_filter

    robot_filter.reset_cache()
    rows = [{"minUin": "4010000000", "maxUin": "4019999999"}]
    bot = MagicMock()
    bot.call_api = AsyncMock(return_value=rows)

    await robot_filter.fetch_robot_ranges(bot)
    await robot_filter.fetch_robot_ranges(bot)
    assert bot.call_api.await_count == 1, "两次调用应只打一次 API"

    robot_filter.reset_cache()
    await robot_filter.fetch_robot_ranges(bot)
    assert bot.call_api.await_count == 2, "reset_cache 后应重新调 API"


# --------------------------------------------------------------------------- #
# 6. is_robot：self_id 命中
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_is_robot_detects_self_id():
    from nonebot_plugin_waifu import robot_filter

    robot_filter.reset_cache()
    bot = MagicMock()
    # 故意用一个**不在任何区间**的 self_id，证明走的是 self_id 分支而非区间
    bot.self_id = "1234567890"
    bot.call_api = AsyncMock(return_value=[])
    assert await robot_filter.is_robot(bot, 1234567890)
    bot.call_api.assert_not_awaited()  # self_id 命中 → 根本不需要查区间


@pytest.mark.asyncio
async def test_is_robot_uses_ranges_for_others():
    from nonebot_plugin_waifu import robot_filter

    robot_filter.reset_cache()
    bot = MagicMock()
    bot.self_id = "1234567890"
    bot.call_api = AsyncMock(return_value=[])
    assert await robot_filter.is_robot(bot, BOT_4014)  # 兜底区间命中
    assert not await robot_filter.is_robot(bot, REAL_HUMAN)


# --------------------------------------------------------------------------- #
# 7/8. apply_marry 拒绝机器人目标（整条拒绝、全有全无）
# --------------------------------------------------------------------------- #
def test_apply_marry_rejects_robot_target():
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(
        group_id=GROUP,
        requester=ME,
        partners=[BOT_4014],
        now=1.0,
        max_partners=1,
        robot_qqs=frozenset({BOT_4014}),
    )
    assert out.error, "命中机器人必须拒绝"
    assert str(BOT_4014) in out.error
    assert "机器人" in out.error
    # 库里无变化：无 pending、无婚约
    assert (
        store.get_pending(GROUP, requester=ME, partner=BOT_4014, kind="marry") is None
    )
    assert store.list_partners(GROUP, ME) == []


def test_apply_marry_normal_target_still_works():
    """非机器人目标不受守卫影响（防止守卫误伤正常人）。"""
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(
        group_id=GROUP,
        requester=ME,
        partners=[REAL_HUMAN],
        now=1.0,
        max_partners=1,
        robot_qqs=frozenset({BOT_4014}),
    )
    assert not out.error
    assert (
        store.get_pending(GROUP, requester=ME, partner=REAL_HUMAN, kind="marry")
        is not None
    )


def test_apply_marry_multi_rejected_if_any_robot():
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(
        group_id=GROUP,
        requester=ME,
        partners=[REAL_HUMAN, BOT_4014],
        now=1.0,
        max_partners=5,
        robot_qqs=frozenset({BOT_4014}),
    )
    assert out.error, "多目标里有一个机器人 → 整条拒绝"
    assert (
        store.get_pending(GROUP, requester=ME, partner=REAL_HUMAN, kind="marry") is None
    )
    assert (
        store.get_pending(GROUP, requester=ME, partner=BOT_4014, kind="marry") is None
    )
    assert store.list_partners(GROUP, ME) == []


# --------------------------------------------------------------------------- #
# 9. 接线守卫（AST）：_h_marry 真的把机器人集合传进 apply_marry
# --------------------------------------------------------------------------- #
def test_h_marry_passes_robot_qqs():
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "apply_marry":
            call = node
    assert call is not None, "找不到 apply_marry 调用 —— 接线可能被删了"
    kw = {k.arg: k.value for k in call.keywords}
    assert "robot_qqs" in kw, "apply_marry 漏传 robot_qqs（守卫会静默失效）"
    flat = ast.unparse(kw["robot_qqs"])
    assert "is_robot" in flat and "robot_filter" in flat, f"没接到 is_robot：{flat}"


def test_h_marry_uses_bot_param():
    """`_h_marry` 现在要用 bot 取机器人集合 —— 形参名应为 `bot`（不带下划线）。"""
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_h_marry":
            fn = node
    assert fn is not None, "找不到 _h_marry"
    names = [a.arg for a in fn.args.args]
    assert "bot" in names, f"_h_marry 形参里没有 bot：{names}"
    assert "_bot" not in names, "_h_marry 不该再用 _bot"


# --------------------------------------------------------------------------- #
# 模块公开面
# --------------------------------------------------------------------------- #
def test_robot_filter_public_surface():
    from nonebot_plugin_waifu import robot_filter

    for name in ("in_ranges", "fetch_robot_ranges", "is_robot", "reset_cache"):
        assert name in robot_filter.__all__, name
        assert callable(getattr(robot_filter, name))
    assert robot_filter._TTL_SECONDS == 3600
