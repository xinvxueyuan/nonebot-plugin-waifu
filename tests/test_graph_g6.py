"""G6 绘制层（`graph_g6`）：数据映射纯函数 + 脚本装配 + 静态资源 provenance。

**不碰浏览器**（真出图在 `tests/test_graph_g6_render.py`）—— 这里钉的是：
layout 的坐标/配色/粗细怎么变成 G6 的 nodes/edges、端点圆外径一致、无向边没有端口、
胶囊与昵称的转义、g6.min.js 只内联一次、就绪旗在。
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import re
import xml.etree.ElementTree as ET

from nonebot_plugin_waifu import graph_g6, layout, relations, render

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _manual(*edges):
    """手工构 `RelationGraph`（`kind` 用字面量）。"""
    es = tuple(relations.RelationEdge(int(a), int(b), kind) for a, b, kind in edges)
    return relations.RelationGraph(
        edges=es,
        nodes=tuple(sorted({m for e in es for m in (e.src, e.dst)})),
        total_edges=len(es),
    )


def _data(cluster, index=1, **over):
    kw = {
        "nicknames": {},
        "avatars": {},
        "colors": render._EDGE_COLOR,
        "widths": render._EDGE_PX,
        "labels": render._EDGE_LABEL,
    }
    kw.update(over)
    return graph_g6.cluster_data(cluster, index=index, **kw)


def _nodes(data):
    return {n["id"]: n for n in data["nodes"]}


# --------------------------------------------------------------------------- #
# 坐标工具（用户实拍的老毛病：轴对齐线的 bbox 里有一个伪造的 1px 维度）
# --------------------------------------------------------------------------- #
def test_line_endpoints_mirror_the_bbox_diagonal():
    line = layout.EdgeLine(
        kind="marriage",
        src=1,
        dst=2,
        x=10,
        y=20,
        w=100,
        h=60,
        angle=math.degrees(math.atan2(60, 100)),
        label_x=0,
        label_y=0,
    )
    x1, y1, x2, y2 = graph_g6.line_endpoints(line)
    assert (round(x1), round(y1), round(x2), round(y2)) == (10, 20, 110, 80)


def test_axis_aligned_line_loses_the_fake_pixel_slope():
    """水平线的 bbox 高是伪造的 1px；端点要还原成**真正水平**的线。"""
    line = layout.EdgeLine("like", 1, 2, 0, 50, 200, 1, 0.0, 0, 0)
    x1, y1, x2, y2 = graph_g6.line_endpoints(line)
    assert y1 == y2 == 50.5
    assert (round(x1), round(x2)) == (0, 200)


def test_nickname_truncates_to_six_chars():
    assert graph_g6.truncate_name("后藤一里（烟雨版）") == "后藤一里（烟…"
    assert graph_g6.truncate_name("短名") == "短名"
    assert graph_g6.truncate_name("  空格也算  ") == "空格也算"


def test_chip_aabb_matches_layouts_collision_geometry():
    """胶囊的 AABB 必须与 layout 的碰撞口径**同源**（否则 chip 会压到节点盒）。"""
    anchor_x, anchor_y, angle = 100.0, 80.0, 30.0
    got = graph_g6.chip_aabb(anchor_x, anchor_y, angle)
    want = layout._rotated_chip_aabb(anchor_x, anchor_y, angle)
    assert all(abs(a - b) < 1e-9 for a, b in zip(got, want, strict=True))


# --------------------------------------------------------------------------- #
# 头像节点 / 占位
# --------------------------------------------------------------------------- #
def test_avatar_node_is_an_image_with_ring_and_truncated_label():
    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    data = _data(
        cluster,
        nicknames={1: "后藤一里（烟雨版）", 2: "乙"},
        avatars={1: "data:image/png;base64,AAAA"},
    )
    (node,) = [n for n in data["nodes"] if n["id"] == graph_g6.avatar_id(1)]
    assert node["type"] == "image"
    assert node["img"] == "data:image/png;base64,AAAA"
    assert node["label"] == "后藤一里（烟…"  # 6 字 + …（沿用旧口径）
    assert node["style"] == {
        "stroke": graph_g6.RING_COLOR,
        "lineWidth": graph_g6.RING_WIDTH,
    }
    # 节点坐标 = 节点盒中心（layout 的线端点就是按「离盒心 AVATAR/2+GAP」算的）
    box = next(b for b in cluster.nodes if b.uid == 1)
    assert (node["x"], node["y"]) == (box.cx, box.cy)


def test_avatar_less_member_gets_pink_placeholder_with_first_char():
    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    data = _data(cluster, nicknames={1: "甲", 2: "乙"})
    (node,) = [n for n in data["nodes"] if n["id"] == graph_g6.avatar_id(1)]
    svg = _svg_of(node["img"])
    assert graph_g6.PH_BG in svg and graph_g6.PH_FG in svg
    assert ">甲<" in svg  # 首字
    assert f'r="{graph_g6.NODE_SIZE / 2}"' in svg


def test_placeholder_svg_is_well_formed_xml():
    """⚠️ SVG 是 XML：字体栈里的双引号不转义 → 整张位图**静默不显示**（实测踩过）。"""
    svg = _svg_of(graph_g6.placeholder_uri("甲", 1))
    root = ET.fromstring(svg)  # 引号没转义的话这里直接炸
    assert root.tag.endswith("svg")
    assert graph_g6.FONT_STACK.split(",")[0].strip('"') in svg


def _svg_of(uri: str) -> str:
    import base64

    assert uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")


# --------------------------------------------------------------------------- #
# 边 + 隐藏锚点 + 端口圆
# --------------------------------------------------------------------------- #
def test_every_edge_maps_to_a_g6_edge_with_the_relation_color_and_width():
    (cluster,) = layout.layout_graph(
        _manual((1, 2, "marriage"), (1, 3, "crush"), (1, 4, "like")), focus=None
    )
    data = _data(cluster)
    assert len(data["edges"]) == len(cluster.lines) == 3
    by_stroke = {e["style"]["stroke"]: e for e in data["edges"]}
    assert set(by_stroke) == set(render._EDGE_COLOR.values())
    for edge in data["edges"]:
        style = edge["style"]
        assert style["endArrow"] is False
        assert style["lineWidth"] in (3, 4)
        assert "lineDash" not in style  # 用户口径：连线一律实线
    mar = next(e for e in data["edges"] if e["style"]["stroke"] == "#db2777")
    assert mar["style"]["lineWidth"] == render._EDGE_PX["marriage"] == 4


def test_edges_are_pinned_to_layout_endpoints_by_hidden_anchor_nodes():
    """G6 会把线裁到节点 bbox 边缘；端点必须靠 1px 隐藏锚点钉回 layout 的坐标。"""
    (cluster,) = layout.layout_graph(
        _manual((1, 2, "marriage"), (1, 3, "crush")), focus=None
    )
    data = _data(cluster)
    nodes = _nodes(data)
    for edge in data["edges"]:
        anchor = nodes[edge["source"]]
        assert anchor["style"]["opacity"] == 0  # 不画东西
        assert anchor["size"] == graph_g6.ANCHOR_SIZE == 1
    # 锚点坐标 == 该边两端（G6 自己在 0.5px 半径处收笔 → 亚像素差）
    ends = [(n["x"], n["y"]) for n in data["nodes"] if n["size"] == 1]
    for line in cluster.lines:
        x1, y1, x2, y2 = graph_g6.line_endpoints(line)
        assert any(
            all(abs(a - b) <= 0.5 for a, b in zip((x1, y1), p, strict=True))
            for p in ends
        )
        assert any(
            all(abs(a - b) <= 0.5 for a, b in zip((x2, y2), p, strict=True))
            for p in ends
        )


def test_filled_and_hollow_port_nodes_share_the_outer_diameter():
    """实心 = 发起方、空心 = 被指向的一方；**外径一致**。

    描边居中在半径上，所以空心圆的 `size` 要缩掉一个 `PORT_STROKE`。
    """
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    data = _data(cluster)
    ports = [n for n in data["nodes"] if n["id"].startswith("p1-")]
    assert len(ports) == 2
    filled, hollow = ports[0], ports[1]
    color = render._EDGE_COLOR["crush"]
    assert filled["style"] == {"fill": color, "stroke": color, "lineWidth": 0}
    assert hollow["style"] == {
        "fill": "#ffffff",
        "stroke": color,
        "lineWidth": graph_g6.PORT_STROKE,
    }
    assert filled["size"] == layout.PORT_D == 12
    assert hollow["size"] == layout.PORT_D - graph_g6.PORT_STROKE
    # 外径一致：实心 12；空心 9 + 两侧各 1.5px 描边 = 12
    assert hollow["size"] + graph_g6.PORT_STROKE == filled["size"]
    # 圆心就落在主线的两端
    (line,) = cluster.lines
    x1, y1, x2, y2 = graph_g6.line_endpoints(line)
    assert (filled["x"], filled["y"]) == (round(x1), round(y1))
    assert (hollow["x"], hollow["y"]) == (round(x2), round(y2))


def test_undirected_marriage_has_no_port_nodes():
    """夫妻无向 → 没有方向标记，也就没有端口圆（图例这么写的）。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    data = _data(cluster)
    assert not [n for n in data["nodes"] if n["id"].startswith("p1-")]
    assert len(data["edges"]) == 1


