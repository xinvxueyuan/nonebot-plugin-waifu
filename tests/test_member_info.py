"""成员信息获取：性别/昵称，以及**适配器解包契约**。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def test_parse_member_info_returns_no_avatar_field():
    """`member_info` **不负责**头像 —— 头像只在 render 里（DRY，别两处各写一份）。"""
    from nonebot_plugin_waifu import member_info

    got = member_info.parse_member_info({
        "user_id": 1,
        "sex": "female",
        "nickname": "甲",
    })
    assert "avatar" not in got
    assert not hasattr(member_info, "avatar_url")


def test_parse_member_info_unwrapped_single_layer():
    """适配器 `handle_api_result` 会解包顶层 `data` → 到手的可能是**单层**成员对象。"""
    from nonebot_plugin_waifu import member_info

    got = member_info.parse_member_info({
        "user_id": 1,
        "sex": "female",
        "nickname": "甲",
    })
    assert got["sex"] == "female"
    assert got["nickname"] == "甲"


def test_parse_member_info_wrapped_two_layer():
    """LLBot 的 action schema 是 `{member: {...}}` —— **两种形状都要接受**。"""
    from nonebot_plugin_waifu import member_info

    payload = {"member": {"user_id": 1, "sex": "male", "nickname": "乙"}}
    got = member_info.parse_member_info(payload)
    assert got["sex"] == "male"
    assert got["nickname"] == "乙"


def test_parse_member_info_tolerates_garbage():
    from nonebot_plugin_waifu import member_info

    for bad in (None, [], "x", 0, {}, {"member": None}, {"member": "x"}):
        got = member_info.parse_member_info(bad)
        assert got["sex"] == "unknown"
        assert got["nickname"] == ""


def test_parse_member_info_prefers_card_over_nickname():
    from nonebot_plugin_waifu import member_info

    got = member_info.parse_member_info({
        "card": "群名片",
        "nickname": "真名",
        "sex": "female",
    })
    assert got["nickname"] == "群名片"


@pytest.mark.asyncio
async def test_fetch_returns_unknown_on_api_failure():
    """API 挂了**不能抛** —— 性别只是展示项，不能让它毁掉整个抽取。"""
    from nonebot_plugin_waifu import member_info

    bot = MagicMock()
    bot.get_group_member_info = AsyncMock(side_effect=RuntimeError("boom"))
    got = await member_info.fetch_member_info(bot, group_id=1, user_id=2)
    assert got["sex"] == "unknown"


@pytest.mark.asyncio
async def test_fetch_treats_missing_sex_as_unknown():
    """API 返回里没有 `sex` 字段（或为 None）→ 必须是 `"unknown"`。

    这直接决定取向筛的行为：`unknown` 在**三个档**下都判为通过（用户口径），
    所以「取不到性别」永远不会把人静默排除。
    """
    from nonebot_plugin_waifu import member_info

    bot = MagicMock()
    bot.get_group_member_info = AsyncMock(return_value={"member": {"nickname": "甲"}})
    got = await member_info.fetch_member_info(bot, group_id=1, user_id=2)
    assert got["sex"] == "unknown"

    bot2 = MagicMock()
    bot2.get_group_member_info = AsyncMock(return_value={"member": {"sex": None}})
    got2 = await member_info.fetch_member_info(bot2, group_id=1, user_id=2)
    assert got2["sex"] == "unknown"


@pytest.mark.asyncio
async def test_fetch_normalizes_sex():
    from nonebot_plugin_waifu import member_info

    bot = MagicMock()
    bot.get_group_member_info = AsyncMock(return_value={"member": {"sex": "FEMALE"}})
    got = await member_info.fetch_member_info(bot, group_id=1, user_id=2)
    assert got["sex"] == "female"


def test_adapter_unwraps_data_field():
    """**契约守卫**：钉住适配器本身的解包行为，而不是钉住我们的 mock。

    适配器（onebot v11 2.4.6）里没有 `Bot.handle_api_result` 方法 —— 解包行为在
    模块级函数 `nonebot.adapters.onebot.v11.utils.handle_api_result`，
    `Adapter._call_api` 正是拿它包住协议端返回值：结尾 `return result.get("data")`。
    所以 `bot.call_api("<私有 action>")` 拿到的**不是**响应信封，而是里面的 `data`。
    适配器哪天不再解包，这条测试要立刻红。
    """
    from nonebot.adapters.onebot.v11.utils import handle_api_result

    rows = [{"uin": "10001", "nick": "甲"}]
    wrapped = {"status": "ok", "retcode": 0, "data": rows}
    assert handle_api_result(wrapped) is rows
