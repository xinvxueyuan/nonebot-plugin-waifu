"""纯函数层：抽签、配额、性别判据、日期边界。

**全部不碰 IO、不读配置对象、不 import NoneBot** —— 这样每个分支都能用
确定性参数（注入 `rng` / `now`）单测。抽签逻辑只在生产跑到，写错了就是
「群里刷出一堆不合理结果」这种只能事后补救的事故，必须能测。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

#: 娶老婆/离婚的二次确认窗口：**3 分钟**（用户口径）。窗口外视为失效。
PENDING_WINDOW_SECONDS: Final[int] = 180

#: 配额用尽时的提示文案（**用户逐字指定，不要改字**）。
QUOTA_MESSAGE: Final[str] = "渣男，你今天没有老婆了！"

#: 当天群里的人都被我抽过一遍（去重把人都排除了）时的文案。
#: 与「池子本来就没人」区分开 —— 后者由 `notices.empty_pool_notice()` 编译。
ALL_DRAWN_MESSAGE: Final[str] = "今天群里的人你已经抽遍了，明天再来"

DEFAULT_TZ: Final[str] = "Asia/Shanghai"


@dataclass(frozen=True)
class Candidate:
    """抽取池里的一个候选。

    `sex` **通常是 `"unknown"`** —— 活跃池（`waifu_members`）里不存性别
    （否则每来一条群消息都得调一次 API）。只有 `orientation != all` 时，
    `handle._pick_eligible` 才会对被抽中的候选补查性别并复核。
    """

    user_id: int
    last_seen: float
    sex: str | None = None


@dataclass(frozen=True)
class DrawCheck:
    """能否抽取的判定结果。"""

    ok: bool
    reason: str = ""
    message: str = ""

    def as_denial(self) -> tuple[str, str]:
        return self.reason, self.message


def date_key(ts: float, *, tz_name: str = DEFAULT_TZ) -> str:
    """把 unix 秒转成「当地日期」字符串。

    ⚠️ 必须用配置时区而不是 UTC：跨天判定在 +08:00 下与 UTC 差 8 小时，
    用 UTC 会让「当天」在北京时间 08:00 才切换（用户会看到「今天的老婆」在早上变人）。
    """
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name)
    except Exception:
        # 时区库缺失/名字写错时退回 UTC，宁可差 8 小时也不要让功能整个挂掉。
        from datetime import timezone

        tz = timezone.utc
    return datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%d")


def can_draw(*, draws_used: int, daily_limit: int) -> DrawCheck:
    """能不能抽。**现在只剩配额一条判据**（用户 2026-10-14：离婚冷却取消）。

    Args:
        draws_used: 当日已用次数（含「今日老婆」的首次抽，用户口径：所有抽都计数）。
        daily_limit: 当日上限（配置 `WAIFU_DAILY_LIMIT`，默认 20）。
    """
    if draws_used >= daily_limit:
        return DrawCheck(False, reason="quota", message=QUOTA_MESSAGE)
    return DrawCheck(True)


def normalize_sex(raw: Any) -> str:
    """把各种来源的性别规范成 `male` / `female` / `unknown`。

    来源实测很脏：事件里的 `sender.sex` **恒为 None**（生产抽样 384/384），
    成员信息 API 才给 `male`/`female`/`unknown`。未知一律归 `unknown`。
    """
    s = str(raw or "").strip().casefold()
    if s in {"male", "m", "男"}:
        return "male"
    if s in {"female", "f", "女"}:
        return "female"
    return "unknown"


def is_eligible_gender(my_sex: str | None, cand_sex: str | None, *, mode: str) -> bool:
    """性别是否可抽（三档取向，用户 2026-10-10 拍板）。

    | mode | 中文 | 行为 |
    |---|---|---|
    | `all` | 所有 | **不限**（默认）—— 恒 True，不查性别 |
    | `normal` | 正常 | **只抽异性** |
    | `reverse` | 相反 | **只抽同性** |

    ⚠️ **硬规则：任一方性别未知（`unknown`/None）一律判为通过。**
    不能因为「查不到性别」就把人静默排除 —— 那会让某些群永远抽不出人，
    而且失败是**静默的**（群里只会看到「还没有能抽的人」，永远查不到原因）。
    """
    if mode not in {"normal", "reverse"}:
        return True  # all / 未知取值：不过滤
    me, cand = normalize_sex(my_sex), normalize_sex(cand_sex)
    if me == "unknown" or cand == "unknown":
        return True  # 任一方性别隐藏 → 判为通过
    same = me == cand
    return same if mode == "reverse" else not same


def pick_target(
    pool: Iterable[Candidate],
    *,
    me: int,
    my_sex: str | None,
    mode: str,
    excluded: set[int],
    rng: Callable[[Sequence[Candidate]], Candidate] | None = None,
) -> Candidate | None:
    """从池子里挑一个（**排除自己 / 今日已锁定者**，再过一遍取向筛）。

    ⚠️ 取向筛只在 `mode` 为 `normal`/`reverse` 时才有实际效果；`all`（默认）
    下 `is_eligible_gender` 恒 True —— 也就是**不查性别、不过滤**。

    ⚠️ **已婚者不再被排除**（用户 2026-10-14：已婚者照常抽、也照常被抽）。

    `rng` 接收**筛完的候选列表**并返回其一 —— 注入它就能确定性断言
    「排除逻辑对不对」，而不是靠随机碰运气。默认 `random.choice`。

    注意：`Candidate.sex` 可能仍是 `"unknown"`（池子里不存性别），
    所以真正的取向筛要在调用方补查性别后再做一次 —— 见 `handle._pick_eligible`。
    """
    cands = [
        c
        for c in pool
        if c.user_id != me
        and c.user_id not in excluded
        and is_eligible_gender(my_sex, c.sex, mode=mode)
    ]
    if not cands:
        return None
    if rng is not None:
        return rng(cands)
    import random

    return random.choice(cands)


def pending_alive(*, requested_at: float, now: float | None = None) -> bool:
    """该确认请求是否还在 3 分钟窗口内。"""
    ts = time.time() if now is None else now
    return (ts - requested_at) <= PENDING_WINDOW_SECONDS


__all__ = [
    "ALL_DRAWN_MESSAGE",
    "DEFAULT_TZ",
    "PENDING_WINDOW_SECONDS",
    "QUOTA_MESSAGE",
    "Candidate",
    "DrawCheck",
    "can_draw",
    "date_key",
    "is_eligible_gender",
    "normalize_sex",
    "pending_alive",
    "pick_target",
]
