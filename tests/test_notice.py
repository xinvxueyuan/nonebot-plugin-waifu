"""回执卡纯函数：字段齐全、转义、语气标记、文本兜底信息等价。"""

from __future__ import annotations


def _ctx():
    from nonebot_plugin_waifu import render

    return render.NoticeContext(
        title="今天已经定人",
        tone="deny",
        headline="甲，你今天的老婆是乙（20002）",
        avatar_user_id=20002,
        fields=(("状态", "已确认，今天不能再换"), ("今日抽取", "已用 1/20 次")),
        items=(render.NoticeItem(user_id=30003, line="等待回应"),),
        note="明天再发「今日老婆」就能重新抽",
    )


def test_notice_html_carries_every_field():
    """**信息密度守卫**：标题/首行/头像/字段/名单/下一步一个都不能少。"""
    from nonebot_plugin_waifu import render

    html = render.build_notice_html(
        _ctx(), avatars={20002: "data:image/jpeg;base64,AAAA"}
    )
    for needle in (
        "今天已经定人",
        "甲，你今天的老婆是乙（20002）",
        "状态",
        "已确认，今天不能再换",
        "今日抽取",
        "已用 1/20 次",
        "等待回应",
        "明天再发「今日老婆」就能重新抽",
        "data:image/jpeg;base64,AAAA",
    ):
        assert needle in html, f"回执缺内容：{needle}"
    assert "http" not in html  # 绝不出现网络图片 URL


def test_notice_html_escapes_and_marks_tone():
    from nonebot_plugin_waifu import render

    ctx = render.NoticeContext(title="a<b", tone="warn", headline="x&y", note="n<x")
    html = render.build_notice_html(ctx)
    assert "a<b" not in html and "a&lt;b" in html and "x&amp;y" in html
    assert 'data-tone="warn"' in html


def test_notice_fallback_text_is_information_equivalent():
    """兜底文本必须与图**信息等价**（用户口径：不能靠翻历史）。"""
    from nonebot_plugin_waifu import render

    text = render.build_notice_fallback_text(_ctx())
    for needle in (
        "今天已经定人",
        "甲，你今天的老婆是乙（20002）",
        "状态：已确认，今天不能再换",
        "今日抽取：已用 1/20 次",
        "等待回应",
        "明天再发「今日老婆」就能重新抽",
    ):
        assert needle in text
    assert "@" not in text


def test_notice_user_ids_collects_main_and_items():
    from nonebot_plugin_waifu import render

    assert render.notice_user_ids(_ctx()) == (20002, 30003)
