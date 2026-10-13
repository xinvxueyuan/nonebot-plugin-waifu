"""今日老婆的五个命令。

命令语义（**用户逐条确认过，不要自行发挥**）：

    今日老婆 / 抽老婆   当天**首次**执行 → 抽取并锁定为「今日老婆」；
                        之后执行 → **只展示**当前老婆（不换人、不消耗次数）。
    换一只              **这个才是更换词**：每次都重新抽（消耗 1 次配额）；
                        但**今日已确认后禁止换**（锁定即定死，不消耗次数）。
    确认老婆            锁定今日老婆，**今天不能再用「换一只」换人**；**只锁自己**
                        （不锁对方），效果是「我之后抽签不会再抽到 ta」；每日恢复。
    娶老婆 @xxx||int      需**双方互相**发「娶老婆 @对方」；窗口 3 分钟；绑定后双方
                        不再参与他人抽取、也不能再抽群友。
    离婚 @xxx||int      同上；离婚后无冷却（用户 2026-10-14 取消，随离随抽）。

配额：一天每人最多 20 次，**所有「抽」都计数**（首次 + 每次换）。超限文案
「渣男，你今天没有老婆了！」是用户逐字指定的。

⚠️ **性别只用于渲染展示，不参与抽取筛选**（用户 2026-10-10 口径）。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import re
import time
from typing import TYPE_CHECKING, Final, Literal

from nonebot import on_command
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.params import CommandArg

from . import member_info, render, robot_filter, rules, store
from .config import DEFAULT_HEADLINE_TEMPLATE, plugin_config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    import sqlite3

logger = logging.getLogger("nonebot_plugin_waifu")

CMD_TODAY: Final[str] = "今日老婆"
CMD_CONFIRM: Final[str] = "确认老婆"
CMD_MARRY: Final[str] = "娶老婆"
CMD_DIVORCE: Final[str] = "离婚"

#: 「换老婆」命令词 —— **配置可变**（用户 2026-10-10：换一只是其可变配置，用于生产）。
#: 源码默认「换老婆」；生产 `.env.dev` 写 `WAIFU_CHANGE_CMD=换一只` 即切换。
CMD_CHANGE: Final[str] = plugin_config.waifu_change_cmd

#: 命令词单一定义处（注册响应器与插件元数据都引它）。
#: ⚠️ 含配置项，所以是**运行时**求值，不是字面量常量。
CMD_NAMES: Final[tuple[str, ...]] = (
    CMD_TODAY,
    CMD_CHANGE,
    CMD_CONFIRM,
    CMD_MARRY,
    CMD_DIVORCE,
)

PRIORITY: Final[int] = plugin_config.waifu_command_priority


def _today() -> str:
    return rules.date_key(time.time(), tz_name=rules.DEFAULT_TZ)


@dataclass(frozen=True)
class Action:
    """「该做什么」的判定结果（纯数据，便于逐格单测）。"""

    action: Literal["draw", "show", "deny"]
    reason: str = ""
    message: str = ""
    row: sqlite3.Row | None = None


def resolve_today_action(
    *,
    existing: sqlite3.Row | None,
    daily_limit: int,
    draws_used: int = 0,
) -> Action:
    """「今日老婆 / 抽老婆」该干什么。

    **已有记录 → 展示**（不换人、不计数）；没有 → 走配额判定后抽取。
    """
    if existing is not None:
        return Action(action="show", row=existing)
    check = rules.can_draw(draws_used=draws_used, daily_limit=daily_limit)
    if not check.ok:
        return Action(action="deny", reason=check.reason, message=check.message)
    return Action(action="draw")


def resolve_change_action(
    *,
    existing: sqlite3.Row | None,
    daily_limit: int,
    locked_today: bool,
) -> Action:
    """「换一只」该干什么：**今日已确认则拒绝**，否则永远走抽取（有记录也要换人），但要过配额。

    判定顺序：**先「已确认」→ 再配额**（用户 2026-10-11 口径：
    确认即定死，且不消耗次数 —— 所以拒绝要走「已确认」这一支，压在配额之前）。
    """
    if locked_today:
        return Action(
            action="deny", reason="confirmed", message=rules.CONFIRMED_MESSAGE
        )
    used = int(existing["draws_used"]) if existing is not None else 0
    check = rules.can_draw(draws_used=used, daily_limit=daily_limit)
    if not check.ok:
        return Action(action="deny", reason=check.reason, message=check.message)
    return Action(action="draw", row=existing)


async def _build_and_send(
    *,
    bot: Bot,
    event: GroupMessageEvent,
    partner_id: int,
    headline: str,
    footer: str | None = None,
) -> None:
    """取资料 → 渲染卡片 → 发送（**渲染失败回退纯文本**）。

    Args:
        bot: 当前群会话的 Bot 实例（取成员信息 / 发送消息）。
        event: 触发命令的群消息事件（取群号 / 抽取人）。
        partner_id: 被抽中者的 QQ 号（卡片正文对它）。
        headline: 卡片首行文案（`_headline()` 生成的成句文案）。
        footer: 卡片底部可选的补充文案（如「今日已用 N/上限 次」）。

    **结果消息不含任何 `@`**（用户 2026-10-10 要求关闭全部 `@`）：
    结果消息 = 卡片图（渲染不可用时为**纯文本**），
    **不 @、没有额外文案、不另发消息**。
    被抽中者**不会收到任何提示**（通知机制已整体关闭）。

    **发图不带引用段**：QQ 会把带 `reply` 的图片强制折叠成缩略图（实机反馈过）。
    定位成员用卡片正文的 QQ 号即可。
    """
    info = await member_info.fetch_member_info(
        bot, group_id=event.group_id, user_id=partner_id
    )
    row = store.get_member(event.group_id, partner_id)
    nick = (
        info["nickname"]
        or (str(row["nickname"]) if row is not None else "")
        or str(partner_id)
    )
    last_seen = float(row["last_seen"]) if row is not None else None
    ctx = render.CardContext(
        title=headline,
        mention=str(partner_id),
        user_id=partner_id,
        nickname=nick,
        sex=str(info["sex"]),
        last_seen_text=render.format_last_seen(last_seen),
        avatar_data_uri=await render.fetch_avatar_data_uri(partner_id),
    )
    png = await render.render_card(ctx, footer=footer)
    # 结果消息不含任何 @（用户 2026-10-10 要求关闭全部 @）：纯卡片图 / 纯文本。
    # ⚠️ **不另发消息、不写任何话术**。
    if png is not None:
        await bot.send_group_msg(
            group_id=event.group_id,
            message=Message(MessageSegment.image(png)),
        )
    else:
        fallback = render.build_fallback_text(ctx, footer=footer)
        await bot.send_group_msg(
            group_id=event.group_id,
            message=Message(MessageSegment.text(fallback)),
        )


async def _headline(bot: Bot, event: GroupMessageEvent, partner_id: int) -> str:
    """卡片首行文案（模板来自配置）：

        模板默认：{drawer}, 你今天的群友老婆是 {partner}（{user_id}）
        生产覆盖：{drawer}, 你今天的群幼老婆是 {partner}（{user_id}）

    - 模板来自 **`WAIFU_HEADLINE_TEMPLATE`**（配置可变，用户 2026-10-10 拍板）：
      源码默认「群友」，**生产 `.env.dev` 覆盖成「群幼」**。
    - `{drawer}` 取自 `event.sender`（`card` → `nickname` → QQ 号），**零 API 调用**。
    - `{partner}` / `{user_id}` 取自成员信息 —— 与卡片正文同源，保证两处一致。
    - 占位符写错由 `format_headline()` 兜住：warning + 回退默认模板，**不抛**。
    """
    sender = getattr(event, "sender", None)
    drawer = str(
        getattr(sender, "card", None)
        or getattr(sender, "nickname", None)
        or event.user_id
    )
    info = await member_info.fetch_member_info(
        bot, group_id=event.group_id, user_id=partner_id
    )
    row = store.get_member(event.group_id, partner_id)
    partner_nick = (
        info["nickname"]
        or (str(row["nickname"]) if row is not None else "")
        or str(partner_id)
    )
    return format_headline(
        plugin_config.waifu_headline_template,
        drawer=drawer,
        partner=partner_nick,
        user_id=partner_id,
    )


def format_headline(template: str, *, drawer: str, partner: str, user_id: int) -> str:
    """套用文案模板；**占位符写错不抛，回退默认模板**。

    配置是可变的（用户明确要求），所以必须能容错：
    `WAIFU_HEADLINE_TEMPLATE` 里多写一个 `{qq}` 会让 `str.format` 抛 `KeyError` ——
    如果让它冒出去，**整条命令就变成「没反应」**，而那明明是配置问题。
    这里 warning + 回退，群里照常出卡片，日志里有线索。
    """
    fmt = {"drawer": drawer, "partner": partner, "user_id": user_id}
    try:
        return str(template).format(**fmt)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning(
            "WAIFU_HEADLINE_TEMPLATE 占位符有问题（%s: %s），已回退默认模板",
            type(e).__name__,
            e,
        )
        return DEFAULT_HEADLINE_TEMPLATE.format(**fmt)


async def _pick_eligible(
    *,
    pool: list[rules.Candidate],
    me: int,
    my_sex: str | None,
    mode: str,
    excluded: set[int],
    married: set[int],
    fetch_sex: Callable[[int], Awaitable[str]],
    max_tries: int = 8,
) -> rules.Candidate | None:
    """按取向挑一个候选；**性别隐藏/取不到的一律判为通过**。

    - **`mode == "all"`（默认）时零 API 调用**：pool 里的 `sex` 都是 `"unknown"`，
      `is_eligible_gender(..., mode="all")` 恒 True，首轮即中、**不调 `fetch_sex`**。
      这正是用户说的「不需要判断性别」那种行为。
    - **`normal`/`reverse` 时最多试 `max_tries` 次**：抽中一个 → 补查它一个人的性别 →
      不满足取向就加进排除集再抽。**不做无限循环**（群里全是不满足取向的人时必须收敛到
      None 并给一句可读文案）。
    - `fetch_sex` 是**注入**的（`Callable[[int], Awaitable[str]]`），
      单测用字典查表即可，完全确定性、不碰网络。
    """
    tried = set(excluded)
    for _ in range(max_tries):
        cand = rules.pick_target(
            pool,
            me=me,
            my_sex=my_sex,
            mode=mode,
            excluded=tried,
            married=married,
        )
        if cand is None:
            return None
        if mode not in {"normal", "reverse"}:
            return cand  # 不筛：直接返回，一次 API 都不调
        cand_sex = await fetch_sex(cand.user_id)
        if rules.is_eligible_gender(my_sex, cand_sex, mode=mode):
            return rules.Candidate(
                user_id=cand.user_id, last_seen=cand.last_seen, sex=cand_sex
            )
        tried.add(cand.user_id)  # 取向不符：本轮跳过，继续抽
    return None


async def _do_draw(
    *,
    bot: Bot,
    event: GroupMessageEvent,
    redraw: bool,
) -> str | None:
    """抽一个人并落库。返回错误文案（None = 成功已发送）。

    卡片首行由 `_headline()` 现算（它需要被抽中者的昵称，抽之前拿不到）；
    结果消息不含任何 @（用户 2026-10-10 要求关闭全部 @），
    所以这里**不需要**动作名之类的参数。

    `redraw=True`（换一只）走 `resolve_change_action`，否则走 `resolve_today_action`。

    ⚠️ **抽取池不做任何性别筛选**（用户 2026-10-10 口径）：只排除
    自己 / 今日已锁定者 / **今日已抽到过的人** / 已婚者。性别只在下游渲染时展示 ——
    所以这里**没有**「抽中后查性别、不合适就重抽」的循环（那是已删除的死逻辑）。

    排除集 = `locked_partners`（确认锁）**并上** `drawn_partners`（当日抽取历史）——
    这样同一天内首次抽到的人 + 每次换一只抽到的人都不会再出现（用户 2026-10-11 口径）。
    """
    gid, me = event.group_id, int(event.user_id)
    today = _today()
    existing = store.get_daily(gid, me, today)

    if redraw:
        action = resolve_change_action(
            existing=existing,
            daily_limit=plugin_config.waifu_daily_limit,
            locked_today=store.has_lock(gid, me, today),
        )
    else:
        action = resolve_today_action(
            existing=existing,
            daily_limit=plugin_config.waifu_daily_limit,
            draws_used=int(existing["draws_used"]) if existing is not None else 0,
        )

    if action.action == "deny":
        return action.message

    if action.action == "show" and action.row is not None:
        await _build_and_send(
            bot=bot,
            event=event,
            partner_id=int(action.row["partner_id"]),
            headline=await _headline(bot, event, int(action.row["partner_id"])),
            footer="（今日已抽过；要换人发「" + plugin_config.waifu_change_cmd + "」）",
        )
        return None

    # 已婚者不能再抽
    if store.is_married(gid, me):
        return "你已经有老婆了，先「离婚」吧"

    mode = plugin_config.waifu_orientation
    pool = [
        rules.Candidate(
            user_id=int(r["user_id"]),
            last_seen=float(r["last_seen"]),
            # 池子里不存性别（否则每来一条群消息都得调 API）。
            # mode="all"（默认）时这个 "unknown" 根本不会被用到；
            # normal/reverse 时由 `_pick_eligible` 对被抽中者补查。
            sex="unknown",
        )
        for r in store.list_members(gid)
    ]

    # 我自己的性别：只在真的需要筛的时候查（mode=all 时省掉这次 API 调用）
    my_sex: str | None = None
    if mode in {"normal", "reverse"}:
        my_sex = (await member_info.fetch_member_info(bot, group_id=gid, user_id=me))[
            "sex"
        ]

    async def _fetch_sex(uid: int) -> str:
        """补查某个候选的性别（注入给 `_pick_eligible`，便于单测替换）。"""
        info = await member_info.fetch_member_info(bot, group_id=gid, user_id=uid)
        return str(info["sex"])

    # 已婚者不能再抽
    married = store.married_ids(gid)

    # 「抽遍了」与「池子本来就没人」要分清：在调用前先算一个**未去重**的可用候选数
    # （只排除自己 / 已婚者，**不**排除 locked/drawn）。为 0 才是真的没人。
    # 不为此多加数据库查询 —— 复用已读入的 pool 与上面算出的 married。
    raw_available = any(c.user_id != me and c.user_id not in married for c in pool)

    picked = await _pick_eligible(
        pool=pool,
        me=me,
        my_sex=my_sex,
        mode=mode,
        excluded=store.locked_partners(gid, me, today)
        | store.drawn_partners(gid, me, today),
        married=married,
        fetch_sex=_fetch_sex,
    )
    if picked is None:
        # 池子里有人、但今天都被我抽遍了 → 专门的「抽遍了」文案；
        # 池子本来就空 → 保留原文案（避免误报）。
        if raw_available:
            return rules.ALL_DRAWN_MESSAGE
        return "群里还没有能抽的人（要有人发过言才会进池子）"

    store.set_daily(group_id=gid, user_id=me, date_key=today, partner_id=picked.user_id)
    row = store.get_daily(gid, me, today)
    used = int(row["draws_used"]) if row is not None else 0
    footer = (
        f"今日已用 {used}/{plugin_config.waifu_daily_limit} 次"
        if plugin_config.waifu_show_quota_in_card
        else None
    )
    await _build_and_send(
        bot=bot,
        event=event,
        partner_id=picked.user_id,
        headline=await _headline(bot, event, picked.user_id),
        footer=footer,
    )
    return None


@dataclass(frozen=True)
class MarriageOutcome:
    """一次「娶老婆/离婚」命令的处理结果。"""

    bound: bool = False
    error: str = ""
    message: str = ""
    partner: int | None = None
    partners: tuple[int, ...] = ()


def _resolve_target(_event: GroupMessageEvent, args: Message) -> list[int]:
    """从命令参数里取**全部** QQ 号：`@某人`（可多个）或纯文本数字（空格/逗号分隔）。

    - `MessageSegment.at` 的 `data["qq"]` 可能是 `"all"`（@全体）→ **忽略**。
    - 纯文本里按「非数字字符」切分，接受多个 QQ 号（用户明确要求 `int` 等价）。
    - **去重、保持出现顺序**；一个都没有时返回**空列表**。

    向后兼容：单个 `@` 或单个数字仍然正常工作（返回单元素列表）。
    """
    out: list[int] = []
    for seg in args:
        if seg.type != "at":
            continue
        qq = str(seg.data.get("qq", "")).strip()
        if qq.isdigit():
            uid = int(qq)
            if uid not in out:
                out.append(uid)
    plain = args.extract_plain_text()
    for token in re.findall(r"\d+", plain):
        uid = int(token)
        if uid not in out:
            out.append(uid)
    return out


def _format_marry_message(targets: list[int], *, bound: bool) -> str:
    """按现有风格生成回执文案（多目标逐行列出；超过 5 个截断并说明）。"""
    minutes = plugin_config.waifu_pending_minutes
    if bound and len(targets) == 1:
        return "娶老婆成功！"
    lines = [
        f"已向 {t} 求婚，{minutes} 分钟内让 ta 回发「娶老婆 @你」就成" for t in targets
    ]
    if len(lines) > 5:
        extra = len(lines) - 5
        lines = lines[:5]
        lines.append(f"……等共 {extra + 5} 人（已省略后续）")
    return "\n".join(lines)


def apply_marry(
    *,
    group_id: int,
    requester: int,
    partner: int | None = None,
    partners: list[int] | None = None,
    now: float,
    max_partners: int = 1,
    robot_qqs: frozenset[int] = frozenset(),
) -> MarriageOutcome:
    """处理一次「娶老婆」，支持**一次 @ 多人**（贪婪参数）。

    语义（用户确认，2026-10-11 支持多配偶）：
    - 目标 = `partners`（多目标）或 `[partner]`（单目标，向后兼容）。
    - **机器人守卫**（2026-10-11，生产实证）：`robot_qqs` 里的目标一律拒娶，
      **整条拒绝、全有全无**。为保持本函数是**纯函数、易测**，机器人判定
      （需 await）由 `_h_marry` 异步算好后传入，这里只做集合判定 ——
      本函数自身**不 await 任何东西**。
    - **各算各的**：每个目标各发一次请求；谁先回发就绑定谁，互不影响。
    - **上限只查发起方**：`已有效配偶数 + 本次目标数 > max_partners` → **整条拒绝**
      （全有全无、一行不改）。`max_partners == 0` → **跳过上限判定**（不限）。
    - 自己 → 从目标里剔除（不能娶自己，不算错误）。
    - 对方已婚 → 那一人不可娶；若本次只有 ta 则整条拒绝，否则只跳过 ta。
    - 对方已发过给自己的请求且**窗口未过** → 绑定成功并清请求。
    - 其它 → 记录请求，回「等对方确认」。
    """
    targets = list(partners) if partners is not None else ([partner] if partner else [])
    # 去重 + 剔自己
    uniq: list[int] = []
    for t in targets:
        if t != requester and t not in uniq:
            uniq.append(t)

    if not uniq:
        return MarriageOutcome(error="不能和自己娶老婆")

    # 机器人守卫（与「不能娶自己」同一层：目标校验阶段）—— **整条拒绝、全有全无**
    hit_robots = [t for t in uniq if t in robot_qqs]
    if hit_robots:
        shown = hit_robots[:3]
        more = len(hit_robots) - len(shown)
        listed = "、".join(str(t) for t in shown) + (f" 等 {more} 个" if more else "")
        return MarriageOutcome(
            error=f"不能娶机器人当老婆（{listed}）",
            partners=tuple(uniq),
        )

    # 上限判定（只查发起方；0 = 不限）—— **整条拒绝、全有全无**
    if max_partners > 0:
        current = store.count_partners(group_id, requester)
        if current + len(uniq) > max_partners:
            return MarriageOutcome(
                error=(
                    f"你最多只能有 {max_partners} 个老婆"
                    f"（现在 {current} 个，本次要娶 {len(uniq)} 个），"
                    "先去「离婚」吧"
                ),
                partners=tuple(uniq),
            )

    # 单配（max=1）下保留「对方已婚则拒绝」（多配时对方可以已是别人的第 N 个）
    if max_partners == 1:
        for t in uniq:
            if store.is_married(group_id, t):
                return MarriageOutcome(error=f"{t} 已经有老婆了", partners=tuple(uniq))

    bound_to: int | None = None
    for t in uniq:
        reverse = store.get_pending(
            group_id, requester=t, partner=requester, kind="marry"
        )
        if reverse is not None:
            ts = float(reverse["requested_at"])
            if rules.pending_alive(requested_at=ts, now=now):
                store.marry(group_id=group_id, a=requester, b=t)
                store.clear_pending(
                    group_id, requester=t, partner=requester, kind="marry"
                )
                store.clear_pending(
                    group_id, requester=requester, partner=t, kind="marry"
                )
                if bound_to is None:
                    bound_to = t
                continue
            store.clear_pending(group_id, requester=t, partner=requester, kind="marry")
        store.set_pending(
            group_id=group_id,
            requester=requester,
            partner=t,
            kind="marry",
            requested_at=now,
        )

    outcome = len(uniq) == 1 and bound_to is not None
    return MarriageOutcome(
        bound=outcome,
        partner=bound_to or uniq[0],
        partners=tuple(uniq),
        message=_format_marry_message(uniq, bound=outcome),
    )


def apply_divorce(
    *, group_id: int, requester: int, partner: int, now: float
) -> MarriageOutcome:
    """处理一次「离婚 @partner」：同样**需双方互相发**；成功后无任何冷却。

    多配偶下：只能离**自己确实娶了的**那一位（`partner` 必须在 `list_partners` 里）。
    """
    if requester == partner:
        return MarriageOutcome(error="不能和自己离婚")
    my_partners = store.list_partners(group_id, requester)
    if not my_partners:
        return MarriageOutcome(error="你还没娶老婆")
    if partner not in my_partners:
        return MarriageOutcome(error=f"你的老婆是 {my_partners[0]}，不是 {partner}")

    reverse = store.get_pending(
        group_id, requester=partner, partner=requester, kind="divorce"
    )
    if reverse is not None:
        ts = float(reverse["requested_at"])
        if rules.pending_alive(requested_at=ts, now=now):
            store.divorce(group_id=group_id, a=requester, b=partner)
            store.clear_pending(
                group_id, requester=partner, partner=requester, kind="divorce"
            )
            store.clear_pending(
                group_id, requester=requester, partner=partner, kind="divorce"
            )
            return MarriageOutcome(
                bound=False,
                partner=partner,
                message="已离婚",
            )
        store.clear_pending(
            group_id, requester=partner, partner=requester, kind="divorce"
        )

    store.set_pending(
        group_id=group_id,
        requester=requester,
        partner=partner,
        kind="divorce",
        requested_at=now,
    )
    minutes = plugin_config.waifu_pending_minutes
    return MarriageOutcome(
        partner=partner,
        message=f"已申请离婚，{minutes} 分钟内让 ta 回发「离婚 @你」就生效",
    )


today_cmd = on_command(
    CMD_TODAY, aliases={"/今日老婆", "抽老婆", "/抽老婆"}, priority=PRIORITY, block=True
)
# 命令词来自配置 → 斜杠别名也按它拼，别写死字面量
change_cmd = on_command(
    CMD_CHANGE, aliases={f"/{CMD_CHANGE}"}, priority=PRIORITY, block=True
)
confirm_cmd = on_command(
    CMD_CONFIRM, aliases={"/确认老婆"}, priority=PRIORITY, block=True
)
marry_cmd = on_command(
    CMD_MARRY,
    # 「结婚」= 「娶老婆」同义词（用户 2026-10-11 拍板；生产实证：发「结婚 @某人」
    # 原本毫无反应 —— 命令只注册在「娶老婆」上）。**只加这一个词**，离婚不加别名。
    aliases={"结婚", "/结婚", "/娶老婆"},
    priority=PRIORITY,
    block=True,
)
divorce_cmd = on_command(CMD_DIVORCE, aliases={"/离婚"}, priority=PRIORITY, block=True)


@today_cmd.handle()
async def _h_today(bot: Bot, event: GroupMessageEvent) -> None:
    err = await _do_draw(bot=bot, event=event, redraw=False)
    if err is not None:
        await today_cmd.finish(
            MessageSegment.at(event.user_id) + MessageSegment.text(f"\n{err}")
        )


@change_cmd.handle()
async def _h_change(bot: Bot, event: GroupMessageEvent) -> None:
    err = await _do_draw(bot=bot, event=event, redraw=True)
    if err is not None:
        await change_cmd.finish(
            MessageSegment.at(event.user_id) + MessageSegment.text(f"\n{err}")
        )


@confirm_cmd.handle()
async def _h_confirm(_bot: Bot, event: GroupMessageEvent) -> None:
    """确认今天的抽取结果。

    ⚠️ **只锁自己**（用户 2026-10-10 明确）：锁存在独立的 `waifu_locked` 表，
    键 = (群, 用户, 日期)。效果是「**我在抽签时不会再把 ta 抽给我**」，但
    **别人仍然可能抽到 ta**。

    - **确认即定死**（用户 2026-10-11）：今天不能再发「换一只」换人
      （`resolve_change_action` 读 `store.has_lock` → 直接拒绝，不消耗次数）。
    - 一天一个：再确认一次会改为锁定**新的那位**（覆盖，不是累加）。
    - 跨天恢复：明天 date_key 变了，锁自然失效。
    """
    gid, me, today = event.group_id, int(event.user_id), _today()
    if not store.lock_daily(gid, me, today):
        await confirm_cmd.finish(
            MessageSegment.at(event.user_id)
            + MessageSegment.text("\n你还没抽今天的，先发「今日老婆」")
        )
    row = store.get_daily(gid, me, today)
    partner = int(row["partner_id"]) if row is not None else 0
    await confirm_cmd.finish(
        MessageSegment.at(event.user_id)
        + MessageSegment.text(
            f"\n已确认 {partner} 为今日老婆，"
            "今天不能再换（只锁你自己、不影响别人；明天恢复）"
        )
    )


@marry_cmd.handle()
async def _h_marry(
    bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    """「娶老婆」：**贪婪参数**（可一次 @ 多人），上限只查发起方。

    `args: Message = CommandArg()` 本身就是贪婪的（拿到命令后的全部消息），
    所以无需改签名 —— 关键是 `_resolve_target` 取**全部** at 段。

    **机器人守卫**（2026-10-11，生产实证）：拿到 `targets` 后先**并发**判定哪些
    是机器人（`robot_filter.is_robot`，含本机器人 `self_id`），再把命中的
    组成 `frozenset` 传给 `apply_marry(robot_qqs=...)` —— 保持它仍是纯函数。
    `bot` 形参现在真的被用到了（NoneBot 依赖注入**按注解匹配**，形参名可自由）。
    """
    targets = _resolve_target(event, args)
    if not targets:
        await marry_cmd.finish(
            MessageSegment.at(event.user_id)
            + MessageSegment.text(
                "\n用法：娶老婆 @某人（可一次 @ 多人，或 娶老婆 QQ号）"
            )
        )
    out = apply_marry(
        group_id=event.group_id,
        requester=int(event.user_id),
        partners=targets,
        now=time.time(),
        max_partners=plugin_config.waifu_max_partners,
        robot_qqs=frozenset(
            t
            for t, hit in zip(
                targets,
                await asyncio.gather(*(robot_filter.is_robot(bot, t) for t in targets)),
                strict=False,
            )
            if hit
        ),
    )
    text = out.error or out.message
    await marry_cmd.finish(
        MessageSegment.at(event.user_id) + MessageSegment.text(f"\n{text}")
    )


@divorce_cmd.handle()
async def _h_divorce(
    _bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()
) -> None:
    targets = _resolve_target(event, args)
    if not targets:
        await divorce_cmd.finish(
            MessageSegment.at(event.user_id)
            + MessageSegment.text("\n用法：离婚 @某人（或 离婚 QQ号）")
        )
    out = apply_divorce(
        group_id=event.group_id,
        requester=int(event.user_id),
        partner=targets[0],
        now=time.time(),
    )
    text = out.error or out.message
    await divorce_cmd.finish(
        MessageSegment.at(event.user_id) + MessageSegment.text(f"\n{text}")
    )


__all__ = [
    "CMD_CHANGE",
    "CMD_CONFIRM",
    "CMD_DIVORCE",
    "CMD_MARRY",
    "CMD_NAMES",
    "CMD_TODAY",
    "Action",
    "MarriageOutcome",
    "apply_divorce",
    "apply_marry",
    "change_cmd",
    "confirm_cmd",
    "divorce_cmd",
    "format_headline",
    "marry_cmd",
    "resolve_change_action",
    "resolve_today_action",
    "today_cmd",
]
