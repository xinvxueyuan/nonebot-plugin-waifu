"""「结婚」= 「娶老婆」的命令别名（生产实证：用户发「结婚 @某人」完全没反应）。

**为什么必须行为级**：`on_command(..., aliases={...})` 的效果只在注册那一刻由
`nonebot.rule.command()` 写进全局 `TrieRule`；源码里出现 "结婚" 这几个字
（字符串/AST 断言）**证明不了**它真的进了 trie、也证明不了规则能接住它。
所以这里用**真实 `TrieRule.get_value`** 解析**真实 `GroupMessageEvent`**，
再把解析结果喂给**真实 matcher 的 `rule`** 判定 —— 这正是生产那条路径的前两步
（`nonebot.message.handle_event`：先 `TrieRule.get_value`，再逐个 matcher 比规则）。
"""

from __future__ import annotations

import asyncio
import dataclasses

import nonebot
from nonebot.adapters.onebot.v11 import (
    Adapter,
    Bot,
    GroupMessageEvent,
    Message,
    MessageSegment,
)
from nonebot.adapters.onebot.v11.event import Sender
from nonebot.rule import TrieRule

GROUP = 1094538078
ME = 10001
PARTNER = 12345
BOT_ID = 3128682634

#: 命令词 → matcher 名（用真实注册的 matcher，不是手搓替身）
_CMD_TO_MATCHER = (
    ("今日老婆", "today_cmd"),
    ("换老婆", "change_cmd"),
    ("确认老婆", "confirm_cmd"),
    ("娶老婆", "marry_cmd"),
    ("离婚", "divorce_cmd"),
)


def _bot() -> Bot:
    """真实 onebot v11 `Bot`（规则判定只用到它的 `self_id` 与类型）。"""
    return Bot(adapter=Adapter(nonebot.get_driver()), self_id=str(BOT_ID))


def _event(
    *, text: str | None = None, at: int | None = None, user_id: int = ME
) -> GroupMessageEvent:
    """构造真实事件：首个文本段 + 可选 at 段。

    ⚠️ 本机 onebot v11 的 `GroupMessageEvent` 需要 `message_id`（不给会缺字段）。
    """
    segs: list[MessageSegment] = []
    if text is not None:
        segs.append(MessageSegment.text(text))
    if at is not None:
        segs.append(MessageSegment.at(at))
    return GroupMessageEvent(
        time=1,
        self_id=BOT_ID,
        post_type="message",
        sub_type="normal",
        user_id=user_id,
        message_type="group",
        message_id=42,
        message=segs,
        raw_message="".join(str(s) for s in segs),
        font=0,
        sender=Sender(user_id=user_id, nickname=f"u{user_id}", role="member"),
        group_id=GROUP,
    )


def _parse(
    text: str, *, at: int | None = None, user_id: int = ME
) -> tuple[dict, dict, GroupMessageEvent]:
    """真实 `TrieRule.get_value` 解析（等价于 `handle_event` 里的那一步）。"""
    event = _event(text=text, at=at, user_id=user_id)
    state: dict = {}
    parsed = TrieRule.get_value(_bot(), event, state)
    return parsed, state, event


def _cmd_words(matcher: type) -> set[str]:
    """读某个命令 matcher **真实注册的命令词集合**（含别名）。

    命令词只存在 `CommandRule.cmds` 里（matcher 的 `repr` 只是显示用）。
    ⚠️ 取不到就 fail —— 否则这类断言会恒真（「扫了个寂寞」）。
    """
    words: set[str] = set()
    for checker in matcher.rule.checkers:
        call = getattr(checker, "call", None)
        cmds = getattr(call, "cmds", None)
        if not cmds:
            continue
        for cmds_tuple in cmds:
            words.update(cmds_tuple)
    assert words, f"没取到 {matcher.__name__} 的任何命令词 —— 提取器失效了"
    return words


