"""pytest 全局初始化。

**为什么必须有这个文件**：本包的 `__init__.py` 是 NoneBot 插件入口，模块级会调用
`on_message()` / `require()` / `get_plugin_config()`。测试 `import nonebot_plugin_waifu`
会先执行它 —— 没有初始化过的 driver 直接抛
`ValueError: NoneBot has not been initialized.`。conftest 最先被 pytest 导入，赶在前面。

⚠️ `COMMAND_START` 含**空串**（生产 qbot 里裸词直接触发）。空串**不能**用
`nonebot.init(COMMAND_START=["", "/"])` 传（pydantic 会丢掉空字符串，只剩 `{'/'}`），
必须走环境变量 JSON 反序列化。
"""

import os

import nonebot
from nonebot.adapters.onebot.v11 import Adapter
import pytest

os.environ.setdefault("COMMAND_START", '["", "/"]')

# 用 none 驱动：单元测试不需要任何服务器/客户端，省掉 fastapi+uvicorn 那一坨。
nonebot.init(driver="~none", log_level="WARNING")
nonebot.get_driver().register_adapter(Adapter)


@pytest.fixture(autouse=True)
def _isolate_store(tmp_path, monkeypatch):
    """**每个测试**都把库指到临时目录（autouse，忘不掉）。

    这是安全护栏：`store.default_db_path()` 指向 localstore 的**真实**插件数据目录，
    哪个测试漏了隔离又踩到懒初始化，就会往本机数据目录里写表 —— 本地发现不了。
    """
    from nonebot_plugin_waifu import store

    path = tmp_path / "waifu.sqlite3"
    monkeypatch.setattr(store, "_db_path", path, raising=False)
    store.init(path)
    return path


@pytest.fixture
def tmp_store(_isolate_store):
    """需要库路径时用它（与 autouse 隔离是同一个文件）。"""
    return _isolate_store
