"""群成员信息获取（性别 / 昵称）。

## 为什么必须调 API 才能拿性别

**实测（2026-10-10）**：生产库抽查 393 条最新群消息事件，`sender` 里
`card/level/nickname/role/title/user_id` 各出现 393 次，而 `sex` 出现 384 次
**且取值全是 `null`**。所以「从事件里读性别」是**死码**，只能调成员信息 API。

## `get_group_member_info` 是真实方法（**不要**加 pyright ignore）

先前判断有误，已纠正：v11 适配器的 `bot.py` 里确实没有 `def`，但
`nonebot/internal/adapter/bot.py:47` 的 `BaseBot.__getattr__` 返回 `_ApiCall`
代理（`async def __call__(**kwargs)`）→ **任何 action 名都是真实可调用对象**，
调用即走 `adapter._call_api`；且 `bot.pyi:353` 已声明它 —— pyright 认得。

## 两种返回形状都要接受

LLBot 的 action schema 是 `{member: {...}}`；而适配器 `handle_api_result` 会解包
顶层 `data`，所以到手的也可能是**单层**成员对象。**别赌某一层在哪拆** —— 两种都收。

## 性别只用于展示

用户 2026-10-10 口径：**性别不参与抽取筛选**，只在渲染卡片时展示。
所以这里返回的 `sex` 不喂给 `pick_target`。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .rules import normalize_sex

if TYPE_CHECKING:
    from nonebot.adapters.onebot.v11 import Bot

logger = logging.getLogger("nonebot_plugin_waifu")


def parse_member_info(payload: Any) -> dict[str, Any]:
    """把 API 返回（信封 / `{member:...}` / 单层对象）规范成成员字典。

    返回 `{"sex": "male|female|unknown", "nickname": str, "user_id": int|None}`。
    容忍任何垃圾输入（返回 unknown/空），**绝不抛**。
    """
    obj: Any = payload
    if isinstance(obj, dict) and "member" in obj:
        inner = obj.get("member")
        obj = inner if isinstance(inner, dict) else {}
    if not isinstance(obj, dict):
        return {"sex": "unknown", "nickname": "", "user_id": None}

    nick = str(obj.get("card") or obj.get("nickname") or "").strip()
    uid = obj.get("user_id")
    try:
        uid_int: int | None = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        uid_int = None
    return {"sex": normalize_sex(obj.get("sex")), "nickname": nick, "user_id": uid_int}


async def fetch_member_info(bot: Bot, *, group_id: int, user_id: int) -> dict[str, Any]:
    """取成员信息；**任何失败都回退 unknown + 空昵称**。

    包 try 只为**网络/协议端失败**兜底（成员已退群、断连、协议端不支持该 action）：
    性别只是展示项，不能让它毁掉整条抽取。
    """
    try:
        raw = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
    except Exception as e:
        logger.warning(
            "取成员信息失败 group=%s user=%s: %s: %s",
            group_id,
            user_id,
            type(e).__name__,
            e,
        )
        return {"sex": "unknown", "nickname": "", "user_id": user_id}
    return parse_member_info(raw)


__all__ = ["fetch_member_info", "parse_member_info"]
