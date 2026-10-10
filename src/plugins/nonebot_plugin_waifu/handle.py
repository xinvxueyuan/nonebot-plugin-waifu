"""命令层（占位骨架：Task 11 起逐步实现五个命令的 matcher 与 handler）。

本模块当前只导出 `CMD_NAMES`，作为命令词的**单一定义处**，供插件元数据与测试引用。
真正的响应器注册（`on_command(...)` / `on_message(...)`）在 Task 11 追加。
"""

from __future__ import annotations

from typing import Final

#: 五个命令词（Task 11 会拆出配置项后可变的「换老婆」；此处先用源码默认值）。
CMD_TODAY: Final[str] = "今日老婆"
CMD_CHANGE: Final[str] = "换一只"
CMD_CONFIRM: Final[str] = "确认老婆"
CMD_MARRY: Final[str] = "娶老婆"
CMD_DIVORCE: Final[str] = "离婚"

#: 命令词单一定义处（注册响应器与插件元数据都引它）。
CMD_NAMES: Final[tuple[str, ...]] = (
    CMD_TODAY,
    CMD_CHANGE,
    CMD_CONFIRM,
    CMD_MARRY,
    CMD_DIVORCE,
)

__all__ = ["CMD_NAMES"]
