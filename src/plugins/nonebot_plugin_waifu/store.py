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

## 表只加不改

`init()` 每次都会 `executescript(_SCHEMA)`，全部是 `CREATE TABLE IF NOT EXISTS`：
新建表是安全的（不动已有数据），但**绝不要**在这里写 DROP / ALTER。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
import sqlite3
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

_T_MEMBERS = "waifu_members"
_T_DAILY = "waifu_daily"
_T_MARRIAGE = "waifu_marriage"
_T_PENDING = "waifu_pending"
_T_CD = "waifu_divorce_cd"

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
    PRIMARY KEY (group_id, user_id)
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
    """写入/更新当日老婆记录。

    `draws_used` 在**已有记录**时 `+1`（换一只累加计数，用户口径：所有抽都计数）；
    首次写入时置 1（那次抽取本身算一次）。
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
    """把当日记录标记为「已确认」→ True；没有记录 → False。

    确认后该 partner 会被 `locked_partners()` 返回，从而**从抽取池里移除**。
    """
    with _conn() as conn:
        cur = conn.execute(
            f"UPDATE {_T_DAILY} SET locked = 1, updated_at = ?"
            " WHERE group_id = ? AND user_id = ? AND date_key = ?",
            (time.time(), group_id, user_id, date_key),
        )
        return cur.rowcount > 0


def locked_partners(group_id: int, date_key: str) -> set[int]:
    """当天所有已确认（locked=1）的 partner 的 QQ 号集合。"""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT partner_id FROM {_T_DAILY}"
            " WHERE group_id = ? AND date_key = ? AND locked = 1",
            (group_id, date_key),
        ).fetchall()
        return {int(r["partner_id"]) for r in rows}


def marry(*, group_id: int, a: int, b: int) -> None:
    """绑定婚姻：**同一事务**写入双向两行。

    为什么存两行而不是一行 + 方向判断：`(group_id, user_id)` 作主键后
    「查某人的配偶」永远是一行等值查询，调用方不需要知道谁是 a 谁是 b。
    代价是写入必须原子 —— 少写一行就会出现「A 已婚、B 单身」的脏状态。
    """
    now = time.time()
    with _conn() as conn:
        for me, other in ((a, b), (b, a)):
            conn.execute(
                f"INSERT INTO {_T_MARRIAGE}"
                " (group_id, user_id, partner_id, status, since)"
                " VALUES (?, ?, ?, 'married', ?)"
                " ON CONFLICT (group_id, user_id) DO UPDATE SET"
                "   partner_id = excluded.partner_id,"
                "   status = excluded.status,"
                "   since = excluded.since",
                (group_id, me, other, now),
            )


def divorce(*, group_id: int, a: int, b: int) -> None:
    """解除婚姻：同事务删除双向两行（保留冷却由 `set_divorce_cd` 另行负责）。"""
    with _conn() as conn:
        conn.execute(
            f"DELETE FROM {_T_MARRIAGE} WHERE group_id = ? AND user_id IN (?, ?)",
            (group_id, a, b),
        )


def get_partner(group_id: int, user_id: int) -> int | None:
    """取配偶 QQ 号；未婚返回 None。"""
    with _conn() as conn:
        row = conn.execute(
            f"SELECT partner_id FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND user_id = ? AND status = 'married'",
            (group_id, user_id),
        ).fetchone()
        return int(row["partner_id"]) if row else None


def is_married(group_id: int, user_id: int) -> bool:
    return get_partner(group_id, user_id) is not None


def married_ids(group_id: int) -> set[int]:
    """该群所有已婚者（用来把他们从抽取池里排除）。"""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT user_id FROM {_T_MARRIAGE}"
            " WHERE group_id = ? AND status = 'married'",
            (group_id,),
        ).fetchall()
        return {int(r["user_id"]) for r in rows}


def set_divorce_cd(*, group_id: int, user_id: int, until: float) -> None:
    """给**离婚发起方**设冷却截止时刻。"""
    with _conn() as conn:
        conn.execute(
            f"INSERT INTO {_T_CD} (group_id, user_id, until) VALUES (?, ?, ?)"
            " ON CONFLICT (group_id, user_id) DO UPDATE SET until = excluded.until",
            (group_id, user_id, until),
        )


def get_divorce_cd(group_id: int, user_id: int) -> float | None:
    """取冷却截止时刻；没有返回 None。"""
    with _conn() as conn:
        row = conn.execute(
            f"SELECT until FROM {_T_CD} WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        return float(row["until"]) if row else None


__all__ = [
    "count_members",
    "default_db_path",
    "divorce",
    "get_daily",
    "get_divorce_cd",
    "get_member",
    "get_partner",
    "init",
    "is_initialized",
    "is_married",
    "list_members",
    "lock_daily",
    "locked_partners",
    "married_ids",
    "marry",
    "set_daily",
    "set_divorce_cd",
    "touch_member",
]
