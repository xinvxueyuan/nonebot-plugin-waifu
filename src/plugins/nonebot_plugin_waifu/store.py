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


__all__ = [
    "count_members",
    "default_db_path",
    "get_member",
    "init",
    "is_initialized",
    "list_members",
    "touch_member",
]