# --------------------------------------------------------------------------- #
# 关系名胶囊（自绘：G6 的 labelCfg.style.background 被静默忽略）
# --------------------------------------------------------------------------- #
def test_edge_label_is_a_white_capsule_rotated_with_the_line():
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    data = _data(cluster)
    (capsule,) = [n for n in data["nodes"] if n["id"] == graph_g6.capsule_id(1, 0)]
    svg = _svg_of(capsule["img"])
    root = ET.fromstring(svg)
    assert 'fill="#ffffff"' in svg  # 胶囊底
    assert f'stroke="{render._EDGE_COLOR["crush"]}"' in svg  # 1px 同色描边
    assert ">倾心<" in svg  # 关系名（`_EDGE_LABEL`）
    (line,) = cluster.lines
    deg = round(layout.label_rotation(line.angle), 1)
    assert f"rotate({deg} " in svg  # 随线旋转（锚点 = 底边中点）
    assert root.tag.endswith("svg")


def test_capsule_node_size_equals_its_bitmap_size():
    """G6 的 image 节点会把位图拉满节点：尺寸不一致 = 胶囊被缩放变形。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    data = _data(cluster)
    (capsule,) = [n for n in data["nodes"] if n["id"] == graph_g6.capsule_id(1, 0)]
    svg = _svg_of(capsule["img"])
    width, height = re.findall(r'width="([\d.]+)" height="([\d.]+)"', svg)[0]
    assert capsule["size"] == [float(width), float(height)]


def test_capsule_anchor_is_the_bottom_center_on_its_own_line():
    """Chip 底边中点必须落在**自己那条线**上（用户口径：标签贴线）。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "like")), focus=None)
    (line,) = cluster.lines
    x1, y1, x2, y2 = graph_g6.line_endpoints(line)
    ax, ay = line.label_x + layout.CHIP_W / 2, line.label_y + layout.CHIP_H
    # 点到直线距离（line 的方向向量与 ax,ay 的叉积）
    dx, dy = x2 - x1, y2 - y1
    dist = abs(dx * (ay - y1) - dy * (ax - x1)) / math.hypot(dx, dy)
    assert dist < 1.0


