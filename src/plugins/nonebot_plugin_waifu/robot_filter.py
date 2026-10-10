"""机器人判定：用公开 API `get_robot_uin_range` + 内置兜底区间 + TTL 缓存。

## 为什么需要它（生产实证）

生产群测试时发现：`娶老婆 @4014989500` **把官方机器人当真人求婚成功了**
（`waifu_pending` 表里躺着 `partner=4014989500`）。所以「娶老婆」的目标校验阶段
必须把机器人挡掉 —— 见 `handle.apply_marry(robot_qqs=...)`。

## 公开 API 与**适配器解包**（本模块最容易写错的地方）

上游 LLBot 提供私有动作 `get_robot_uin_range`，返回信封形如：

    {"status":"ok","retcode":0,"data":[{"minUin":"..","maxUin":".."}, ...],
     "message":"","wording":""}

但 OneBot V11 适配器的 `handle_api_result` 结尾是 `return result.get("data")`，
所以 `bot.call_api("get_robot_uin_range")` 拿到的**直接就是那个区间 list**，
**不是**整个信封。**两种形状都接受**（别赌某一层在哪拆）—— 见 `_normalize_ranges`。

## 绝不抛（本仓历史教训）

任何失败（`ActionFailed` / `NetworkError` / `ApiNotAvailable` / `NotImplementedError` /
其它）都必须**降级返回内置兜底区间** + 一条 warning。**绝不抛** ——
否则一次协议端不支持就会让整条命令「毫无反应」。

## TTL 缓存

区间相对稳定，但不要每次命令都调一次 API：进程内缓存 `_TTL_SECONDS` 秒，
`reset_cache()` 供测试用。
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

logger = logging.getLogger("nonebot_plugin_waifu")

#: 公开动作名（LLBot 私有实现，非 OneBot 标准）。
ACTION_GET_ROBOT_UIN_RANGE: Final[str] = "get_robot_uin_range"

#: **内置兜底区间**：从生产 bundle 里实测到的值。
#: API 取不到时用它 —— 保证「至少挡住已知的官方机器人」。
_FALLBACK_RANGES: Final[tuple[tuple[int, int], ...]] = (
    (3328144510, 3328144510),
    (2854196301, 2854216399),
    (66600000, 66600000),
    (3889000000, 3889999999),
    (4010000000, 4019999999),
)

#: 缓存有效期（秒）。
_TTL_SECONDS: Final[int] = 3600

#: 进程内缓存：`(写入时刻, 区间列表)`；`None` = 未缓存。
#: 小写（可变模块级状态）—— pyright strict 会把全大写名当常量、禁止重赋值。
_cache: tuple[float, list[tuple[int, int]]] | None = None


def reset_cache() -> None:
    """清空进程内缓存（供测试用）。"""
    global _cache
    _cache = None


def _to_int(value: Any) -> int | None:
    """容忍 `"123"` / `123` 两种写法；失败返回 None（不抛）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _normalize_ranges(payload: Any) -> list[tuple[int, int]]:
    """把 API 返回规范成 `[(min, max), ...]`，**两种形状都接受**。

    - 适配器解包后：直接是区间 list；
    - 仍是信封：取里面的 `data`。

    数组里混入非 dict 行（协议端插了字符串 / None）要**跳过该行**，
    不要让整个列表解析抛。解析失败/为空 → 返回 `[]`（由调用方决定是否兜底）。
    """
    rows = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out: list[tuple[int, int]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lo = _to_int(row.get("minUin"))
        hi = _to_int(row.get("maxUin"))
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        out.append((lo, hi))
    return out


def in_ranges(qq: int, ranges: Any) -> bool:
    """判断 `qq` 是否落在任一区间内（**边界含端点**）。

    `ranges` 可以是：
    - `{"minUin":"..","maxUin":".."}` 形状的 list（API 原始行）；
    - `(min, max)` 元组 list（本模块规范形状）。

    纯函数、不抛；空 ranges → False。
    """
    if not isinstance(ranges, list):
        try:
            ranges = list(ranges)
        except TypeError:
            return False
    for item in ranges:
        if isinstance(item, dict):
            lo = _to_int(item.get("minUin"))
            hi = _to_int(item.get("maxUin"))
        else:
            try:
                lo, hi = _to_int(item[0]), _to_int(item[1])
            except (TypeError, IndexError, KeyError):
                continue
        if lo is None or hi is None:
            continue
        if lo > hi:
            lo, hi = hi, lo
        if lo <= qq <= hi:
            return True
    return False


def _fallback_ranges() -> list[tuple[int, int]]:
    """内置兜底区间的**可变副本**（带明确元素类型，避免 Final 字面量收窄）。"""
    return [(lo, hi) for lo, hi in _FALLBACK_RANGES]


async def fetch_robot_ranges(bot: Bot) -> list[tuple[int, int]]:
    """取机器人 QQ 号区间；**任何失败都降级返回内置兜底、绝不抛**。

    带 TTL 缓存（`_TTL_SECONDS`）：区间相对稳定，不要每次命令都调一次 API。
    """
    global _cache
    now = time.monotonic()
    if _cache is not None:
        cached_at, ranges = _cache
        if now - cached_at < _TTL_SECONDS:
            return ranges
    try:
        payload = await bot.call_api(ACTION_GET_ROBOT_UIN_RANGE)
        ranges = _normalize_ranges(payload)
    except Exception as e:
        logger.warning(
            "取机器人区间失败（%s: %s），降级用内置兜底区间", type(e).__name__, e
        )
        fallback = _fallback_ranges()
        _cache = (now, fallback)
        return fallback
    if not ranges:
        # 取到了但内容为空/形状不认识 → 同样兜底（别让守卫形同虚设）
        logger.warning("机器人区间为空或无法解析，降级用内置兜底区间")
        ranges = _fallback_ranges()
    _cache = (now, ranges)
    return ranges


async def is_robot(bot: Bot, qq: int) -> bool:
    """判断 `qq` 是否为机器人：**先查 self_id，再查区间**。

    - `bot.self_id`（本机器人自身）永远判为机器人 —— 用户明确要求。
    - 其余按 `get_robot_uin_range`（失败时兜底区间）判定。
    """
    if str(qq) == str(getattr(bot, "self_id", "")):
        return True
    ranges = await fetch_robot_ranges(bot)
    return in_ranges(qq, ranges)


__all__ = [
    "ACTION_GET_ROBOT_UIN_RANGE",
    "fetch_robot_ranges",
    "in_ranges",
    "is_robot",
    "reset_cache",
]
