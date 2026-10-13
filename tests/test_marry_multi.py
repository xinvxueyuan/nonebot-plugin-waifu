"""多配偶：上限配置、三列主键重建、一次 @ 多人（全有全无）、各对独立。"""

from __future__ import annotations

import ast
import pathlib
import sqlite3

from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment
from nonebot.adapters.onebot.v11.event import Sender

GROUP = 1094538078
ME = 10001
OTHER = 20002
THIRD = 30003
FOURTH = 40004


def _event(user_id: int = ME) -> GroupMessageEvent:
    return GroupMessageEvent(
        time=1,
        self_id=3128682634,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=0,
        message=[],
        raw_message="",
        font=0,
        sender=Sender(user_id=user_id, nickname=f"u{user_id}", role="member"),
        group_id=GROUP,
    )


def _pk(db_path) -> list[str]:
    """读某库 waifu_marriage 的主键列（按主键序号排序）。"""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("PRAGMA table_info(waifu_marriage)").fetchall()
    conn.close()
    pk = sorted((int(r[5]), str(r[1])) for r in rows if int(r[5]) > 0)
    return [name for _, name in pk]


def _marry_pair(a: int, b: int, *, now: float = 1.0, max_partners: int = 1) -> None:
    """让 a 与 b 真正绑定（互发双向请求）。"""
    from nonebot_plugin_waifu import handle

    handle.apply_marry(
        group_id=GROUP, requester=a, partner=b, now=now, max_partners=max_partners
    )
    handle.apply_marry(
        group_id=GROUP, requester=b, partner=a, now=now + 1.0, max_partners=max_partners
    )


# --------------------------------------------------------------------------- #
# R1：WAIFU_MAX_PARTNERS 配置
# --------------------------------------------------------------------------- #
def test_max_partners_default_is_one():
    from nonebot_plugin_waifu.config import Config

    assert Config().waifu_max_partners == 1


# --------------------------------------------------------------------------- #
# R4：一次 @ 多人 + 上限判定（只查发起方、全有全无）
# --------------------------------------------------------------------------- #
def test_marry_rejected_when_over_limit():
    """已有 1 个老婆 + max=1 → 再娶 → 拒绝且库里无变化。"""
    from nonebot_plugin_waifu import handle, store

    _marry_pair(ME, OTHER, max_partners=1)
    assert store.get_partner(GROUP, ME) == OTHER

    out = handle.apply_marry(
        group_id=GROUP, requester=ME, partner=THIRD, now=100.0, max_partners=1
    )
    assert out.error, "超过上限必须拒绝"
    assert "1" in out.error, "拒绝文案要说明上限"
    # 库里无变化
    assert store.list_partners(GROUP, ME) == [OTHER]
    assert store.list_partners(GROUP, THIRD) == []


def test_marry_allows_second_when_max_is_two():
    from nonebot_plugin_waifu import handle, store

    _marry_pair(ME, OTHER, max_partners=2)
    out1 = handle.apply_marry(
        group_id=GROUP, requester=ME, partner=THIRD, now=100.0, max_partners=2
    )
    assert not out1.error, "max=2 时第二个应能娶"
    out2 = handle.apply_marry(
        group_id=GROUP, requester=THIRD, partner=ME, now=110.0, max_partners=2
    )
    assert out2.bound
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}


def test_marry_unlimited_when_max_is_zero():
    """max=0 → 不限：多个都能娶。"""
    from nonebot_plugin_waifu import store

    for other in (OTHER, THIRD, FOURTH):
        _marry_pair(ME, other, now=1.0, max_partners=0)
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD, FOURTH}
    assert store.count_partners(GROUP, ME) == 3


def test_marry_multi_at_whole_rejected_when_any_over_limit():
    """max=1、已 0 个、一次 @ 2 人 → **整条拒绝**（全有全无）。"""
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(
        group_id=GROUP,
        requester=ME,
        partners=[OTHER, THIRD],
        now=1.0,
        max_partners=1,
    )
    assert out.error, "一次娶两个超过 max=1 必须整条拒绝"
    assert store.list_partners(GROUP, ME) == []
    # 一行都没写：两个 pending 都不该存在
    assert store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry") is None
    assert store.get_pending(GROUP, requester=ME, partner=THIRD, kind="marry") is None