# --------------------------------------------------------------------------- #
# 脚本装配：g6.min.js 内联一次、一簇一容器、就绪旗
# --------------------------------------------------------------------------- #
def test_container_html_matches_the_cluster_box():
    (cluster,) = layout.layout_graph(_manual((1, 2, "marriage")), focus=None)
    html = graph_g6.container_html(2, cluster)
    assert graph_g6.container_id(2) == "g6-2"
    assert 'id="g6-2"' in html
    assert f"width:{cluster.width}px;height:{cluster.height}px" in html
    assert 'class="gk"' in html


def test_build_scripts_inlines_g6_once_and_inits_one_graph_per_cluster():
    clusters = layout.layout_graph(
        relations.build_relations(
            marriages=[(1, 2, 1.0), (3, 4, 2.0)], crushes={}, likes={}
        ),
        focus=None,
    )
    assert len(clusters) == 2
    script = graph_g6.build_scripts(
        clusters,
        nicknames={},
        avatars={},
        colors=render._EDGE_COLOR,
        widths=render._EDGE_PX,
        labels=render._EDGE_LABEL,
        g6_js="/*G6LIB*/",
    )
    assert script.count("/*G6LIB*/") == 1  # g6.min.js 只内联一次
    assert script.count("\n  render(") == 2  # 一簇一次初始化
    assert "__G6_LIB__" not in script and "__G6_CALLS__" not in script


