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

#: 「老婆关系图」命令词的默认值（用户 2026-10-14 点名；可用 `WAIFU_GRAPH_CMD` 覆盖）。
DEFAULT_GRAPH_CMD: Final[str] = "老婆关系图"

#: 关系图最多画几条边（大图截断 + 页脚说明）。
#: 默认 = **字段上限 60**（用户 2026-10-14 要求：生产全群图去重后 30 条边，
#: 旧默认 20 会把全群图截掉 —— 想看全图不该还要自己配）。
DEFAULT_GRAPH_MAX_EDGES: Final[int] = 60

#: 「槽位重排」（v0.8.0）开关的默认值：出图前解一次「谁坐哪个坑」。
#: 关掉 = 完全回到历史落点序（逐像素一致），出图不再等任何求解。
DEFAULT_LAYOUT_SOLVE: Final[bool] = True

#: 首次求解的**秒预算**（默认 8s）：缓存未命中时先解一版够用的，出图不等满预算。
DEFAULT_LAYOUT_SOLVE_BUDGET: Final[float] = 8.0

#: 后台**精修**的秒预算（默认 90s）：首次出图后异步重解，覆盖缓存（下次出图用上）。
DEFAULT_LAYOUT_REFINE_BUDGET: Final[float] = 90.0

#: 求解器的随机种子（默认 20261017）：同种子 + 同参数 → **同一个解**（可复现）。
DEFAULT_LAYOUT_SEED: Final[int] = 20261017

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
        description="娶老婆的二次确认窗口（分钟），默认 3",
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
    waifu_graph_cmd: str = Field(
        default=DEFAULT_GRAPH_CMD,
        min_length=1,
        description="「老婆关系图」命令词；默认「老婆关系图」",
    )
    waifu_graph_max_edges: int = Field(
        default=DEFAULT_GRAPH_MAX_EDGES,
        ge=1,
        le=60,
        description="关系图最多画几条关系边，默认 60",
    )
    waifu_layout_solve: bool = Field(
        default=DEFAULT_LAYOUT_SOLVE,
        description=(
            "关系图是否做「槽位重排」（固定环几何、只搜谁坐哪个坑）："
            "关掉 = 历史落点序（逐像素一致）"
        ),
    )
    waifu_layout_solve_budget: float = Field(
        default=DEFAULT_LAYOUT_SOLVE_BUDGET,
        ge=0.0,
        le=600.0,
        description="首次槽位求解的秒预算，默认 8（0 = 不做搜索，直接用历史序）",
    )
    waifu_layout_refine_budget: float = Field(
        default=DEFAULT_LAYOUT_REFINE_BUDGET,
        ge=0.0,
        le=3600.0,
        description=(
            "后台精修槽位解的秒预算，默认 90（首次出图不等它，跑完覆盖缓存；"
            "只允许高于缓存里那条解的预算才覆盖；0 = 不派精修）"
        ),
    )
    waifu_layout_seed: int = Field(
        default=DEFAULT_LAYOUT_SEED,
        description="槽位求解器的随机种子，默认 20261017（同种子同参数 → 同一个解）",
    )

    waifu_max_partners: int = Field(
        default=1,
        ge=0,
        le=100,
        description="一个人最多能娶几个老婆；0 = 不限；默认 1",
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
    waifu_headline_template: str = Field(
        default=DEFAULT_HEADLINE_TEMPLATE,
        description=(
            "卡片首行文案模板，可用 {drawer} {partner} {user_id} 三个占位符；"
            "默认「群友」，生产覆盖为「群幼」"
        ),
    )
    #: 「指婚」白名单。⚠️ 键名是 **WIFE_SUPERUSERS**（用户 2026-10-14 点名，不是
    #: WAIFU_SUPERUSERS）：插件自带白名单，不依赖宿主 NoneBot 的 SUPERUSERS。
    wife_superusers: str = Field(
        default="",
        description=(
            "「指婚」白名单：允许使用指婚的超级管理员 QQ 号（逗号/空格分隔）；"
            "生产 = 1330509996"
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

    @field_validator("wife_superusers", mode="before")
    @classmethod
    def _accept_bare_qq_numbers(cls, v: Any) -> str:
        """`.env` 里写裸数字时 pydantic 收到的是 **int 不是 str** —— 转字符串。

        ⚠️ 2026-10-14 生产实证：`WIFE_SUPERUSERS=1330509996` →
        `ValidationError: input_type=int` → **整个插件起不来**（`Failed to import`）。
        配置写错绝不能炸插件 —— 与 `_accept_chinese_orientation` 同一类护栏。
        """
        if isinstance(v, str):
            return v
        if v is None or isinstance(v, bool):
            return ""
        if isinstance(v, int):
            return str(v)
        if isinstance(v, (list, tuple, set, frozenset)):
            return ",".join(str(x) for x in v)
        return str(v)

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
    "DEFAULT_GRAPH_CMD",
    "DEFAULT_GRAPH_MAX_EDGES",
    "DEFAULT_HEADLINE_TEMPLATE",
    "DEFAULT_LAYOUT_REFINE_BUDGET",
    "DEFAULT_LAYOUT_SEED",
    "DEFAULT_LAYOUT_SOLVE",
    "DEFAULT_LAYOUT_SOLVE_BUDGET",
    "DEFAULT_MEMBER_TTL_DAYS",
    "Config",
    "plugin_config",
]
