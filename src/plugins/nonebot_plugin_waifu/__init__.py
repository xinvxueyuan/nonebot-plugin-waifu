"""今日老婆插件主模块。

## 两个 matcher

1. **`on_message` catch-all（`block=False`）** —— 唯一职责是给「活跃成员池」记一笔
   （用户要求：不活跃就抽不到）。**必须 `block=False`**：它兜住所有群消息，
   设成 True 会把别的插件全饿死。它**从不**阻断传播、也从不回消息。
2. **六个命令（`priority` 更小、`block=True`）** 在 `handle.py`。

## 容灾

状态全落 sqlite（`store`），启动时 `replay_from_store()` 只做三件事：
清掉过期的待确认请求 → 记录统计日志 → 给懒初始化兜底。因为**真相就在库里**，
进程被杀/机器重启都不会丢（不像内存态任务）。

## 干跑不碰库

`get_driver().on_startup` 在只 import 不 run 的干跑里**不触发**，所以干跑天然安全。
"""

from __future__ import annotations

import time

from nonebot import get_driver, logger, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent
from nonebot.plugin import PluginMetadata
from nonebot.rule import is_type

from . import handle, rules, store
from .config import plugin_config

__plugin_meta__ = PluginMetadata(
    name="今日老婆",
    description="从群内活跃成员里抽一位当老婆，支持换一只/确认/娶老婆/离婚/指婚",
    usage="\n".join([
        "今日老婆 / 抽老婆   当天首次抽取；再发只展示当前老婆",
        "换一只              重新抽一位（消耗次数）",
        "确认老婆            锁定今日老婆并从抽取池移除（次日恢复）",
        "娶老婆 @某人          需双方互相发；3 分钟内有效",
        "离婚 @某人          无需对方同意（单人即时生效）；无冷却（随离随抽）",
        "老婆关系图          本群老婆关系图；@某人 则只看 ta 的一度关系",
        "指婚 @a @b @c       仅超管（WIFE_SUPERUSERS）：字面绕过双方，让 a 娶 b、c",
        "",
        f"每天最多 {plugin_config.waifu_daily_limit} 次（含首次与每次换一只）",
    ]),
    type="application",
    homepage="https://github.com/xinvxueyuan/nonebot-plugin-waifu",
    supported_adapters={"nonebot.adapters.onebot.v11"},
    extra={
        "menu_type": "群内小游戏",
        "commands": [
            {"command": "今日老婆", "desc": "从群友里抽一位今日老婆"},
            {
                "command": plugin_config.waifu_change_cmd,
                "desc": "重新抽一位（不重复、消耗次数）",
            },
            {"command": "确认老婆", "desc": "定死今天的（今天不能再换，只锁自己）"},
            {"command": "娶老婆", "desc": "@某人求婚（可一次 @ 多人），需双方互相发"},
            {"command": "离婚", "desc": "与已娶的人离婚（无需对方同意，即时生效）"},
            {
                "command": plugin_config.waifu_graph_cmd,
                "desc": "本群老婆关系图（夫妻/倾心），可 @某人 看 ta 的",
            },
            {
                "command": "指婚",
                "desc": "仅超管（WIFE_SUPERUSERS）：字面绕过双方，第一位 @ 的娶其余人",
            },
        ],
        "order": 10,
    },
)


def _ensure_store() -> None:
    """懒初始化（幂等）。测试通过 conftest 抢先占位，不会被覆盖。"""
    if store.is_initialized():
        return
    try:
        store.init(store.default_db_path())
    except Exception:
        logger.exception("今日老婆：库初始化失败")


def replay_from_store() -> dict[str, int]:
    """启动时重放：清过期请求 + 统计。**不碰任何平台 API**（安全）。

    Returns:
        `{"members": 该次统计可见成员数（只统计第一张表总数）, "married": ..., ...}`
        这里给的是**全局计数**，用于日志与验收断言。
    """
    _ensure_store()
    summary: dict[str, int] = {"members": 0, "married": 0, "pending_cleared": 0}
    try:
        with store._conn() as conn:  # 内部只读统计
            summary["members"] = int(
                conn.execute("SELECT COUNT(*) AS n FROM waifu_members").fetchone()["n"]
            )
            summary["married"] = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM waifu_marriage WHERE status = 'married'"
                ).fetchone()["n"]
            )
    except Exception:
        logger.exception("今日老婆：统计失败（不影响功能）")

    summary["pending_cleared"] = store.purge_pending_before(
        now=time.time(), window=rules.PENDING_WINDOW_SECONDS
    )
    logger.info(
        "今日老婆容灾：成员 {} 条、已婚 {} 条、清理过期待确认 {} 条",
        summary["members"],
        summary["married"],
        summary["pending_cleared"],
    )
    return summary


async def _on_startup() -> None:
    _ensure_store()
    replay_from_store()


get_driver().on_startup(_on_startup)

# ── 活跃度收集（catch-all，**绝不阻断**）──────────────────────────
waifu_watch = on_message(
    rule=is_type(GroupMessageEvent),
    priority=plugin_config.waifu_match_priority,
    block=False,
)

if plugin_config.waifu_match_priority <= plugin_config.waifu_command_priority:
    logger.warning(
        "WAIFU_MATCH_PRIORITY({}) <= 命令优先级({})：命令可能被监听抢先。请调大。",
        plugin_config.waifu_match_priority,
        plugin_config.waifu_command_priority,
    )


@waifu_watch.handle()
async def _record_member(_bot: Bot, event: GroupMessageEvent) -> None:
    """记一笔活跃度。**永不阻断、永不回消息、永不抛**。"""
    _ensure_store()
    try:
        sender = getattr(event, "sender", None)
        nick = str(
            getattr(sender, "card", None) or getattr(sender, "nickname", None) or ""
        )
        store.touch_member(
            group_id=event.group_id, user_id=int(event.user_id), nickname=nick
        )
    except Exception:
        logger.exception("今日老婆：记录活跃度失败（已忽略）")


__all__ = [
    "__plugin_meta__",
    "handle",
    "replay_from_store",
    "waifu_watch",
]
