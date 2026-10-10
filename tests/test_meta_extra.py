"""信息插件（功能）所需的帮助元数据（`__plugin_meta__.extra`）。

新「信息插件（功能）」直接读各插件 `__plugin_meta__.extra` 渲染帮助菜单：
`menu_type` 决定归到哪一类、`order` 决定类内排序、`commands` 出命令列表。

⚠️ 第二条测试才是真守卫：命令词里「换老婆」是**配置驱动**的
（生产 `WAIFU_CHANGE_CMD=换一只`）。若 extra 里写死字面量，
生产菜单会与真实注册的命令词**对不上**（发菜单里那词毫无反应），
而这里因为默认值恰好等于字面量，本地测试还会全绿 —— 所以必须与
`plugin_config.waifu_change_cmd` / `handle.CMD_NAMES` 双向对齐。
"""

from __future__ import annotations

import nonebot_plugin_waifu as pkg
from nonebot_plugin_waifu import handle
from nonebot_plugin_waifu.config import plugin_config


def test_plugin_meta_has_menu_type():
    """菜单分类与排序权重是按新插件规范给的固定值。"""
    extra = pkg.__plugin_meta__.extra
    assert extra["menu_type"] == "群内小游戏"
    assert extra["order"] == 10


def test_plugin_meta_commands_complete():
    """五条命令齐全、顺序稳定、每条都有非空 desc，且与真实注册命令一致。"""
    extra = pkg.__plugin_meta__.extra
    commands = extra["commands"]

    words = [item["command"] for item in commands]
    # 顺序 = 菜单展示顺序（用户拍板）
    assert words == [
        "今日老婆",
        plugin_config.waifu_change_cmd,
        "确认老婆",
        "娶老婆",
        "离婚",
    ]
    # 与 handle 里真正注册的命令词一一对齐：配置改了 extra 必须跟着变
    assert set(words) == set(handle.CMD_NAMES)

    for item in commands:
        assert item["desc"].strip(), f"命令「{item['command']}」的 desc 为空"
