"""今日老婆的持久化层（stdlib sqlite3，零新依赖）。

## 为什么用 sqlite 而不是 ORM

本插件只有 5 张小表、没有迁移历史需求。用 `nonebot-plugin-orm` 会引入 alembic 迁移文件
（revision 必须在**进程内跨插件全局唯一**，历史上已被这块坑过），而 qbot 宿主还设了
`ALEMBIC_STARTUP_CHECK=false` —— 那条路径会在启动时 `migrate.sync` 并 **DROP 掉本进程
没有模型的表**。纯 stdlib 的独立库文件完全不碰这套机制，最省事也最安全。

## 为什么库路径要走 localstore 的**显式命名** API

用 `localstore.get_data_file("<插件名>", "<文件>")`，**不要**用
`get_plugin_data_file()` —— 后者靠**调用栈**猜是哪个插件
（`_try_get_caller_plugin`），猜不中就抛
`RuntimeError("Cannot detect caller plugin")`。在启动钩子里栈更绕，
一旦猜不中就会被兜底吞掉，表现成「容灾静默失效」，最难查。

## 表只加不改（唯一例外：`waifu_marriage`）

`init()` 每次都会 `executescript(_SCHEMA)`，全部是 `CREATE TABLE IF NOT EXISTS`：
新建表是安全的（不动已有数据），但**绝不要**在这里写 DROP / ALTER。

**唯一例外是 `waifu_marriage`**：它需要 `(group_id, user_id, partner_id)` 三列主键
才能支持「一个人多配偶」，而 `CREATE TABLE IF NOT EXISTS` 不会改动已存在的表。
所以 `init()` 里额外跑一个**幂等的重建步骤** `_ensure_marriage_schema()`：
检测现有主键列，若不是三列主键 → **先断言表内行数为 0**（安全护栏）→
`DROP TABLE` + 按新 schema 重建；**行数不为 0 就绝不重建**，只记一条 warning。

⚠️ 除 `waifu_marriage` 外，其它表**依然只加不改**。
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger("nonebot_plugin_waifu")

_T_MEMBERS = "waifu_members"
_T_DAILY = "waifu_daily"
_T_MARRIAGE = "waifu_marriage"
_T_PENDING = "waifu_pending"
_T_CD = "waifu_divorce_cd"
_T_LOCK = "waifu_locked"
_T_DRAWN = "waifu_drawn"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_T_MEMBERS} (
    group_id  INTEGER NOT NULL,
    user_id   INTEGER NOT NULL,
    nickname  TEXT    NOT NULL DEFAULT '',
    last_seen REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS {_T_DAILY} (
    group_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    date_key   TEXT    NOT NULL,
    partner_id INTEGER NOT NULL,
    locked     INTEGER NOT NULL DEFAULT 0,
    draws_used INTEGER NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id, date_key)
);

CREATE TABLE IF NOT EXISTS {_T_MARRIAGE} (
    group_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    partner_id INTEGER NOT NULL,
    status     TEXT    NOT NULL,
    since      REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id, partner_id)
);

CREATE TABLE IF NOT EXISTS {_T_PENDING} (
    group_id     INTEGER NOT NULL,
    requester    INTEGER NOT NULL,
    partner      INTEGER NOT NULL,
    kind         TEXT    NOT NULL,
    requested_at REAL    NOT NULL,
    PRIMARY KEY (group_id, kind, requester, partner)
);

CREATE TABLE IF NOT EXISTS {_T_CD} (
    group_id INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    until    REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id)
);

CREATE TABLE IF NOT EXISTS {_T_LOCK} (
    group_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    date_key   TEXT    NOT NULL,
    partner_id INTEGER NOT NULL,
    created_at REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id, date_key)
);

CREATE TABLE IF NOT EXISTS {_T_DRAWN} (
    group_id   INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    date_key   TEXT    NOT NULL,
    partner_id INTEGER NOT NULL,
    drawn_at   REAL    NOT NULL,
    PRIMARY KEY (group_id, user_id, date_key, partner_id)
);
"""

_db_path: Path | None = None


