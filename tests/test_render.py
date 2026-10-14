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

    g = relations.build_relations(
        marriages=[(1, 2, 1.0)], crushes={3: 1}, likes={}, max_edges=20
    )
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

    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 1}, likes={})
    text = render.build_graph_fallback_text(g, nicknames={1: "甲", 2: "乙", 3: "丙"})
    assert "夫妻" in text and "倾心" in text and "甲" in text
    assert "@" not in text


def test_graph_title_switches_with_focus():
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    a = render.build_graph_html(clusters, nicknames={1: "甲"}, avatars={}, focus=1)
    b = render.build_graph_html(clusters, nicknames={1: "甲"}, avatars={})
    assert "以「甲」为中心" in a and "以「甲」为中心" not in b


# --------------------------------------------------------------------------- #
# 用户实拍（2026-10-14）：「4 个 2 成员簇各占一行、整图 2271px 高」+ 长名字折行压图
# --------------------------------------------------------------------------- #
def test_two_member_clusters_share_one_row():
    """**2 成员簇两列并排**：两个 2 成员簇落在同一个 `.row` 里。"""
    from nonebot_plugin_waifu import layout, relations, render

    g = relations.build_relations(
        marriages=[(1, 2, 1.0), (3, 4, 2.0)], crushes={}, likes={}
    )
    clusters = layout.layout_graph(g, focus=None)
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert html.count('class="row"') == 1
    assert html.count('class="cluster"') == 2
    row = html[html.index('class="row"') : html.index('class="footer"')]
    assert row.count('class="cluster"') == 2  # 两个簇都在这一行里


def test_big_cluster_gets_its_own_row():
    """≥3 成员簇独占一行（不跟 2 成员簇挤在一行）。"""
    from nonebot_plugin_waifu import layout, relations, render

    g = relations.build_relations(
        marriages=[(1, 2, 1.0)], crushes={3: 1, 4: 5}, likes={}
    )
    clusters = layout.layout_graph(g, focus=None)
    assert sorted(len(c.nodes) for c in clusters) == [2, 3]
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert html.count('class="row"') == 2
    assert html.count('class="cluster"') == 2


def test_node_name_is_truncated_to_six_chars():
    """长名字（「后藤一里（烟雨版）」）会超宽折行压到图上 → 截到 6 字 + `…`。"""
    from nonebot_plugin_waifu import layout, relations, render

    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={}, likes={})
    clusters = layout.layout_graph(g, focus=None)
    html = render.build_graph_html(
        clusters, nicknames={1: "后藤一里烟雨版", 2: "乙"}, avatars={}
    )
    assert render.NAME_MAX == 6
    assert "后藤一里烟雨…" in html
    assert "后藤一里烟雨版" not in html  # 第 7 个字以后不再上图


def test_graph_card_is_wider_than_the_plain_card():
    """关系图卡片比普通卡片宽（2 成员簇两列并排要 808px，640 的卡片放不下）。"""
    from nonebot_plugin_waifu import layout, relations, render

    assert render.GRAPH_CARD_WIDTH > render.CARD_WIDTH
    assert render.GRAPH_MAX_WIDTH >= render.GRAPH_CARD_WIDTH + 32  # body padding 16*2
    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={}, likes={})
    clusters = layout.layout_graph(g, focus=None)
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert f"width: {render.GRAPH_CARD_WIDTH}px" in html  # 卡片宽注入进 CSS
    assert ".row { display: block" in html  # 行容器
    assert "display: inline-block" in html  # 簇按 inline-block 排（两列并排）


# --------------------------------------------------------------------------- #
# 用户 2026-10-14（二）：标签不再靠「→」字符表方向（改画真箭头）+ 新关系「喜欢」
# --------------------------------------------------------------------------- #
def _manual(*edges):
    """手工构 `RelationGraph`（`kind` 用字面量，含 `like`）。"""
    from nonebot_plugin_waifu import relations

    es = tuple(relations.RelationEdge(int(a), int(b), kind) for a, b, kind in edges)
    return relations.RelationGraph(
        edges=es,
        nodes=tuple(sorted({m for e in es for m in (e.src, e.dst)})),
        total_edges=len(es),
    )


def test_edge_label_table_drops_the_text_arrow_and_adds_like():
    """「倾心→」里的箭头字符删掉（方向改由图上真箭头表达）；新增「喜欢」。"""
    from nonebot_plugin_waifu import render

    assert render._EDGE_LABEL == {"marriage": "夫妻", "crush": "倾心", "like": "喜欢"}
    assert "→" not in "".join(render._EDGE_LABEL.values())


