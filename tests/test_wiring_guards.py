"""接线静态守卫。

**为什么不能只测纯函数**：把 handler 里的判据换掉、把 `block=False` 改成 True、
把优先级写反 —— 纯函数测试**依然全绿**（变异检验实证过）。所以必须读源码断言。
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import nonebot_plugin_waifu as pkg


def test_watch_matcher_is_non_blocking():
    """`block=False` 是安全底线：catch-all 阻断会饿死所有别的插件。"""
    src = inspect.getsource(pkg)
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "on_message":
            kw = {k.arg: k.value for k in node.keywords}
            assert "block" in kw, "on_message 必须显式写 block（别依赖默认值）"
            assert isinstance(kw["block"], ast.Constant) and kw["block"].value is False
            found = True
    assert found, "没找到 catch-all 的 on_message —— 守卫可能失效了"


def test_every_command_has_nonempty_cmd_name():
    """六个命令词都在；「换老婆」「老婆关系图」**来自配置**（默认值见 config）。"""
    from nonebot_plugin_waifu import handle
    from nonebot_plugin_waifu.config import DEFAULT_CHANGE_CMD, DEFAULT_GRAPH_CMD

    assert handle.CMD_CHANGE == DEFAULT_CHANGE_CMD == "换老婆"
    assert handle.CMD_GRAPH == DEFAULT_GRAPH_CMD == "老婆关系图"
    assert set(handle.CMD_NAMES) == {
        "今日老婆",
        DEFAULT_CHANGE_CMD,
        "确认老婆",
        "娶老婆",
        "离婚",
        DEFAULT_GRAPH_CMD,
    }


def test_change_command_word_comes_from_config():
    """「换老婆」命令词**必须可配**（生产用「换一只」，用户 2026-10-10 拍板）。"""
    import inspect

    from nonebot_plugin_waifu import handle

    src = inspect.getsource(handle)
    assert "CMD_CHANGE: Final[str] = plugin_config.waifu_change_cmd" in src
    # 斜杠别名也按它拼，不允许写死字面量
    assert 'aliases={f"/{CMD_CHANGE}"}' in src


def test_result_message_has_no_at_all():
    """结果消息**不含任何 `@`**（用户 2026-10-10 要求关闭全部 @）。

    结果消息 = 卡片图（渲染不可用时为纯文本）；被抽中者不会收到任何提示。
    仍不允许出现任何额外话术。
    """
    import inspect

    from nonebot_plugin_waifu import handle

    src = inspect.getsource(handle._build_and_send)
    # ← 结果消息里根本不允许出现 at（抽取人 / 被抽取人都不行，防回归）
    assert "MessageSegment.at" not in src
    # 纯图分支与纯文本回退分支都还在
    assert "MessageSegment.image(png)" in src
    assert "MessageSegment.text(fallback)" in src
    # 那条「你被 X 抽成 Y 了」的话术必须已经不存在
    assert "抽成" not in src
    # 也不该再另发一条通知消息（只允许两处 send_group_msg：出图 / 回退文本）
    assert src.count("send_group_msg") == 2


def test_today_command_has_chou_laopo_alias():
    """用户口径：「抽老婆」与「今日老婆」**同义**。"""
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    assert '"抽老婆"' in src


def test_commands_have_slash_prefix_aliases():
    """用户要求加 `/` 前缀别名（生产 COMMAND_START 含空串，两种都能触发）。

    「换老婆」「老婆关系图」那两条是**配置驱动**的 f-string，所以单独断言。
    """
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    for cmd in ("/今日老婆", "/确认老婆", "/娶老婆", "/离婚"):
        assert f'"{cmd}"' in src, f"缺少别名 {cmd}"
    assert 'aliases={f"/{CMD_CHANGE}"}' in src
    assert 'aliases={f"/{CMD_GRAPH}"}' in src


def test_confirm_does_not_lock_partner():
    """「确认老婆」**不锁对方**（用户 2026-10-10 明确）。

    静态断言：`_h_confirm` 里只允许出现**一次** `lock_daily`，且必须是对调用者
    自己（`gid, me, today`）—— 不允许把 partner 也锁掉。
    """
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    start = src.index("async def _h_confirm")
    end = src.index("__all__", start)
    body = src[start:end]
    assert body.count("store.lock_daily") == 1, "lock_daily 被调了不止一次"
    assert "store.lock_daily(gid, me, today)" in body
    assert "lock_daily(gid, partner" not in body
    assert "lock_daily(gid, int(row" not in body


def test_do_draw_early_returns_locked_message():
    """**接线守卫（2026-10-14）**：确认后 `_do_draw` 必须**早退**成「已定人」提示。

    `store.get_locked_partner(...)` 必须在两个 `resolve_*` 调用**之前**出现；
    否则会退化成「复用抽取卡片」的老 bug（纯函数仍全绿，只有接线坏了才露馅）。
    """
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_do_draw"
    )
    # 跳过 docstring：它按名字提到 `resolve_*`（只是注释性的叙述），
    # 会变成假锚点 —— 守卫要钉的是**代码里的调用顺序**。
    body = fn.body[1:] if fn.body and isinstance(fn.body[0], ast.Expr) else fn.body
    joined = "\n".join(ast.unparse(stmt) for stmt in body)
    assert "get_locked_partner" in joined and "locked_notice" in joined
    assert joined.index("get_locked_partner") < joined.index("resolve_today_action")


def test_unknown_gender_always_passes_guard():
    """**用户口径**：「群员隐藏的全部判为通过」。

    静态守卫，钉住 `rules.is_eligible_gender` 里那句 `unknown → True`。
    若有人把它改成 fail-closed（性别查不到就不给抽），这条会红 ——
    那种改法会让某些群**永远抽不出人**，且失败是静默的。
    """
    import inspect

    from nonebot_plugin_waifu import rules

    src = inspect.getsource(rules.is_eligible_gender)
    assert '"unknown"' in src
    assert "return True" in src

    # 行为侧再钉一遍（三档都要通过）
    for mode in ("normal", "all", "reverse"):
        assert rules.is_eligible_gender("male", "unknown", mode=mode)
        assert rules.is_eligible_gender("unknown", "male", mode=mode)


def test_no_unbounded_retry_loop_in_pick_eligible():
    """取向筛必须**有界**（最多 `max_tries` 次），不能写 while True。

    否则群里全是不满足取向的人时，这条命令会**卡住不返回**
    （外人看来就是「机器人死了」）。
    """
    import inspect

    from nonebot_plugin_waifu import handle

    src = inspect.getsource(handle._pick_eligible)
    assert "while True" not in src
    assert "range(max_tries)" in src
    assert "max_tries: int = 8" in inspect.getsource(handle._pick_eligible)


def test_all_mode_skips_sex_lookup():
    """`all`（默认）档下**不许调用 fetch_sex**（用户口径：默认不判断性别）。"""
    import inspect

    from nonebot_plugin_waifu import handle

    src = inspect.getsource(handle._pick_eligible)
    # 提前 return 必须在 fetch_sex 之前
    assert src.index('if mode not in {"normal", "reverse"}') < src.index("fetch_sex(")


def test_headline_template_default_is_qunyou():
    """首行模板**默认用「群友」**，生产用配置覆盖成「群幼」（用户 2026-10-10 拍板）。"""
    from nonebot_plugin_waifu.config import (
        DEFAULT_HEADLINE_TEMPLATE,
        Config,
    )

    assert DEFAULT_HEADLINE_TEMPLATE == (
        "{drawer}, 你今天的群友老婆是 {partner}（{user_id}）"
    )
    assert Config().waifu_headline_template == DEFAULT_HEADLINE_TEMPLATE


def test_headline_template_is_configurable():
    """模板必须**可配**（用户明确要求「配置可变量」）。"""
    from nonebot_plugin_waifu import handle
    from nonebot_plugin_waifu.config import Config

    c = Config(waifu_headline_template="{drawer} 抽到了 {partner}({user_id})")
    got = handle.format_headline(
        c.waifu_headline_template, drawer="甲", partner="乙", user_id=10001
    )
    assert got == "甲 抽到了 乙(10001)"


def test_bad_headline_template_falls_back_instead_of_raising():
    """**占位符写错不能让命令没反应** —— 必须回退默认模板。

    配置是可变的，用户随时可能写错一个 `{qq}`；让它冒 KeyError 出去的话，
    群里表现就是「发命令毫无反应」，而根因只是一处配置笔误。
    """
    from nonebot_plugin_waifu import handle

    for bad in ("{drawer} 的 {qq}", "{drawer} 的 {", "{drawer} 的 {}"):
        got = handle.format_headline(bad, drawer="甲", partner="乙", user_id=1)
        assert "甲" in got and "乙" in got  # 回退后仍是可用的成句文案


def test_quota_message_is_exact():
    """文案是用户逐字指定的。"""
    from nonebot_plugin_waifu import rules

    assert rules.QUOTA_MESSAGE == "渣男，你今天没有老婆了！"


def test_confirm_reply_no_longer_claims_change_skips():
    """**文案与行为一致**：确认成功回执不再说「换一只时也会跳过」。

    因为确认后根本不能再换（用户 2026-10-11 新口径）。

    ⚠️ Task 8A 起这条文案是 `notices.confirm_notice()` **编译出来的回执卡**，
    不再写在 `_h_confirm` 里 —— 所以断言目标改成「用户实际看到的那段文本」
    （`build_notice_fallback_text`，与图一一对应），比钉源码更贴近行为。
    """
    from nonebot_plugin_waifu import notices, render

    ctx = notices.confirm_notice(
        me_name="甲",
        partner_id=20002,
        partner_name="乙",
        last_seen_text="2026-10-14 00:31",
        used=1,
        limit=20,
    )
    text = render.build_notice_fallback_text(ctx)
    assert "换一只时也会跳过" not in text, "确认回执还写着旧行为（换一只会跳过）"
    assert "不能再换" in text, "确认回执应说明今天不能再换"


def test_no_plain_text_reply_left_in_handlers():
    """**接线守卫（Task 8A）**：回执的纯文本只允许出现在**三处兜底**里。

    用户口径：此插件其余地方也一律用图片代替纯文本（纯文本回执在群里会被刷掉）。
    所以 `MessageSegment.text(...)` 只允许在
    `_build_and_send`（抽取卡片渲染失败）、`_send_notice`（回执卡渲染失败）
    与 `_h_graph`（关系图渲染失败 → `build_graph_fallback_text`）里出现；
    `MessageSegment.at(...)` 只允许在 `_send_notice` 里出现（回执要 @ 到发起人，
    而抽取卡片**一个 @ 都不许有**）。

    只断言「源码里没有某个字符串」不够 —— 这里按**函数**收集调用点，
    任何 handler 里新加的纯文本回执都会让这条红。
    """
    src = pathlib.Path("src/plugins/nonebot_plugin_waifu/handle.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)

    def _owners(attr: str) -> set[str]:
        """收集 `MessageSegment.<attr>(...)` 出现在哪些函数里。"""
        found: set[str] = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == attr
                    and getattr(node.func.value, "id", "") == "MessageSegment"
                ):
                    found.add(fn.name)
        return found

    assert _owners("text") == {"_build_and_send", "_send_notice", "_h_graph"}, (
        "handle.py 里出现了兜底之外的纯文本回复"
    )
    assert _owners("at") == {"_send_notice"}, (
        "handle.py 里只有 _send_notice 允许 @（抽取卡片不许有 @）"
    )


def test_pending_window_is_three_minutes():
    from nonebot_plugin_waifu import rules

    assert rules.PENDING_WINDOW_SECONDS == 180


def test_built_html_never_contains_http_image_url():
    """**行为断言**：渲染出的 HTML 里不允许出现 `src="http...`。

    头像必须走 data URI —— htmlkit 的 `network_img_fetcher` 不带请求头且失败静默，
    直接把 http 头像地址写进模板会得到碎图且**无任何报错**。

    ⚠️ 不要写成「源码里不许出现 qlogo.cn」：`render.avatar_url()` 本来就要拼那个
    基址（照 lingchu-bot 前端）。要钉的是**最终 HTML**，不是源码。
    """
    from nonebot_plugin_waifu import render

    ctx = render.CardContext(
        title="今日老婆",
        mention="@1",
        user_id=1,
        nickname="甲",
        sex="female",
        last_seen_text="2026-10-10 03:20",
        avatar_data_uri=render.to_data_uri(b"\xff\xd8\xff"),
    )
    html = render.build_html(ctx)
    assert 'src="http' not in html
    assert 'src="data:image/' in html

    # 没有头像时也不许退化成 http 直链（必须是占位块）
    no_avatar = render.CardContext(**{**ctx.__dict__, "avatar_data_uri": None})
    html2 = render.build_html(no_avatar)
    assert 'src="http' not in html2
    assert "<img" not in html2