def _rule_hits(parsed_state: dict, event: GroupMessageEvent) -> list[str]:
    """把解析结果喂给全部**真实**命令 matcher 的 rule，返回命中的 matcher 名。"""
    bot = _bot()
    hits: list[str] = []
    for _cmd, name in _CMD_TO_MATCHER:
        matcher = _matcher(name)
        if asyncio.run(matcher.rule(bot, event, dict(parsed_state))):
            hits.append(name)
    return hits


def _matcher(name: str) -> type:
    from nonebot_plugin_waifu import handle

    return getattr(handle, name)


def _marry_rule_accepts(parsed_state: dict, event: GroupMessageEvent) -> bool:
    """只用 `marry_cmd` 那条真实规则判定（`Rule.__call__` 是真实检查入口）。"""
    matcher = _matcher("marry_cmd")
    return asyncio.run(matcher.rule(_bot(), event, dict(parsed_state)))


def test_jiehun_alias_registered():
    """「结婚 @某人」被真实 trie 解析出命令词，且**被 marry_cmd 的规则接住**。"""
    from nonebot_plugin_waifu import handle

    parsed, state, event = _parse("结婚 ", at=PARTNER)

    # ① 真实 trie 解析出了命令（没有别名时这里是 None —— 生产的「毫无反应」）
    assert parsed["command"] == ("结婚",), f"trie 没认出「结婚」：{parsed['command']}"
    assert parsed["command_arg"] == Message(MessageSegment.at(PARTNER)), (
        f"参数没解析出来：{parsed['command_arg']!r}"
    )

    # ② 真的被「娶老婆」那条 matcher 接住（而不是被别的命令接走）
    assert _marry_rule_accepts(state, event) is True, "「结婚」没被 marry_cmd 接住"
    assert _rule_hits(state, event) == ["marry_cmd"], (
        f"「结婚」路由到了非 marry 的 matcher：{_rule_hits(state, event)}"
    )

    # ③ 注册表里确实有这个词（命令词只存在于 CommandRule.cmds）
    assert "结婚" in _cmd_words(handle.marry_cmd)
    # 反向：别的命令词表里不许有它
    assert "结婚" not in _cmd_words(handle.divorce_cmd)


def test_jiehun_alias_matches_marry_semantics():
    """**行为对照**：同样参数，用哪个命令词跑 `apply_marry` 结果都一致。

    跑的是**真实** `handle.apply_marry`：参数由真实 trie 解析 → `_resolve_target`
    → `apply_marry`，四个命令词（`娶老婆` / `结婚` / `/娶老婆` / `/结婚`）必须给出
    逐字段相同的结果；最后再用「结婚」把一桩婚事真正办成（双向确认绑定成功）。
    """
    from nonebot_plugin_waifu import handle, store

    outcomes: dict[str, tuple] = {}
    for word in ("娶老婆", "结婚", "/娶老婆", "/结婚"):
        parsed, state, event = _parse(f"{word} ", at=PARTNER)
        assert parsed["command"] is not None, f"「{word}」没被解析成命令"
        assert _marry_rule_accepts(state, event) is True, (
            f"「{word}」没被 marry_cmd 接住"
        )

        targets = handle._resolve_target(event, parsed["command_arg"])
        assert targets == [PARTNER], f"「{word}」解析出的目标不对：{targets}"

        store.clear_pending(GROUP, requester=ME, partner=PARTNER, kind="marry")
        out = handle.apply_marry(
            group_id=GROUP,
            requester=ME,
            partners=targets,
            now=1000.0,
            max_partners=1,
        )
        outcomes[word] = dataclasses.astuple(
            dataclasses.replace(out, partners=tuple(out.partners))
        )

    # 四个词的结果**完全一致**（同一个 dataclass 的全部字段）
    distinct = set(outcomes.values())
    assert len(distinct) == 1, f"不同命令词结果不一致：{outcomes}"

    # 且结果本身是「已发出求婚」而不是某种错误（防止四个都错得一样）
    first = handle.apply_marry(
        group_id=GROUP, requester=ME, partners=[PARTNER], now=1000.0, max_partners=1
    )
    assert not first.error and first.bound is False
    assert "求婚" in first.message

    # 「结婚」能把婚事办成：对方用同一个别名回发
    reply_parsed, _state, reply_event = _parse("结婚 ", at=ME, user_id=PARTNER)
    reply_targets = handle._resolve_target(reply_event, reply_parsed["command_arg"])
    assert reply_targets == [ME]
    bound = handle.apply_marry(
        group_id=GROUP,
        requester=PARTNER,
        partners=reply_targets,
        now=1001.0,
        max_partners=1,
    )
    assert bound.bound is True, f"「结婚」没能办成婚事：{bound}"
    assert store.list_partners(GROUP, ME) == [PARTNER]