def test_like_edge_is_blue_solid_and_does_not_clash_with_crush():
    """「喜欢」= 蓝色（#0ea5e9）3px **实线**；三种关系一律实线（配色/粗细不变）。

    用户 2026-10-14：「wife 插件的虚线的效果很差，都用实线」—— 倾心/喜欢
    原先的虚线段（`_EDGE_DASHES=4`）取消，颜色仍是唯一区分手段。
    """
    from nonebot_plugin_waifu import render

    assert render._EDGE_COLOR["like"] == "#0ea5e9"
    assert render._EDGE_COLOR["like"] != render._EDGE_COLOR["crush"]
    assert render._EDGE_PX["like"] == 3
    assert render._EDGE_DASHES["like"] == 1  # 实线
    assert render._EDGE_COLOR["marriage"] == "#db2777"
    assert render._EDGE_PX["marriage"] == 4 and render._EDGE_DASHES["marriage"] == 1
    assert render._EDGE_COLOR["crush"] == "#f59e0b"
    assert render._EDGE_PX["crush"] == 3 and render._EDGE_DASHES["crush"] == 1
    # 全表口径：三个 kind 一个不剩地都是实线（别再偷偷加回虚线）
    assert render._EDGE_DASHES == {"marriage": 1, "crush": 1, "like": 1}


def test_ports_render_as_filled_and_hollow_circles_in_the_edge_color():
    """端口圆：**起点实心**（同色填充）、**终点空心**（白底 + 同色描边）、外径一致。

    用户 2026-10-14：「不要箭头了，线条两边一个空心圆一个实心圆，作为节点的接口」。
    """
    from nonebot_plugin_waifu import layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    html = render._cluster_html(cluster, nicknames={}, pool={})
    color = render._EDGE_COLOR["crush"]
    assert html.count('class="port"') == 2  # 有向边两端各一个
    assert f"background:{color}" in html  # 实心
    assert f"background:#ffffff;border:{render.PORT_STROKE}px solid {color}" in html
    # 外径一致（border-box 才做得到），且不含任何箭头痕迹
    assert html.count(f"width:{layout.PORT_D}px;height:{layout.PORT_D}px") == 2
    assert 'class="seg"' in html  # 主线还在


def test_ports_absent_on_undirected_marriage_edges():
    """夫妻（无向）没有端口圆 —— 方向标记只属于有向边。"""
    from nonebot_plugin_waifu import layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    html = render._cluster_html(cluster, nicknames={}, pool={})
    assert 'class="port"' not in html


def test_label_is_rotated_along_its_line_in_the_html():
    """标签**真的转了**：`transform:rotate(θdeg)`，且绕**底边中点**转。

    θ 与 `layout.label_rotation` 同源。

    litehtml 实测不支持 `transform`（静默不转），换 Chromium 正是为了这条 —— 回归钉住。
    """
    from nonebot_plugin_waifu import layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    (line,) = cluster.lines
    html = render._cluster_html(cluster, nicknames={}, pool={})
    deg = round(layout.label_rotation(line.angle), 1)
    assert f"transform:rotate({deg}deg)" in html
    # 旋转原点是底边中点 = 线上那个锚点
    assert "transform-origin: 50% 100%" in render._GRAPH_CSS


def test_graph_footer_and_text_fallback_share_the_new_legend():
    """图例 footer 与纯文本回退用同一条新图例（三种关系的箭头含义写全）。"""
    from nonebot_plugin_waifu import layout, render

    legend = (
        "粉色=夫妻（双向） · 琥珀=倾心 · 蓝色=喜欢 · "
        "有向边两端：实心圆=发起方，空心圆=被指向的一方"
    )
    g = _manual((1, 2, "marriage"), (3, 4, "crush"), (5, 6, "like"))
    html = render.build_graph_html(
        layout.layout_graph(g, focus=None), nicknames={}, avatars={}
    )
    assert legend in html
    names = {1: "甲", 2: "乙", 3: "丙", 4: "丁", 5: "戊", 6: "己"}
    text = render.build_graph_fallback_text(g, nicknames=names)
    assert legend in text
    assert "夫妻" in text and "倾心" in text and "喜欢" in text
    assert "倾心→" not in html and "倾心→" not in text


def test_like_edge_renders_its_own_color_and_label():
    """「喜欢」边在 HTML 里用自己的蓝色 + 「喜欢」chip（不是倾心的琥珀/文案）。"""
    from nonebot_plugin_waifu import layout, render

    g = _manual((1, 2, "like"), (1, 3, "crush"), (1, 4, "marriage"))
    clusters = layout.layout_graph(g, focus=1)
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert "喜欢" in html and "#0ea5e9" in html
    assert "#f59e0b" in html and "#db2777" in html


def test_status_badge_and_avatars_are_vertically_centered():
    """胶囊/头像与同行文字**垂直居中**（用户 2026-10-14：「状态胶囊没有对齐文字」）。

    实测（Chromium 布局引擎）：inline span 按基线对齐 → 胶囊中心比标题文字中心低
    **4.5px**；行内小人头偏 1.5px；抽取卡的头像行偏 **9px**。改成 flex +
    `align-items: center` 后三处偏量都是 **0.0px**。
    """
    from nonebot_plugin_waifu import render

    assert (
        ".title { display: flex; align-items: center; gap: 10px; }"
        in render._NOTICE_CSS
    )
    assert (
        ".item { display: flex; align-items: center; margin-top: 8px; }"
        in render._NOTICE_CSS
    )
    assert ".body { display: flex; align-items: center; }" in render._CSS
    # 胶囊自己撑高度（line-height:1 + 上下 5px），别再靠 margin 挤位置
    assert "margin-left: 10px" not in render._NOTICE_CSS