def default_db_path() -> Path:
    """生产路径：localstore 的插件数据目录（只在 NoneBot 初始化后调用）。"""
    import nonebot_plugin_localstore as localstore

    return localstore.get_data_file("nonebot_plugin_waifu", "waifu.sqlite3")


def init(path: Path) -> None:
    """指定库文件并建表。启动钩子与测试都走这里。"""
    global _db_path
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _db_path = path
    with _conn() as conn:
        conn.executescript(_SCHEMA)
        _ensure_marriage_schema(conn)


def _ensure_marriage_schema(conn: sqlite3.Connection) -> None:
    """把旧的 `waifu_marriage`（两列主键）**幂等重建**成三列主键。

    `CREATE TABLE IF NOT EXISTS` 不会改动已存在的表，所以老库升级后仍会停在
    两列主键（物理上只能存一个配偶）。这里显式检测主键：

    - 已经是 `(group_id, user_id, partner_id)` → 什么都不做（幂等）。
    - 还是旧主键 → **先断言表内行数为 0**（安全护栏）；
      - 0 行 → `DROP TABLE` + 按新 schema 重建；
      - 非 0 行 → **不重建**，记一条 warning（不冒丢数据的风险）。

    ⚠️ 这是本模块**唯一**允许对已有表做 DROP 的地方，且被零行护栏兜住。
    """
    rows = conn.execute(f"PRAGMA table_info({_T_MARRIAGE})").fetchall()
    if not rows:  # 表还不存在：executescript 已按新 schema 建好
        return
    pk_cols = tuple(
        str(r["name"])
        for r in sorted(rows, key=lambda r: int(r["pk"]))
        if int(r["pk"]) > 0
    )
    if pk_cols == ("group_id", "user_id", "partner_id"):
        return

    n = int(conn.execute(f"SELECT COUNT(*) AS n FROM {_T_MARRIAGE}").fetchone()["n"])
    if n != 0:
        logger.warning(
            "%s 主键仍是旧的两列 %s 且表内有 %d 行 —— **不重建**"
            "（重建会有丢数据风险，请先人工清空或迁移）",
            _T_MARRIAGE,
            pk_cols,
            n,
        )
        return

    logger.warning("检测到 %s 旧主键 %s 且零行，重建为三列主键", _T_MARRIAGE, pk_cols)
    conn.execute(f"DROP TABLE {_T_MARRIAGE}")
    conn.executescript(_SCHEMA)


def is_initialized() -> bool:
    return _db_path is not None


@contextlib.contextmanager
def _conn() -> Generator[sqlite3.Connection]:
    if _db_path is None:
        msg = "store.init() 还没调用"
        raise RuntimeError(msg)
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def touch_member(*, group_id: int, user_id: int, nickname: str) -> None:
    """记录/刷新一个成员的活跃时间（监听群消息时调用）。

    昵称**不用空串覆盖**已有值：取不到昵称时（`card`/`nickname` 都空）保留旧的好值，
    否则一次异常就会把池子里的昵称全洗成空。
    """
    now = time.time()
    nick = (nickname or "").strip()
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO {_T_MEMBERS} (group_id, user_id, nickname, last_seen)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (group_id, user_id) DO UPDATE SET"
            "   last_seen = excluded.last_seen,"
            f"   nickname = CASE WHEN excluded.nickname <> ''"
            f"        THEN excluded.nickname ELSE {_T_MEMBERS}.nickname END",
            (group_id, user_id, nick, now),
        )


def list_members(group_id: int) -> list[sqlite3.Row]:
    """某群全部成员（按最后发言时间倒序 —— 越活跃越靠前，便于人工核对）。"""
    with _conn() as conn:
        return conn.execute(
            f"SELECT group_id, user_id, nickname, last_seen FROM {_T_MEMBERS}"
            " WHERE group_id = ? ORDER BY last_seen DESC",
            (group_id,),
        ).fetchall()


