"""渲染纯函数：转义、必含字段、截断。**不装 htmlkit 也能跑。**"""

from __future__ import annotations


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


def test_build_html_escapes_user_input():
    """昵称是用户随手写的，含 `<` 不转义会破坏版面甚至注入标记。"""
    from nonebot_plugin_waifu import render

    html = render.build_html(_ctx())
    assert "<b>某人" not in html
    assert "&lt;b&gt;某人" in html


def test_build_html_contains_all_required_fields():
    """用户要求的字段一个都不能少。"""
    from nonebot_plugin_waifu import render

    html = render.build_html(_ctx())
    for needle in ("今日老婆", "@某人", "10001", "甲", "female", "2026-10-10 03:20"):
        assert needle in html, f"缺少字段：{needle}"


def test_build_html_embeds_avatar_as_data_uri():
    from nonebot_plugin_waifu import render

    html = render.build_html(_ctx())
    assert 'src="data:image/jpeg;base64,AAAA"' in html


def test_build_html_without_avatar_shows_placeholder():
    """取头像失败 → 占位块，**不是**碎图（htmlkit 取图失败是静默的）。"""
    from nonebot_plugin_waifu import render

    ctx = _ctx()
    ctx = render.CardContext(**{**ctx.__dict__, "avatar_data_uri": None})
    html = render.build_html(ctx)
    assert "<img" not in html
    assert "ph" in html


def test_css_has_no_flex_gap_and_pins_cjk_font():
    """Litehtml 不是 Chromium：`gap` 不生效、字体必须显式声明 CJK。"""
    from nonebot_plugin_waifu import render

    css = render._CSS
    # 剥掉注释再断言，否则注释里作为反面例子提到的 gap 会被误判
    import re

    stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    assert "gap:" not in stripped
    assert "Noto Sans CJK SC" in stripped or "WenQuanYi" in stripped


def test_sex_label_maps_unknown():
    from nonebot_plugin_waifu import render

    assert render.sex_label("female") == "女"
    assert render.sex_label("male") == "男"
    assert render.sex_label("unknown") == "未知"


def test_format_last_seen_is_stable():
    """最后发言时间按配置时区格式化，且对 None 有兜底。"""
    from nonebot_plugin_waifu import render

    assert render.format_last_seen(None) == "未知"
    assert render.format_last_seen(0.0)  # 不抛即可