def test_resolve_target_returns_all_ats():
    """一次 @ 多人 → 全部；重复去重；`all` 忽略；多个纯文本 QQ 号也能解析。"""
    from nonebot_plugin_waifu import handle

    msg = Message([
        MessageSegment.at(111),
        MessageSegment.at(222),
        MessageSegment.at(111),  # 重复
        MessageSegment.at("all"),  # @全体 → 忽略
        MessageSegment.text("333 444,555"),
    ])
    got = handle._resolve_target(_event(), msg)
    assert got == [111, 222, 333, 444, 555]
    # 向后兼容：单个 @ 或单个数字仍正常
    assert handle._resolve_target(_event(), Message([MessageSegment.at(999)])) == [999]
    assert handle._resolve_target(_event(), Message([MessageSegment.text("777")])) == [
        777
    ]
    # 没有目标 → 空列表（不是 None）
    assert handle._resolve_target(_event(), Message([MessageSegment.text("hi")])) == []


def test_multi_marry_each_pair_independent():
    """A @B @C 后，B 单独回发 → 只绑定 A-B，C 仍未绑定。"""
    from nonebot_plugin_waifu import handle, store

    out = handle.apply_marry(
        group_id=GROUP,
        requester=ME,
        partners=[OTHER, THIRD],
        now=1.0,
        max_partners=5,
    )
    assert not out.error
    assert (
        store.get_pending(GROUP, requester=ME, partner=OTHER, kind="marry") is not None
    )
    assert (
        store.get_pending(GROUP, requester=ME, partner=THIRD, kind="marry") is not None
    )

    back = handle.apply_marry(
        group_id=GROUP, requester=OTHER, partner=ME, now=2.0, max_partners=5
    )
    assert back.bound
    assert store.list_partners(GROUP, ME) == [OTHER]
    assert store.list_partners(GROUP, THIRD) == []
    assert store.get_partner(GROUP, THIRD) is None


def test_divorce_only_removes_that_pair():
    """A 同时娶了 B、C；A 与 B 离婚 → C 仍与 A 已婚。"""
    from nonebot_plugin_waifu import store

    store.marry(group_id=GROUP, a=ME, b=OTHER)
    store.marry(group_id=GROUP, a=ME, b=THIRD)
    assert set(store.list_partners(GROUP, ME)) == {OTHER, THIRD}

    store.divorce(group_id=GROUP, a=ME, b=OTHER)
    assert store.list_partners(GROUP, ME) == [THIRD]
    assert store.is_married(GROUP, THIRD)
    assert not store.is_married(GROUP, OTHER)


# --------------------------------------------------------------------------- #
# R2：婚姻表三列主键重建（零行安全护栏）
# --------------------------------------------------------------------------- #
_OLD_MARRIAGE = (
    "CREATE TABLE waifu_marriage ("
    " group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,"
    " partner_id INTEGER NOT NULL, status TEXT NOT NULL, since REAL NOT NULL,"
    " PRIMARY KEY (group_id, user_id));"
)


def test_marriage_table_rebuild_when_old_primary_key(tmp_path):
    from nonebot_plugin_waifu import store

    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_MARRIAGE)
    conn.commit()
    conn.close()
    assert _pk(db) == ["group_id", "user_id"]

    store.init(db)
    assert _pk(db) == ["group_id", "user_id", "partner_id"]
    # 新表真的能用：一次写两条
    store.marry(group_id=1, a=1, b=2)
    store.marry(group_id=1, a=1, b=3)
    assert set(store.list_partners(1, 1)) == {2, 3}


def test_marriage_table_not_rebuilt_when_rows_present(tmp_path):
    """**安全护栏**：旧表**非零行** → 不重建（不冒丢数据的风险）。"""
    from nonebot_plugin_waifu import store

    db = tmp_path / "old2.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(_OLD_MARRIAGE)
    conn.execute("INSERT INTO waifu_marriage VALUES (1, 1, 2, 'married', 0.0)")
    conn.commit()
    conn.close()

    store.init(db)
    assert _pk(db) == ["group_id", "user_id"], "非零行时不该重建"
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM waifu_marriage").fetchone()[0]
    conn.close()
    assert n == 1, "老数据必须还在"


# --------------------------------------------------------------------------- #
# R5：接线守卫
# --------------------------------------------------------------------------- #
def test_h_marry_passes_max_partners_to_apply_marry():
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    call = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "apply_marry":
            call = node
    assert call is not None, "找不到 apply_marry 调用 —— 接线可能被删了"
    kw = {k.arg: k.value for k in call.keywords}
    assert "max_partners" in kw, "apply_marry 漏传 max_partners（上限判定会静默失效）"
    flat = ast.unparse(kw["max_partners"])
    assert "plugin_config.waifu_max_partners" in flat, f"没接到配置：{flat}"
