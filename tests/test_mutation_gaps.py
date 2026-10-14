"""补齐变异检验实测出的两个覆盖缺口（M11 的靶子「离婚 CD」已随 2026-10-14 口径整删）。

上一批变异检验有变异体存活，根因都是「没有测试真的调用那段代码」：

- **M14** 渲染失败改成抛出（而非返回 None）—— 没有测试调 `render_card`；
- **M16** 结果消息里不再 @ 被抽取人 —— 守卫只断言源码里「字符串存在」，
  没断言 `at_partner` 真的拼进 `message`。
  （**2026-10-10 用户要求关闭全部 @**，M16 随之改写成更严的**防回归守卫**：
  钉「结果消息里绝不允许出现任何 @」，把加回任何 @（抽取人 / 被抽取人）当作变异体。）

（原 **M11**「离婚 CD 给双方」的靶子本身已删 —— 用户 2026-10-14 取消离婚冷却，
`apply_divorce` 不再写任何冷却，对应的 CD 断言测试一并删除。同批口径变更：
**离婚改「单人即时生效」**——不再双向确认，本节继续钉「一发即离、不留 pending、
校验不放宽」。）

两条都**不依赖真 htmlkit**（`render._load_htmlkit` 是单独抽出来可 monkeypatch 的）。
"""

from __future__ import annotations

import asyncio
import re

from test_draw_flow import GROUP, ME, OTHER


# --------------------------------------------------------------------------- #
# 离婚：**单人即时生效**（用户 2026-10-14：无需对方同意；冷却已取消）
# --------------------------------------------------------------------------- #
def test_divorce_takes_effect_immediately_by_single_request(tmp_store):
    """**一发即离**：发出「离婚」当场解除关系，**不需要对方回发**任何确认。

    这也是**行为级**的「冷却确实没了」：成功后 `apply_divorce` 不得再
    写/提任何冷却（旧文案「已离婚。发起方 3 天内不能再抽老婆」会让这条红）。
    """
    from nonebot_plugin_waifu import handle, store

    store.marry(group_id=GROUP, a=ME, b=OTHER)
    out = handle.apply_divorce(group_id=GROUP, requester=ME, partner=OTHER)

    assert not out.error, out.error
    assert "已离婚" in out.message
    assert out.partner == OTHER

    # ① 当场生效：双向两行婚姻都清掉（没有「等对方回发」的中间态）
    assert store.get_partner(GROUP, ME) is None
    assert store.get_partner(GROUP, OTHER) is None

    # ② 不留 divorce pending（双向都查；旧实现会写发起方那条）
    assert store.get_pending(GROUP, requester=ME, partner=OTHER, kind="divorce") is None
    assert store.get_pending(GROUP, requester=OTHER, partner=ME, kind="divorce") is None


def test_divorce_non_partner_is_still_rejected(tmp_store):
    """③ 校验不放宽：自己 / 没娶过 / 不是自己的老婆 → 一律报错、关系不动。"""
    from nonebot_plugin_waifu import handle, store

    # 不能和自己离婚；没娶过也不能离
    assert handle.apply_divorce(group_id=GROUP, requester=ME, partner=ME).error
    assert handle.apply_divorce(group_id=GROUP, requester=ME, partner=OTHER).error

    # 娶的是 OTHER，@ 第三人 → 报错，且既有婚姻原样保留
    store.marry(group_id=GROUP, a=ME, b=OTHER)
    out = handle.apply_divorce(group_id=GROUP, requester=ME, partner=30003)
    assert out.error, "离的不是自己的老婆也必须报错"
    assert store.get_partner(GROUP, ME) == OTHER
    assert store.get_partner(GROUP, OTHER) == ME


# --------------------------------------------------------------------------- #
# M14：渲染失败一律回退 None（**不抛**），成功则原样透传
# --------------------------------------------------------------------------- #
def _ctx():
    from nonebot_plugin_waifu import render

    return render.CardContext(
        title="今日老婆",
        mention="@某人",
        user_id=10001,
        nickname="甲<b>某人",
        sex="female",
        last_seen_text="2026-10-10 03:20",
        avatar_data_uri="data:image/jpeg;base64,AAAA",
    )


def test_render_card_returns_none_when_htmlkit_load_raises(monkeypatch):
    """**M14-1**：`_load_htmlkit()` **同步**抛（htmlkit 没装）→ 返回 None，不抛。

    `render_card` 里 `html_to_pic = _load_htmlkit()` 是同步调用，所以替身用普通函数。
    """
    from nonebot_plugin_waifu import render

    def boom():
        raise RuntimeError("htmlkit 未安装")

    monkeypatch.setattr(render, "_load_htmlkit", boom)
    assert asyncio.run(render.render_card(_ctx())) is None


def test_render_card_returns_none_when_render_raises(monkeypatch):
    """**M14-2**：htmlkit 取到了，但渲染时抛 → 仍然返回 None（不抛）。"""
    from nonebot_plugin_waifu import render

    async def boom(_html, *, max_width):
        raise RuntimeError("渲染崩了")

    monkeypatch.setattr(render, "_load_htmlkit", lambda: boom)
    assert asyncio.run(render.render_card(_ctx())) is None


def test_render_card_passes_png_through_on_success(monkeypatch):
    """**M14-3**：成功路径**原样透传** htmlkit 的返回值（否则成功路径没人管）。"""
    from nonebot_plugin_waifu import render

    async def fake(_html, *, max_width):
        return b"PNG"

    monkeypatch.setattr(render, "_load_htmlkit", lambda: fake)
    assert asyncio.run(render.render_card(_ctx())) == b"PNG"


# --------------------------------------------------------------------------- #
# M16：结果消息**完全不含任何 @**（抽取人 / 被抽取人都不能有）（防回归守卫）
# --------------------------------------------------------------------------- #
def test_result_message_has_no_at_never_anyone():
    """**M16（改）**：结果消息里**绝不允许出现任何 `@`**。

    用户 2026-10-10 要求关闭全部 @ → 本条从「钉 @ 双方」→「钉只 @ 抽取人」
    再次反转为**更严的防回归守卫**：结果消息里任何 @ 都不允许出现。

    只断言「单个字符串存在/不存在」是不够的（会被「只留赋值、不拼进 message」骗过）；
    这里把源码空白全部去掉后匹配整段表达式。
    """
    import inspect

    from nonebot_plugin_waifu import handle

    src = inspect.getsource(handle._build_and_send)
    flat = re.sub(r"\s+", "", src)

    # 出图分支：纯图，**没有任何 @**
    assert "message=Message(MessageSegment.image(png))" in flat
    # 回退文本分支：纯文本，**没有任何 @**，且没有前导换行
    assert "message=Message(MessageSegment.text(fallback))" in flat
    # ← 防回归：结果消息里任何形态的 @ 都不允许出现
    assert "MessageSegment.at" not in flat
    assert "at_drawer" not in flat
    assert "at_partner" not in flat