def get_member(group_id: int, user_id: int) -> sqlite3.Row | None:
    """取单个成员；不在池子里返回 None。"""
    with _conn() as conn:
        return conn.execute(
            f"SELECT group_id, user_id, nickname, last_seen FROM {_T_MEMBERS}"
            " WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()


def count_members(group_id: int) -> int:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_T_MEMBERS} WHERE group_id = ?", (group_id,)
        ).fetchone()
        return int(row["n"]) if row else 0


def set_daily(*, group_id: int, user_id: int, date_key: str, partner_id: int) -> None:
    """写入/更新当日老婆记录，**并顺手记一笔当日抽取历史**。

    `draws_used` 在**已有记录**时 `+1`（换一只累加计数，用户口径：所有抽都计数）；
    首次写入时置 1（那次抽取本身算一次）。

    旧列 `locked` 被重置为 0 —— 这是安全的：确认锁**不在这一行**，而是存在
    `waifu_locked` 表（键 = 群+用户+日期），抽取排除集只读那张表，所以重抽
    （换一只 / 今日老婆）不会再把「最近一次确认」冲掉。这里的 `locked` 仅作
    「最近一次确认」的镜像，供 `get_daily` 的消费方与测试查看。

    本函数是「成功抽到一个人」的**唯一落库入口**（首次抽取与换一只都走它），
    所以在这里调用 `record_drawn` 记当日历史 —— 保证**不可能漏写**，
    从而让「同一天不重复抽到同一人」的排除集总有完整数据。
    """
    now = time.time()
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO {_T_DAILY}"
            " (group_id, user_id, date_key, partner_id, locked, draws_used, updated_at)"
            " VALUES (?, ?, ?, ?, 0, 1, ?)"
            " ON CONFLICT (group_id, user_id, date_key) DO UPDATE SET"
            "   partner_id = excluded.partner_id,"
            "   locked = 0,"
            "   draws_used ="
            f"     {_T_DAILY}.draws_used + 1,"
            "   updated_at = excluded.updated_at",
            (group_id, user_id, date_key, partner_id, now),
        )
    record_drawn(
        group_id=group_id, user_id=user_id, date_key=date_key, partner_id=partner_id
    )


def record_drawn(
    *, group_id: int, user_id: int, date_key: str, partner_id: int
) -> None:
    """记一笔「我今天抽到过 ta」（幂等）。

    用 `INSERT OR IGNORE`：主键含 `partner_id`，同一天同一个人重复写**不报错**、
    只留一行（换一只正好抽回同一个人时不会撞主键炸掉整个命令）。
    """
    with _conn() as conn:
        conn.execute(
            f"INSERT OR IGNORE INTO {_T_DRAWN}"
            " (group_id, user_id, date_key, partner_id, drawn_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (group_id, user_id, date_key, partner_id, time.time()),
        )


def drawn_partners(group_id: int, user_id: int, date_key: str) -> set[int]:
    """我今天已经抽到过的人（首次抽取 + 所有换一只的**累积**）。

    带 `user_id` 过滤 —— **只影响我自己**（与 `locked_partners` 一致）：
    我抽到过 X，别人抽签时 X 仍是候选。跨天自然失效（date_key 变了即为空集）。
    """
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT partner_id FROM {_T_DRAWN}"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (group_id, user_id, date_key),
        ).fetchall()
        return {int(r["partner_id"]) for r in rows}


def get_daily(group_id: int, user_id: int, date_key: str) -> sqlite3.Row | None:
    """取某人当天记录；没有返回 None（= 今天还没抽过）。"""
    with _conn() as conn:
        return conn.execute(
            f"SELECT group_id, user_id, date_key, partner_id, locked, draws_used,"
            f" updated_at FROM {_T_DAILY}"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (group_id, user_id, date_key),
        ).fetchone()


