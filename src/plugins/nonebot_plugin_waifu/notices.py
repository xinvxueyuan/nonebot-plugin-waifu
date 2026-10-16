"""回执内容的**纯函数编译层**：业务状态 → `render.NoticeContext`。不碰 IO、不读配置。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from .render import NoticeContext, NoticeItem

if TYPE_CHECKING:
    from collections.abc import Sequence

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


def today_gone_notice(*, used: int, limit: int) -> NoticeContext:
    """次数用尽后再看「今日老婆」：当天最后抽到的那位**已经不算数了**。

    用户 2026-10-17 口径：被配额挡下的那一刻起，最后抽到的那位就不算了
    （关系图上也不再画成「喜欢」），直到他重新抽到新人 —— 跨天没抽也算那天。

    ⚠️ 文案要求（用户原话）：「文案合适一点，抹去之类的技术词不要出现」——
    别写「抹去 / 作废 / 失效 / 登记」这类词。
    """
    return NoticeContext(
        title="今天的老婆飞走了",
        tone="warn",
        headline=f"次数用尽（{used}/{limit}），最后抽到的那位已经不算数了",
        fields=(
            ("为什么", "次数用尽还接着抽，最后一只就不算了"),
            ("现在", "今天没有老婆了"),
            ("什么时候能再抽", "明天（跨天自动恢复）"),
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


def confirm_notice(
    *,
    me_name: str,
    partner_id: int,
    partner_name: str,
    last_seen_text: str,
    used: int,
    limit: int,
) -> NoticeContext:
    """「确认老婆」成功回执（带当事人资料与锁定范围）。"""
    return NoticeContext(
        title="已确认今日老婆",
        tone="ok",
        headline=f"{me_name}，你今天的老婆是 {partner_name}",
        avatar_user_id=partner_id,
        fields=(
            ("昵称", partner_name),
            ("QQ号", str(partner_id)),
            ("最后发言", last_seen_text),
            ("状态", "已定人：今天不能再换"),
            ("影响范围", "只锁你自己（别人仍可能抽到 ta）"),
            ("今日抽取", f"已用 {used}/{limit} 次"),
        ),
        note="明天恢复抽卡",
    )


def marry_notice(
    *,
    requester_name: str,
    results: Sequence[tuple[int, str]],
    minutes: int,
    my_count: int,
    cap: int,
    failed: str = "",
) -> NoticeContext:
    """「娶老婆」回执：**每个对象一行**（可一次 @ 多人）；`failed` 是整体性错误。"""
    cap_text = f"{my_count}/{cap}" if cap > 0 else f"{my_count}（不限）"
    return NoticeContext(
        title="娶老婆" if not failed else "娶老婆没成功",
        tone="deny" if failed else "ok",
        headline=failed or f"{requester_name} 的请求已登记",
        items=tuple(NoticeItem(uid, line) for uid, line in results),
        fields=(
            ("确认方式", f"对方回发「娶老婆 @{requester_name}」即绑定"),
            ("等待窗口", f"{minutes} 分钟内有效"),
            ("你的老婆数", cap_text),
        ),
        note="超过上限、或对方不同意时不会绑定",
    )


def divorce_notice(
    *,
    me_name: str,
    partner_id: int,
    partner_name: str,
    failed: str = "",
) -> NoticeContext:
    """「离婚」回执（已生效 / 没成功两种）。

    **单人即时生效**（用户 2026-10-14）：没有「申请中」态、不需要对方回发；
    成功回执带上**双方昵称**（发起人 + 被离的人）。
    """
    return NoticeContext(
        title="离婚没成功" if failed else "已离婚",
        tone="deny" if failed else "ok",
        headline=failed or f"{me_name} 与 {partner_name}（{partner_id}）已离婚",
        avatar_user_id=partner_id,
        items=(
            NoticeItem(
                partner_id,
                # 失败时这一行写**原因**：写「关系已解除」会与「离婚没成功」自相矛盾。
                failed or "关系已解除",
            ),
        ),
        fields=(
            ("生效方式", "单人即可离婚，无需对方同意"),
            ("离婚之后", "可以直接再娶（本插件没有冷却）"),
        ),
        note="离婚只解除这一对，其它配偶关系不受影响",
    )


def arrange_notice(
    *,
    head_name: str,
    head_id: int,
    results: Sequence[tuple[int, str]],
    failed: str = "",
) -> NoticeContext:
    """「指婚」回执（**超管专用**）：每个对象一行；`failed` 是整体性错误。

    与「娶老婆」回执的区别：没有确认方式/等待窗口 —— 指婚是**当场生效**的。
    """
    return NoticeContext(
        title="指婚没成功" if failed else "指婚完成",
        tone="deny" if failed else "ok",
        headline=failed or f"{head_name} 的婚姻已由超管直接绑定",
        avatar_user_id=head_id,
        fields=(("生效方式", "超管指婚：视为 a 发起、b/c 同意，无需回发确认"),),
        items=tuple(NoticeItem(uid, line) for uid, line in results),
        note="指婚不看配偶上限、也不挡机器人；要解除用「离婚 @某人」",
    )


def usage_notice(*, cmd: str, lines: Sequence[str]) -> NoticeContext:
    """用法提示（不是错误，是「怎么用」）。"""
    return NoticeContext(
        title=f"「{cmd}」怎么用",
        tone="info",
        fields=tuple(("步骤", line) for line in lines),
        note="本插件的回复一律以图片呈现，避免在群里被刷掉",
    )


def graph_empty_notice(*, members: int) -> NoticeContext:
    """关系图空状态：本群还没有任何关系。"""
    return NoticeContext(
        title="本群还没有老婆关系",
        tone="info",
        fields=(
            ("成员池", f"{members} 人（在群里发过言的都算）"),
            ("图上画什么", "夫妻（无向）· 倾心/喜欢（有向）；只画已经发生的关系"),
        ),
        note="「娶老婆 @某人」成为夫妻 · 「确认老婆」成为倾心 · 每日抽取即「喜欢」",
    )


def no_relations_notice(*, focus_id: int) -> NoticeContext:
    """@了人，但 ta 还没有任何关系。"""
    return NoticeContext(
        title="ta 还没有任何老婆关系",
        tone="info",
        avatar_user_id=focus_id,
        fields=(("说明", "既没有夫妻、也没有倾心、也没有喜欢"),),
        note="等 ta 抽一次（抽取即「喜欢」）、「确认老婆」，或「娶老婆 @ta」之后再看",
    )


__all__ = [
    "WAIT_FMT",
    "all_drawn_notice",
    "confirm_notice",
    "divorce_notice",
    "empty_pool_notice",
    "graph_empty_notice",
    "locked_notice",
    "marry_notice",
    "no_relations_notice",
    "quota_notice",
    "usage_notice",
]
