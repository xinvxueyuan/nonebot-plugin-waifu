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
    """用户要求的字段一个都不能少（与 QQ号 重复的 @ 列已按用户要求删除）。"""
    from nonebot_plugin_waifu import render

    html = render.build_html(_ctx())
    for needle in ("今日老婆", "10001", "甲", "female", "2026-10-10 03:20"):
        assert needle in html, f"缺少字段：{needle}"


def test_build_html_drops_redundant_mention_column():
    """**@ 列与 QQ号 列值相同 → 按用户要求删掉 @ 列**（2026-10-10）。

    - `@：` 整行（及 `{mention}` 占位符渲染出的值）不得再出现；
    - `QQ号：` 行必须保留。
    """
    from nonebot_plugin_waifu import render

    html = render.build_html(_ctx())
    assert "@：" not in html
    assert "@某人" not in html  # mention 的值也不再出现
    assert "QQ号：" in html
    assert "10001" in html


def test_build_fallback_text_drops_redundant_mention_line():
    """纯文本回退的字段与图一致：同样去掉重复的 @ 行（QQ号 行在）。"""
    from nonebot_plugin_waifu import render

    text = render.build_fallback_text(_ctx())
    assert "@某人" not in text
    assert "QQ号：10001" in text


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


def test_to_data_uri_builds_correct_header(tmp_path):
    """`data_scheme_img_fetcher` 要求 header 里有 `base64` 字样，格式必须精确。"""
    from nonebot_plugin_waifu import render

    uri = render.to_data_uri(b"\x89PNG\r\n", mime="image/png")
    assert uri.startswith("data:image/png;base64,")
    assert len(uri.split(",", 1)[1]) > 0


def test_avatar_cache_key_is_url_hash():
    from nonebot_plugin_waifu import render

    url = render.avatar_url(1)
    k1 = render._avatar_cache_key(url)
    k2 = render._avatar_cache_key(render.avatar_url(1))
    k3 = render._avatar_cache_key(render.avatar_url(2))
    assert k1 == k2 != k3


def test_avatar_url_matches_lingchu_frontend_shape():
    """头像 URL 形态**照 lingchu-bot 前端**（用户点名参照）。

    前端 `apps/webui/server/qq.ts`：
        QQ_AVATAR_BASE = "https://q.qlogo.cn/headimg_dl"
        `${BASE}?dst_uin=${qq}&spec=${spec}&img_type=jpg`
    """
    from nonebot_plugin_waifu import render

    assert render.avatar_url(12345) == (
        "https://q.qlogo.cn/headimg_dl?dst_uin=12345&spec=640&img_type=jpg"
    )


def test_fetch_avatar_data_uri_returns_none_on_failure(monkeypatch):
    """**失败必须返回 None 且不抛** —— htmlkit 取图失败是静默的，我们要显式降级。"""
    from nonebot_plugin_waifu import render

    async def boom(*_a, **_kw):
        raise RuntimeError("network down")

    monkeypatch.setattr(render, "_download", boom)
    import asyncio

    assert asyncio.run(render.fetch_avatar_data_uri(10001)) is None


def test_fetch_avatar_data_uri_success(monkeypatch):
    from nonebot_plugin_waifu import render

    async def ok(_url):
        return b"\xff\xd8\xff"

    monkeypatch.setattr(render, "_download", ok)
    import asyncio

    uri = asyncio.run(render.fetch_avatar_data_uri(10001))
    assert uri is not None and uri.startswith("data:image/jpeg;base64,")


def test_avatar_failure_is_not_negatively_cached(monkeypatch):
    """**失败不写负缓存** —— 否则一次网络抖动会让该头像永久缺失。"""
    from nonebot_plugin_waifu import render

    render._AVATAR_CACHE.clear()
    calls = {"n": 0}

    async def flaky(_url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first fails")
        return b"\xff\xd8\xff"

    monkeypatch.setattr(render, "_download", flaky)
    import asyncio

    assert asyncio.run(render.fetch_avatar_data_uri(4242)) is None
    assert asyncio.run(render.fetch_avatar_data_uri(4242)) is not None
    assert calls["n"] == 2


def _graph():
    from nonebot_plugin_waifu import layout, relations

    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 1}, max_edges=20)
    return g, layout.layout_graph(g, focus=1)


def test_graph_html_escapes_nicknames_and_draws_nodes():
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    html = render.build_graph_html(
        clusters,
        nicknames={1: "甲<b>", 2: "乙", 3: "丙"},
        avatars={1: "data:image/jpeg;base64,AAAA"},
    )
    assert "<b>" not in html and "甲&lt;b&gt;" in html
    assert html.count('class="node') == 3
    assert "data:image/jpeg;base64,AAAA" in html
    assert 'class="ph-av"' in html  # 没有头像的人用首字占位
    assert "夫妻" in html and "倾心" in html  # 边上直接写关系名
    assert "http" not in html  # 绝不出现网络图片 URL


def test_graph_html_contains_edges_with_gradient_bands():
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert (
        html.count("linear-gradient") >= 2
    )  # 主方案；备选方案改断言 <div class="seg">


def test_graph_fallback_text_has_no_at_and_lists_relations():
    from nonebot_plugin_waifu import relations, render

    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 1})
    text = render.build_graph_fallback_text(g, nicknames={1: "甲", 2: "乙", 3: "丙"})
    assert "夫妻" in text and "倾心" in text and "甲" in text
    assert "@" not in text


def test_graph_title_switches_with_focus():
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    a = render.build_graph_html(clusters, nicknames={1: "甲"}, avatars={}, focus=1)
    b = render.build_graph_html(clusters, nicknames={1: "甲"}, avatars={})
    assert "以「甲」为中心" in a and "以「甲」为中心" not in b