def lock_daily(group_id: int, user_id: int, date_key: str) -> bool:
    """把**我**当日记录确认掉 → True；没有当日记录 → False（原有语义不变）。

    确认效果写进独立的 `waifu_locked` 表（键 = 群+用户+日期），值 = 我当日
    `waifu_daily.partner_id`；**同时**继续把 `waifu_daily.locked = 1` 作为
    「最近一次确认」的镜像（供 `get_daily` 的消费方与既有测试查看）。

    锁绑的是 (群, 用户, 日期) 这一**主体**而不是 `waifu_daily` 那一行，所以
    换一只时 `set_daily` 清零旧列 `locked` 也不会把确认冲掉。一天只能锁一个：
    新表主键含 date_key，再确认一次是**覆盖**而不是累加。
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT partner_id FROM {_T_DAILY}"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (group_id, user_id, date_key),
        ).fetchone()
        if row is None:
            return False
        partner_id = int(row["partner_id"])
        now = time.time()
        conn.execute(
            f"INSERT INTO {_T_LOCK}"
            " (group_id, user_id, date_key, partner_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (group_id, user_id, date_key) DO UPDATE SET"
            "   partner_id = excluded.partner_id,"
            "   created_at = excluded.created_at",
            (group_id, user_id, date_key, partner_id, now),
        )
        conn.execute(
            f"UPDATE {_T_DAILY} SET locked = 1, updated_at = ?"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (now, group_id, user_id, date_key),
        )
        return True


def locked_partners(group_id: int, user_id: int, date_key: str) -> set[int]:
    """**我**今天确认过的人（只影响我自己；别人不受影响）。

    读 `waifu_locked` 并带 `user_id` 过滤 —— 这是「锁只对我生效」的关键：
    我确认了 X，别人抽签时 X 仍是候选。不再用 `waifu_daily.locked` 作排除源
    （它是全局的，会让别人也抽不到 X）。
    """
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT partner_id FROM {_T_LOCK}"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (group_id, user_id, date_key),
        ).fetchall()
        return {int(r["partner_id"]) for r in rows}


def has_lock(group_id: int, user_id: int, date_key: str) -> bool:
    """我今天是否已「确认老婆」（= `waifu_locked` 里有行）。

    「换一只」据此判定能否再换（用户 2026-10-11：确认即定死，不再消耗次数）。
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT 1 FROM {_T_LOCK}"
            " WHERE group_id = ? AND user_id = ? AND date_key = ? LIMIT 1",
            (group_id, user_id, date_key),
        ).fetchone()
        return row is not None


