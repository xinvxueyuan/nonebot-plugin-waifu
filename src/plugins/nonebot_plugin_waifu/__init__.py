"""今日老婆插件（占位骨架，后续 Task 逐步填充）。"""

from __future__ import annotations

from nonebot.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="今日老婆",
    description="从群内活跃成员里抽一位当老婆，支持换一只/确认/娶老婆/离婚",
    usage="今日老婆 / 换一只 / 确认老婆 / 娶老婆 @某人 / 离婚 @某人",
    type="application",
    homepage="https://github.com/xinvxueyuan/nonebot-plugin-waifu",
    supported_adapters={"nonebot.adapters.onebot.v11"},
)
