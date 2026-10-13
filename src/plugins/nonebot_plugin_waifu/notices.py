"""回执内容的**纯函数编译层**：业务状态 → `render.NoticeContext`。不碰 IO、不读配置。"""

from __future__ import annotations

from typing import Final

from .render import NoticeContext

#: 二次确认窗口的措辞（与 `WAIFU_PENDING_MINUTES` 拼成句）
WAIT_FMT: Final[str] = "等待 ta 回发确认（{minutes} 分钟内有效）"


def locked_notice(
    *,
    me_name: str,
    partner_id: int,
    partner_name: str,
    used: int,
    limit: int,
) -> NoticeContext:
    """A3：确认过之后再抽 → 「已经定人不能更换」，把状态/数据/下一步一次带全。"""
    return NoticeContext(
        title="今天已经定人",
        tone="deny",
        headline=f"{me_name}，你今天的老婆是 {partner_name}（{partner_id}）",
        avatar_user_id=partner_id,
        fields=(
            ("状态", "已确认，今天不能再换"),
            ("为什么", "确认即定死：再发「今日老婆」「换一只」都不会换人"),
            ("今日抽取", f"已用 {used}/{limit} 次"),
            ("影响范围", "只锁你自己，不影响别人"),
            ("恢复时间", "明天（跨天自动恢复）"),
        ),
        note="明天再发「今日老婆」就能重新抽",
    )


def quota_notice(*, used: int, limit: int) -> NoticeContext:
    """今日配额用尽。标题必须是用户逐字指定的原文（`rules.QUOTA_MESSAGE`）。"""
    from .rules import QUOTA_MESSAGE

    return NoticeContext(
        title=QUOTA_MESSAGE,
        tone="warn",
        headline=f"今天的 {limit} 次已经用完（已用 {used} 次）",
        fields=(
            ("今日已用", f"{used}/{limit} 次"),
            ("说明", "当天首次抽取与每次「换一只」都计一次"),
            ("「换一只」", "同样会被配额拦住"),
            ("恢复时间", "明天（跨天自动恢复）"),
        ),
        note="明天再来抽吧",
    )


def all_drawn_notice(*, drawn: int, candidates: int) -> NoticeContext:
    """今天群里的人已经抽遍。"""
    from .rules import ALL_DRAWN_MESSAGE

    return NoticeContext(
        title=ALL_DRAWN_MESSAGE,
        tone="warn",
        fields=(
            ("你今天抽到过", f"{drawn} 人"),
            ("群里可选", f"{candidates} 人"),
            ("说明", "同一天不会重复抽到同一个人"),
            ("恢复时间", "明天（跨天自动重置）"),
        ),
        note="明天群里的人会重新进池子",
    )


def empty_pool_notice() -> NoticeContext:
    """池子里**本来就没人**（区别于「今天已经抽遍」）—— 文案逐字保留。"""
    return NoticeContext(
        title="群里还没有能抽的人（要有人发过言才会进池子）",
        tone="warn",
        fields=(
            ("怎么进池子", "在群里发过言的人会自动进池子"),
            ("恢复时间", "有人发言之后立即可抽"),
        ),
        note="抽签只排除你自己和今天已经抽到过的人",
    )


__all__ = [
    "WAIT_FMT",
    "all_drawn_notice",
    "empty_pool_notice",
    "locked_notice",
    "quota_notice",
]
