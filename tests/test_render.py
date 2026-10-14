"""渲染纯函数：转义、必含字段、截断。**不装 htmlkit 也能跑。**"""

from __future__ import annotations

import base64

import pytest


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


def _own_html(html: str) -> str:
    """卡片标记 + **我们自己的**初始化脚本（剔掉内联的 g6.min.js）。

    ⚠️ 组件库里自带 `http` / `lineDash` / `linear-gradient` 这些词 —— 扫全页会把库的
    实现当成我们的输出、误判（实测被咬过）。第一个 `</script>` 之前是卡片标记 + 库。
    """
    head, _, rest = html.partition("</script>")
    return head.split("<script>", 1)[0] + rest.replace("<script>", "", 1)


def test_graph_html_puts_every_member_on_a_canvas_node():
    """昵称不再进 DOM（canvas 画字）—— 卡片里只该有容器、数据和内联脚本。

    ⚠️ 断言一律先算成**小值**（计数/列表）再比：卡片 HTML 有 1.5MB，
    拿它直接参与 `in` / `not in` 断言，一旦失败 pytest 生成解释时会把整页打印出来，
    实测能把整个测试进程卡死几分钟（踩过一次）。
    """
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    html = render.build_graph_html(
        clusters,
        nicknames={1: "甲<b>", 2: "乙", 3: "丙"},
        avatars={1: "data:image/jpeg;base64,AAAA"},
    )
    mine = _own_html(html)
    assert mine.count("<b>") == 0  # 昵称不进 HTML（数据里 `<` 转义成 \u003c）
    assert mine.count("\\u003cb>") == 1
    assert mine.count('"type":"image"') >= 3  # 三个头像节点（占位也是 image）
    assert mine.count("data:image/jpeg;base64,AAAA") == 1
    assert mine.count("data:image/svg+xml;base64,") >= 2  # 没头像的人用自绘占位圆
    assert mine.count("http") == 0  # 绝不出现网络图片 URL（头像全走 data URI）


def test_graph_html_retires_the_old_div_band_drawing():
    """旧绘制（色带/绝对定位 div/`.elab` chip）必须整体退役，别留一半。

    ⚠️ 只扫**卡片标记段**（第一个 `<script>` 之前）：内联的 g6.min.js 自带
    `linear-gradient`（组件库内部的渐变工具），扫全页会误判。
    """
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    card = html.split("<script>", 1)[0]
    assert len(card) < 20_000  # 卡片段就是那点标记，别把库扫进来
    leftovers = [
        d
        for d in ("linear-gradient", 'class="seg"', 'class="node"', 'class="elab"')
        if d in card
    ]
    assert leftovers == []
    assert not hasattr(render, "_segment_html")
    assert not hasattr(render, "_band_background")
    assert not hasattr(render, "_node_html")
    assert not hasattr(render, "_LABEL_TEMPLATE")


