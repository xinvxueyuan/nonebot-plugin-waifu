"""store 层：建表、成员池 upsert、按群查询。"""

from __future__ import annotations

import sqlite3
import time


def test_init_creates_all_tables(tmp_store):
    conn = sqlite3.connect(tmp_store)
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {
        "waifu_members",
        "waifu_daily",
        "waifu_marriage",
        "waifu_pending",
        "waifu_divorce_cd",
    } <= names


def test_touch_member_inserts_then_updates(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    rows = store.list_members(100)
    assert len(rows) == 1
    assert rows[0]["nickname"] == "甲"

    # 二次 upsert 只更新时间与昵称，不新增行
    store.touch_member(group_id=100, user_id=1, nickname="甲改名")
    rows = store.list_members(100)
    assert len(rows) == 1
    assert rows[0]["nickname"] == "甲改名"


def test_list_members_is_scoped_to_group(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    store.touch_member(group_id=200, user_id=2, nickname="乙")
    assert [r["user_id"] for r in store.list_members(100)] == [1]
    assert [r["user_id"] for r in store.list_members(200)] == [2]


def test_touch_member_does_not_clobber_nickname_with_empty(tmp_store):
    """昵称取不到时不要用空串覆盖已有的好昵称。"""
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    store.touch_member(group_id=100, user_id=1, nickname="")
    assert store.list_members(100)[0]["nickname"] == "甲"


def test_last_seen_advances(tmp_store):
    from nonebot_plugin_waifu import store

    store.touch_member(group_id=100, user_id=1, nickname="甲")
    first = store.list_members(100)[0]["last_seen"]
    time.sleep(0.01)
    store.touch_member(group_id=100, user_id=1, nickname="甲")
    second = store.list_members(100)[0]["last_seen"]
    assert second >= first
