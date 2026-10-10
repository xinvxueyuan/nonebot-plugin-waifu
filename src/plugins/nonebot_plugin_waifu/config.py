"""今日老婆插件配置（环境变量前缀 `WAIFU_`）。

所有数值旋钮都放这里，别把裸数字散在代码里 ——
用户明确要求「支持配置可变（例如抽取次数）」。
"""

from __future__ import annotations

import logging
from typing import Any, Final, Literal

from nonebot import get_plugin_config
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger("nonebot_plugin_waifu")

#: 「抽」类命令的优先级。**必须大于** 0，且小于监听 matcher 的优先级。
COMMAND_PRIORITY: Final[int] = 3

#: 活跃度保留天数：超过这么久没发言的人**从池子里淘汰**（可选，默认关）。
DEFAULT_MEMBER_TTL_DAYS: Final[int] = 0

#: 卡片首行的**默认**模板。默认用「群友」；
#: **生产用 `WAIFU_HEADLINE_TEMPLATE` 覆盖成「群幼」**
#: （用户原话是「群幼」，见计划 §1.6）。占位符只有三个：
#:   {drawer}=抽的人的昵称  {partner}=老婆昵称  {user_id}=QQ 号
DEFAULT_HEADLINE_TEMPLATE: Final[str] = (
    "{drawer}, 你今天的群友老婆是 {partner}（{user_id}）"
)

#: 「换老婆」命令词的**默认值**。生产用 `WAIFU_CHANGE_CMD` 覆盖成「换一只」
#: （用户 2026-10-10：「换一只是换老婆的可变配置，用于生产」）。
DEFAULT_CHANGE_CMD: Final[str] = "换老婆"

#: 取向三档的中文写法 → 规范值
#: （用户用中文表达这三档，`.env` 里写中文不该让插件起不来）。
_ORIENTATION_ALIASES: Final[dict[str, str]] = {
    "正常": "normal",
    "所有": "all",
    "相反": "reverse",
    # 顺带收下英文别名，省得有人写 any/all 混着用
    "any": "all",
    "all": "all",
    "normal": "normal",
    "reverse": "reverse",
    "opposite": "reverse",
}


class Config(BaseModel):
    """今日老婆配置。"""

    waifu_daily_limit: int = Field(
        default=20,
        ge=1,
        le=1000,
        description="每人每天最多抽取次数（含当天首次与每次换一只），默认 20",
    )
    waifu_pending_minutes: int = Field(
        default=3,
        ge=1,
        le=60,
        description="娶老婆/离婚的二次确认窗口（分钟），默认 3",
    )
    waifu_divorce_cd_days: int = Field(
        default=3,
        ge=0,
        le=365,
        description="离婚后发起方的冷却天数，默认 3；0 = 不冷却",
    )
    waifu_orientation: Literal["normal", "all", "reverse"] = Field(
        default="all",
        description=(
            "取向：normal=正常（只抽异性）；all=所有（不限，默认）；reverse=相反（只抽同性）。"
            "性别隐藏/取不到的人**一律判为通过**"
        ),
    )
    waifu_show_quota_in_card: bool = Field(
        default=True,
        description="在结果卡片上显示「今日已用 N/上限 次」",
    )
    waifu_change_cmd: str = Field(
        default=DEFAULT_CHANGE_CMD,
        min_length=1,
        description=(
            "「换老婆」命令词。源码默认「换老婆」；**生产覆盖为「换一只」**"
            "（用户 2026-10-10：换一只是换老婆的可变配置）"
        ),
    )

    waifu_member_ttl_days: int = Field(
        default=DEFAULT_MEMBER_TTL_DAYS,
        ge=0,
        le=3650,
        description="活跃度保留天数：超过这么久没发言则不进抽取池；0 = 不淘汰",
    )
    waifu_command_priority: int = Field(
        default=COMMAND_PRIORITY,
        ge=1,
        description="命令 matcher 优先级（越小越先）",
    )
    waifu_match_priority: int = Field(
        default=5,
        ge=1,
        description=(
            "监听所有群消息的 matcher 优先级；"
            "**必须大于命令优先级**，否则命令会被监听抢先"
        ),
    )
    waifu_left_recently_message: str = Field(
        default="你刚离过婚，{days} 天后再来吧",
        description="离婚冷却期内的提示模板；{days} 会被替换为剩余天数",
    )
    waifu_headline_template: str = Field(
        default=DEFAULT_HEADLINE_TEMPLATE,
        description=(
            "卡片首行文案模板，可用 {drawer} {partner} {user_id} 三个占位符；"
            "默认「群友」，生产覆盖为「群幼」"
        ),
    )

    @model_validator(mode="after")
    def _check_priority_order(self) -> Config:
        """监听优先级必须大于命令优先级，否则命令会被 catch-all 抢先。

        手改 `.env` 写反了不该让插件起不来 —— 只告警并自动纠正。
        """
        if self.waifu_match_priority <= self.waifu_command_priority:
            logger.warning(
                "WAIFU_MATCH_PRIORITY(%s) <= WAIFU_COMMAND_PRIORITY(%s)，"
                "已自动调大；请检查 .env 是否写反",
                self.waifu_match_priority,
                self.waifu_command_priority,
            )
            self.waifu_match_priority = self.waifu_command_priority + 2
        return self

    @field_validator("waifu_orientation", mode="before")
    @classmethod
    def _accept_chinese_orientation(cls, v: Any) -> Any:
        """允许 `.env` 里直接写中文（正常/所有/相反）。

        ⚠️ 不加这个的话，写中文会让 pydantic 校验失败 →
        `ValidationError` → 插件**直接起不来**。用户就是用中文表达这三个档的，
        这种「配置写错就整个插件挂掉」的行为必须避免。
        """
        return _ORIENTATION_ALIASES.get(str(v).strip().casefold(), v)


plugin_config: Config = get_plugin_config(Config)

__all__ = [
    "COMMAND_PRIORITY",
    "DEFAULT_CHANGE_CMD",
    "DEFAULT_HEADLINE_TEMPLATE",
    "DEFAULT_MEMBER_TTL_DAYS",
    "Config",
    "plugin_config",
]