def test_graph_html_embeds_one_g6_container_per_cluster_and_the_library_once():
    from nonebot_plugin_waifu import render

    _g, clusters = _graph()
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    assert html.count('class="gk"') == len(clusters) == 1
    assert 'id="g6-1"' in html
    assert html.count("<script>") == 2  # 内联的 g6.min.js + 我们自己的初始化脚本
    assert html.count("new G6.Graph(") == 1  # 一簇一个 Graph 实例
    # 就绪旗：G6 异步渲染，不置位就会截到空图（render_graph 靠它等）
    assert "window.__ready = true" in html
    assert html.count("<script src") == 0
    assert html.count("assets/g6.min.js") == 0  # 路径不该出现（内容已内联）


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
    """长名字（「后藤一里（烟雨版）」）会超宽折行压到图上 → 截到 6 字 + `…`。

    现在名字画在 canvas 上（G6 的节点标签），但截断口径不变 ——
    卡片 HTML 的 JSON 数据里必须是截过的。
    """
    from nonebot_plugin_waifu import graph_g6, layout, relations, render

    g = relations.build_relations(marriages=[(1, 2, 1.0)], crushes={}, likes={})
    clusters = layout.layout_graph(g, focus=None)
    html = render.build_graph_html(
        clusters, nicknames={1: "后藤一里烟雨版", 2: "乙"}, avatars={}
    )
    assert graph_g6.NAME_MAX == 6
    assert render.NAME_MAX == graph_g6.NAME_MAX  # 两处别各写一个数
    # 大字符串只比计数（直接 `in` 断言一旦失败会把整页打印出来，能把进程卡死）
    assert html.count("后藤一里烟雨…") == 1
    assert html.count("后藤一里烟雨版") == 0  # 第 7 个字以后不再上图


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

    用户 2026-10-14：「wife 插件的虚线的效果很差，都用实线」—— 旧实现靠
    `_EDGE_DASHES` 全 1；现在线是 G6 画的（`type:'line'`，不给 `lineDash` 就是实线），
    所以「一律实线」改由「数据里没有任何 `lineDash`」钉住。
    """
    from nonebot_plugin_waifu import layout, render

    assert render._EDGE_COLOR["like"] == "#0ea5e9"
    assert render._EDGE_COLOR["like"] != render._EDGE_COLOR["crush"]
    assert render._EDGE_PX["like"] == 3
    assert render._EDGE_COLOR["marriage"] == "#db2777"
    assert render._EDGE_PX["marriage"] == 4
    assert render._EDGE_COLOR["crush"] == "#f59e0b"
    assert render._EDGE_PX["crush"] == 3
    g = _manual((1, 2, "marriage"), (3, 4, "crush"), (5, 6, "like"))
    html = render.build_graph_html(
        layout.layout_graph(g, focus=None), nicknames={}, avatars={}
    )
    mine = _own_html(html)
    assert mine.count("lineDash") == 0  # 全表口径：一个 kind 都不许变成虚线
    assert mine.count('"lineWidth":4') == 1
    assert mine.count('"lineWidth":3') >= 2


def test_ports_render_as_filled_and_hollow_circles_in_the_edge_color():
    """端口圆：**起点实心**（同色填充）、**终点空心**（白底 + 同色描边）、外径一致。

    2026-10-15 起画在 G6 里（小圆节点）—— 断言改成看卡片里的数据，口径不变：
    空心靠 `size` 缩一个描边宽、让外径与实心一致（canvas 的描边居中在半径上）。
    """
    from nonebot_plugin_waifu import graph_g6, layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    html = render.build_graph_html([cluster], nicknames={}, avatars={})
    color = render._EDGE_COLOR["crush"]
    # 大字符串只比计数（直接 `in` 断言一旦失败会把整页打印出来，能把进程卡死）
    assert html.count('"id":"p1-') == 2  # 有向边两端各一个
    assert html.count(f'"fill":"{color}","stroke":"{color}","lineWidth":0') == 1  # 实心
    assert (
        html.count(
            f'"fill":"#ffffff","stroke":"{color}","lineWidth":{graph_g6.PORT_STROKE}'
        )
        == 1
    )  # 空心
    assert html.count('"size":12') == 1
    assert html.count(f'"size":{layout.PORT_D - graph_g6.PORT_STROKE}') == 1
    assert html.count('"endArrow":false') == 1  # 不再画箭头


def test_ports_absent_on_undirected_marriage_edges():
    """夫妻（无向）没有端口圆 —— 方向标记只属于有向边。"""
    from nonebot_plugin_waifu import layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    html = render.build_graph_html([cluster], nicknames={}, avatars={})
    assert html.count('"id":"p1-') == 0
    assert html.count('"edges":[{"source"') == 1  # 线还在（锚点连出来的）


def test_edge_label_is_a_rotated_capsule_node_in_the_card():
    """关系名 = **自绘白底胶囊**（G6 的 `labelCfg.style.background` 实测被静默忽略）。

    旋转角与 `layout.label_rotation` 同源（胶囊贴着自己的线转），
    这里只断言卡片里确实带了胶囊节点与它的位图；几何细节在 `tests/test_graph_g6.py`。
    """
    from nonebot_plugin_waifu import graph_g6, layout, render

    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    (line,) = cluster.lines
    html = render.build_graph_html([cluster], nicknames={}, avatars={})
    assert html.count(f'"id":"{graph_g6.capsule_id(1, 0)}"') == 1
    svg = base64.b64decode(
        graph_g6.capsule_uri(
            render._EDGE_LABEL["crush"],
            render._EDGE_COLOR["crush"],
            line.label_x + layout.CHIP_W / 2,
            line.label_y + layout.CHIP_H,
            line.angle,
        ).split(",", 1)[1]
    ).decode("utf-8")
    assert "倾心" in svg  # 关系名写在胶囊里
    assert f"rotate({round(layout.label_rotation(line.angle), 1)} " in svg  # 随线旋转
    # 旧 `.elab` 的 CSS 口径（transform-origin）已随 div 绘制一起退役
    assert ".elab" not in render._GRAPH_CSS


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
    assert legend in html  # 图例是短字符串，直接比没问题
    names = {1: "甲", 2: "乙", 3: "丙", 4: "丁", 5: "戊", 6: "己"}
    text = render.build_graph_fallback_text(g, nicknames=names)
    assert legend in text
    assert "夫妻" in text and "倾心" in text and "喜欢" in text
    assert html.count("倾心→") == 0 and "倾心→" not in text


def test_like_edge_renders_its_own_color_and_label():
    """「喜欢」边用**自己的蓝色** + 自己的关系名（不是倾心的琥珀/文案）。

    关系名画在 canvas 上（自绘胶囊的位图里），所以这里解 base64 看 SVG，
    顺手把三种 kind 的配色都在同一条图上验一遍。
    """
    from nonebot_plugin_waifu import graph_g6, layout, render

    g = _manual((1, 2, "like"), (1, 3, "crush"), (1, 4, "marriage"))
    clusters = layout.layout_graph(g, focus=1)
    html = render.build_graph_html(clusters, nicknames={}, avatars={})
    mine = _own_html(html)
    for kind in ("marriage", "crush", "like"):
        # 边 + 端口圆都带这个颜色，所以只断言「至少出现」，关键是每种都在
        assert mine.count(f'"stroke":"{render._EDGE_COLOR[kind]}"') >= 1, kind
    (cluster,) = clusters
    for line in cluster.lines:
        svg = base64.b64decode(
            graph_g6.capsule_uri(
                render._EDGE_LABEL[line.kind],
                render._EDGE_COLOR[line.kind],
                line.label_x + layout.CHIP_W / 2,
                line.label_y + layout.CHIP_H,
                line.angle,
            ).split(",", 1)[1]
        ).decode("utf-8")
        assert f">{render._EDGE_LABEL[line.kind]}<" in svg
        assert "倾心→" not in svg


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


# --------------------------------------------------------------------------- #
# `_shot` 的「等就绪再截图」：关系图是 G6 异步渲染的，不等就会截到空图
# --------------------------------------------------------------------------- #
def _fake_page_ctx(record: list):
    class _Page:
        async def goto(self, url):
            record.append(("goto", url))

        async def set_content(self, html, wait_until=None):
            record.append(("set_content", wait_until, "g6" in html))

        async def wait_for_function(self, expr, timeout=None):
            record.append(("wait_for_function", expr, timeout))

        async def screenshot(self, **kw):
            record.append(("screenshot", kw))
            return b"PNG"

    class _Ctx:
        async def __aenter__(self):
            record.append(("enter",))
            return _Page()

        async def __aexit__(self, *_exc) -> bool:
            record.append(("exit",))
            return False

    def _factory(*_a, **kw):
        record.append(("new_page", kw))
        return _Ctx()

    return _factory


def test_shot_with_wait_for_waits_for_the_condition_before_screenshotting(monkeypatch):
    """**防「截到空图」的关键**：勾就绪旗（`wait_for_function`）必须在截图之前。"""
    import asyncio

    from nonebot_plugin_waifu import graph_g6, render

    record: list = []
    monkeypatch.setattr(render, "_load_page_ctx", lambda: _fake_page_ctx(record))
    png = asyncio.run(
        render._shot(
            "<html><body>g6</body></html>", width=920, wait_for=graph_g6.READY_EXPR
        )
    )
    assert png == b"PNG"
    order = [step[0] for step in record]
    assert order == [
        "new_page",
        "enter",
        "goto",
        "set_content",
        "wait_for_function",
        "screenshot",
        "exit",
    ]
    steps = {step[0]: step for step in record}
    assert steps["wait_for_function"][1] == graph_g6.READY_EXPR
    assert steps["wait_for_function"][2] == render.READY_TIMEOUT_MS
    assert steps["screenshot"][1]["full_page"] is True
    assert steps["new_page"][1]["viewport"] == {"width": 920, "height": 100}


def test_shot_without_wait_for_keeps_using_html_to_pic(monkeypatch):
    """不带 `wait_for` 的图（抽取卡/回执卡）走老路，别顺手改渲染管线。"""
    import asyncio

    from nonebot_plugin_waifu import render

    calls: list = []

    async def _fake(html, **kw):
        calls.append((html, kw))
        return b"PNG"

    monkeypatch.setattr(render, "_load_renderer", lambda: _fake)
    monkeypatch.setattr(
        render,
        "_load_page_ctx",
        lambda: pytest.fail("不带 wait_for 时不该去拿 page"),
    )
    assert asyncio.run(render._shot("<html></html>", width=720)) == b"PNG"
    ((_html, kw),) = calls
    assert kw == {
        "viewport": {"width": 720, "height": 100},
        "full_page": True,
        "device_scale_factor": 1,
    }
