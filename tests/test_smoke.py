"""冒烟：插件能被导入、元数据在位。"""


def test_plugin_imports():
    import nonebot_plugin_waifu as p

    assert p.__plugin_meta__.name == "今日老婆"


def test_command_names_exported():
    from nonebot_plugin_waifu import handle

    assert "今日老婆" in handle.CMD_NAMES