def get_locked_partner(group_id: int, user_id: int, date_key: str) -> int | None:
    """我当天「确认老婆」的对象；没确认过 → None。

    ⚠️ 这里的 SQL 文本**刻意**与 `locked_partners` 写得不一样（WHERE 少一行）——
    否则 `scripts/mutation_check.py` 里 M18 的三行锚点会命中**两处**，
    脚本对「多命中」按跳过处理 → 整个门禁判失败。
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT partner_id FROM {_T_LOCK} WHERE group_id = ?"
            " AND user_id = ? AND date_key = ?",
            (group_id, user_id, date_key),
        ).fetchone()
        return int(row["partner_id"]) if row else None


def marry(*, group_id: int, a: int, b: int) -> None:
    """绑定婚姻：**同一事务**写入双向两行（**只处理 a、b 这一对**）。

    为什么存两行而不是一行 + 方向判断：主键是 `(group_id, user_id, partner_id)`，
    「查某人的配偶」是一行等值查询，调用方不需要知道谁是 a 谁是 b。
    代价是写入必须原子 —— 少写一行就会出现「A 已婚、B 单身」的脏状态。

    主键含 `partner_id`（2026-10-11 改为多配偶），所以 `ON CONFLICT` 用三列。
    这**不会**清掉 a/b 已有的其它配偶行 —— 各对互相独立。
    """
    now = time.time()
    with _conn() as conn:
        for me, other in ((a, b), (b, a)):
            conn.execute(
                f"INSERT INTO {_T_MARRIAGE}"
                " (group_id, user_id, partner_id, status, since)"
                " VALUES (?, ?, ?, 'married', ?)"
                " ON CONFLICT (group_id, user_id, partner_id) DO UPDATE SET"
                "   status = excluded.status,"
                "   since = excluded.since",
                (group_id, me, other, now),
            )


def divorce(*, group_id: int, a: int, b: int) -> None:
    """解除婚姻：同事务删除**这一对**的双向两行。

    ⚠️ 只删 a、b 之间的两行 —— **不能**用 `user_id IN (?, ?)` 删光，
    否则会把 a 与 b 各自在别处的配偶关系一并抹掉（多配偶下的核心回归）。
    """
    with _conn() as conn:
        conn.execute(
            f"DELETE FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND partner_id = ?",
            (group_id, a, b),
        )
        conn.execute(
            f"DELETE FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND partner_id = ?",
            (group_id, b, a),
        )


def list_partners(group_id: int, user_id: int) -> list[int]:
    """某人的全部配偶 QQ 号（按 since 升序，先娶的在前）；未婚返回空列表。"""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT partner_id FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND status = 'married'"
            " ORDER BY since ASC, partner_id ASC",
            (group_id, user_id),
        ).fetchall()
        return [int(r["partner_id"]) for r in rows]


def count_partners(group_id: int, user_id: int) -> int:
    """某人的配偶数量（用于上限判定）。"""
    with _conn() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND status = 'married'",
            (group_id, user_id),
        ).fetchone()
        return int(row["n"]) if row else 0


def get_partner(group_id: int, user_id: int) -> int | None:
    """取配偶 QQ 号（**多配偶下返回最先娶的那一个**）；未婚返回 None。

    保留单值语义以兼容既有调用方；需要全部配偶时用 `list_partners`。
    """
    with _conn() as conn:
        row = conn.execute(
            f"SELECT partner_id FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND status = 'married'"
            " ORDER BY since ASC, partner_id ASC LIMIT 1",
            (group_id, user_id),
        ).fetchone()
        return int(row["partner_id"]) if row else None


def is_married(group_id: int, user_id: int) -> bool:
    """这个人是否已有任何配偶。"""
    return get_partner(group_id, user_id) is not None


def set_pending(
    *,
    group_id: int,
    requester: int,
    partner: int,
    kind: str,
    requested_at: float,
) -> None:
    """记下一个待对方确认的娶老婆/离婚请求（**有方向**：A→B 与 B→A 是两条）。"""
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO {_T_PENDING}"
            " (group_id, requester, partner, kind, requested_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (group_id, kind, requester, partner) DO UPDATE SET"
            "   requested_at = excluded.requested_at",
            (group_id, requester, partner, kind, requested_at),
        )


def get_pending(
    group_id: int, *, requester: int, partner: int, kind: str
) -> sqlite3.Row | None:
    """取一条待确认请求；没有返回 None（调用方再用 `rules.pending_alive` 判窗口）。"""
    with _conn() as conn:
        return conn.execute(
            f"SELECT group_id, requester, partner, kind, requested_at FROM {_T_PENDING}"
            " WHERE group_id = ? AND requester = ? AND partner = ? AND kind = ?",
            (group_id, requester, partner, kind),
        ).fetchone()


def clear_pending(group_id: int, *, requester: int, partner: int, kind: str) -> None:
    with _conn() as conn:
        conn.execute(
            f"DELETE FROM {_T_PENDING}"
            " WHERE group_id = ? AND requester = ? AND partner = ? AND kind = ?",
            (group_id, requester, partner, kind),
        )


def purge_pending_before(*, now: float, window: float) -> int:
    """删掉窗口已过的请求，返回删除条数（避免表无限增长）。"""
    cutoff = now - window
    with _conn() as conn:
        cur = conn.execute(
            f"DELETE FROM {_T_PENDING} WHERE requested_at < ?", (cutoff,)
        )
        return int(cur.rowcount)


__all__ = [
    "clear_pending",
    "count_members",
    "count_partners",
    "default_db_path",
    "divorce",
    "drawn_partners",
    "get_daily",
    "get_locked_partner",
    "get_member",
    "get_partner",
    "get_pending",
    "has_lock",
    "init",
    "is_initialized",
    "is_married",
    "list_members",
    "list_partners",
    "lock_daily",
    "locked_partners",
    "marry",
    "purge_pending_before",
    "record_drawn",
    "set_daily",
    "set_pending",
    "touch_member",
]