def test_cluster_script_declares_the_frozen_g6_graph():
    """坐标是 layout 算好的：不许自适应/居中/动画；交互模式全关（静态出图）。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    script = graph_g6.build_scripts(
        [cluster],
        nicknames={},
        avatars={},
        colors=render._EDGE_COLOR,
        widths=render._EDGE_PX,
        labels=render._EDGE_LABEL,
        g6_js="/*G6LIB*/",
    )
    for needle in (
        "new G6.Graph(",
        "fitView: false",
        "fitCenter: false",
        "animate: false",
        "modes: { default: [] }",
        "type: 'image', size: 64",
        "type: 'line'",
        'render("g6-1", {',  # 数据 + 容器 id
        f"{cluster.width}, {cluster.height});",  # 簇宽高 1:1
        "window.__ready = true",
        graph_g6.FONT_STACK.split(",")[0].strip('"'),  # CJK 字体栈显式给
    ):
        assert needle in script, needle


def test_ready_flag_is_set_after_all_clusters_render():
    """就绪旗：G6 异步渲染，旗子不置位 `_shot` 就会超时回退文本（绝不发空图）。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    script = graph_g6.build_scripts(
        [cluster],
        nicknames={},
        avatars={},
        colors=render._EDGE_COLOR,
        widths=render._EDGE_PX,
        labels=render._EDGE_LABEL,
        g6_js="/*G6LIB*/",
    )
    assert script.index("window.__ready = false") < script.index("Promise.all(pending)")
    assert graph_g6.READY_EXPR == "window.__ready === true"
    # 图片先解码、再 render（否则截到「头像还没贴上」的图）
    assert script.index("new Image()") < script.index("graph.render()")


def test_missing_asset_raises_instead_of_silently_drawing_nothing(monkeypatch):
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    monkeypatch.setattr(graph_g6, "read_g6_js", lambda: None)
    try:
        graph_g6.build_scripts(
            [cluster],
            nicknames={},
            avatars={},
            colors=render._EDGE_COLOR,
            widths=render._EDGE_PX,
            labels=render._EDGE_LABEL,
        )
    except RuntimeError as e:
        assert "assets/g6.min.js" in str(e)
    else:  # pragma: no cover
        raise AssertionError("资产缺失时必须抛，让 render_graph 回退纯文本")


# --------------------------------------------------------------------------- #
# 注入防护：昵称进 JSON（canvas 画字不需要 HTML 转义），但整包 JSON 内联进 <script>
# --------------------------------------------------------------------------- #
def test_nicknames_cannot_break_out_of_the_inlined_script():
    """昵称里带 `</script>` / `<b>` 也不许出现在 HTML 里（否则整页脚本断掉/可注入）。"""
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    evil = "</script><script>alert(1)</script><b>"
    html = render.build_graph_html(
        [cluster], nicknames={1: evil, 2: "<b>乙"}, avatars={}
    )
    # ⚠️ 大字符串只比计数：直接 `not in` 断言一旦失败，pytest 生成解释时会把整页
    # 打印出来 —— 实测能把测试进程卡死几分钟（这也正是 MN3 变异体的捕获路径）。
    assert html.count("</script><script>alert") == 0
    assert html.count("<b>乙") == 0
    assert html.count("</script>") == 2  # 只有我们自己那两个闭合标签


def test_escaped_payload_round_trips_to_the_original_text():
    (cluster,) = layout.layout_graph(_manual((1, 2, "crush")), focus=None)
    evil = "</script><b>&"
    data = _data(cluster, nicknames={1: evil})
    payload = graph_g6._payload(data)
    assert "<" not in payload
    assert json.loads(payload)["nodes"][0]["label"] == evil[: graph_g6.NAME_MAX] + "…"


# --------------------------------------------------------------------------- #
# 静态资源 provenance（sha256 自证）
# --------------------------------------------------------------------------- #
def test_g6_asset_sha256_matches_provenance():
    """静态资源 provenance 自证：字节数 + sha256 与上游 CDN 一致。

    ⚠️ 校验前先做 **CRLF → LF 归一化**：本仓 `core.autocrlf=true`，在 Windows 克隆时
    工作树里的行尾会被换成 CRLF（`.gitattributes` 已把该文件标成 `-text` 不转换，
    双保险），直接哈希工作树文件会在别的机器上假红（上游 CDN 的字节是 LF）。
    """
    asset = ROOT / graph_g6.G6_ASSET
    raw = asset.read_bytes().replace(b"\r\n", b"\n")
    assert len(raw) == graph_g6.G6_BYTES
    assert hashlib.sha256(raw).hexdigest() == graph_g6.G6_SHA256
    assert (
        (ROOT / "assets" / "G6-LICENSE.txt")
        .read_text(encoding="utf-8")
        .startswith("MIT License")
    )


def test_read_g6_js_returns_the_asset():
    js = graph_g6.read_g6_js()
    assert js is not None
    # 读进来是**字符**数，`G6_BYTES` 是字节数（bundle 里有 200+ 个非 ASCII 字符）
    assert len(js.replace("\r\n", "\n").encode("utf-8")) == graph_g6.G6_BYTES