def test_divorce_has_no_jiehun_like_alias():
    """**反向守卫**：「离婚」不许跟着加别名（用户 2026-10-11：只加「结婚」）。"""
    from nonebot_plugin_waifu import handle

    words = _cmd_words(handle.divorce_cmd)
    # 精确断言：离婚的命令词表只有本体 + 斜杠本体
    assert words == {"离婚", "/离婚"}, f"「离婚」命令词被人动了：{sorted(words)}"
    for banned in ("结婚", "/结婚", "分手", "/分手"):
        assert banned not in words, f"「离婚」不允许有别名 {banned}"

    # 行为侧两向都钉：发「结婚」不会掉进离婚规则；发「离婚」仍然正常命中
    _parsed, state, event = _parse("结婚 ", at=PARTNER)
    divorce_rule = handle.divorce_cmd.rule
    assert asyncio.run(divorce_rule(_bot(), event, dict(state))) is False, (
        "「结婚」被离婚规则接走了"
    )
    parsed_d, state_d, event_d = _parse("离婚 ", at=PARTNER)
    assert parsed_d["command"] == ("离婚",)
    assert asyncio.run(divorce_rule(_bot(), event_d, dict(state_d))) is True
    assert _rule_hits(state_d, event_d) == ["divorce_cmd"]


def test_slash_jiehun_also_works():
    """`/结婚` 也命中（生产 `COMMAND_START` 同时含空串与 `/`）。

    ⚠️ 这里**不能**断言 `command == ("/结婚",)`：`aliases` 里的 `结婚` 与 `/结婚`
    会生成**同一个 trie 前缀** `/结婚`（`command()` 对每个 command_start 都注册一次），
    而 `TrieRule.add_prefix` 是**先到先得 + 警告**，集合迭代顺序又随
    `PYTHONHASHSEED` 变 —— 于是同一个前缀的取值可能是 `("结婚",)` 也可能是
    `("/结婚",)`。两者都在 `CommandRule.cmds` 里、都指向同一个 matcher，
    **行为完全等价**；所以断言「命中 marry」而不是断言那个元组。
    """
    from nonebot_plugin_waifu import handle

    parsed, state, event = _parse("/结婚 ", at=PARTNER)
    assert parsed["command"] in {("结婚",), ("/结婚",)}, (
        f"trie 没认出「/结婚」：{parsed['command']}"
    )
    assert parsed["raw_command"] == "/结婚"
    assert parsed["command_arg"] == Message(MessageSegment.at(PARTNER))
    assert _marry_rule_accepts(state, event) is True, "「/结婚」没被 marry_cmd 接住"
    assert _rule_hits(state, event) == ["marry_cmd"]

    # 注册表：裸词与斜杠词都在（斜杠词属于 aliases）
    words = _cmd_words(handle.marry_cmd)
    assert {"娶老婆", "/娶老婆", "结婚", "/结婚"} <= words, (
        f"命令词表不全：{sorted(words)}"
    )
