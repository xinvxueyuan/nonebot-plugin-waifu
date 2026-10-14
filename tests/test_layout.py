"""关系图布局纯函数：2 成员水平对 / ≥3 成员扇形、坐标确定、不越界、chip 不压名字。"""

from __future__ import annotations


def _mk(*edges):
    from nonebot_plugin_waifu import relations

    marriages = [
        (a, b, float(i)) for i, (a, b, kind) in enumerate(edges) if kind == "m"
    ]
    crushes = {a: b for a, b, kind in edges if kind == "c"}
    return relations.build_relations(marriages=marriages, crushes=crushes)


def _l():
    from nonebot_plugin_waifu import layout

    return layout


def _name_band(lay, box):
    """节点盒底部的名字带矩形（名字画在头像下方）。"""
    return (box.x, box.y + box.h - lay.NAME_BAND_H, box.x + box.w, box.y + box.h)


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _edge_vectors(cluster):
    """每条边的方向向量（用节点中心算，bbox 只有正数、丢了方向）。"""
    boxes = {n.uid: n for n in cluster.nodes}
    return [
        (boxes[ln.dst].cx - boxes[ln.src].cx, boxes[ln.dst].cy - boxes[ln.src].cy)
        for ln in cluster.lines
        if ln.src in boxes and ln.dst in boxes
    ]


def test_ego_layout_puts_focus_at_the_bottom_of_the_ring():
    """焦点簇是**扇形**：hub 在簇的下方中间、一度关系散在上方（不是整圆）。

    ⚠️ 旧断言是「hub 正好在簇的正中心」——那是**整圆**布局的性质。用户实拍
    （2026-10-14）：整圆会把 2 人簇摆成上下两点、3 人链摆成一条竖线
    （「请不要让线在一条直线上，我要的是连线」），所以改成扇形 + 内容归一化，
    这条断言随之改成扇形该有的性质（hub 在下、环上节点散开）。
    """
    lay = _l()
    g = _mk((1, 2, "m"), (3, 1, "c"), (4, 1, "c"))
    (cluster,) = lay.layout_graph(g.ego(1), focus=1)
    assert cluster.hub == 1
    by_uid = {n.uid: n for n in cluster.nodes}
    assert set(by_uid) == {1, 2, 3, 4}
    hub = by_uid[1]
    ring = [n for n in cluster.nodes if n.uid != 1]
    assert hub.cy > max(n.cy for n in ring)  # hub 落在下方
    assert round(hub.cx) == cluster.width // 2  # 仍在水平中轴上
    assert len({round(n.y) for n in ring}) >= 2  # 真的散开在四周，不是一列


def test_layout_is_deterministic_and_inside_canvas():
    lay = _l()
    g = _mk((1, 2, "m"), (1, 3, "c"), (1, 4, "c"), (1, 5, "c"))
    a = lay.layout_graph(g.ego(1), focus=1)
    b = lay.layout_graph(g.ego(1), focus=1)
    assert a == b
    for cluster in a:
        assert cluster.width > 0 and cluster.height > 0
        for n in cluster.nodes:
            assert n.x >= 0 and n.x + n.w <= cluster.width
            assert n.y >= 0 and n.y + n.h <= cluster.height


def test_lines_count_and_bbox_cover_segment():
    lay = _l()
    g = _mk((1, 2, "m"), (3, 1, "c"))
    (cluster,) = lay.layout_graph(g.ego(1), focus=1)
    assert len(cluster.lines) == 2
    for ln in cluster.lines:
        assert ln.w >= 1 and ln.h >= 1
        assert -180.0 <= ln.angle <= 180.0


def test_whole_graph_makes_one_cluster_per_component():
    lay = _l()
    g = _mk((1, 2, "m"), (3, 1, "c"), (8, 9, "m"))
    clusters = lay.layout_graph(g, focus=None)
    assert len(clusters) == 2
    assert {cluster.caption for cluster in clusters} == {"#1", "#2"}
    assert max(len(c.nodes) for c in clusters) == 3


def test_prefers_highest_degree_node_as_hub():
    lay = _l()
    g = _mk((1, 2, "m"), (3, 1, "c"), (4, 1, "c"))
    (cluster,) = lay.layout_graph(g, focus=None)
    assert cluster.hub == 1


# --------------------------------------------------------------------------- #
# 用户实拍（2026-10-14）：「请不要让线在一条直线上，我要的是连线」+「文字都和图重叠了」
# --------------------------------------------------------------------------- #
def test_two_member_cluster_is_a_horizontal_pair():
    """**2 成员簇 = 水平对**：同 y、中心水平相距 `PAIR_SPAN`。

    旧实现 `θ_i = -90 + i*360/n` 在 n=1 时把唯一的节点摆在 hub **正上方** ——
    2 人簇就是「上下两点一条竖线」，实拍图里 4 个簇看起来像 4 行列表。
    """
    lay = _l()
    g = _mk((1, 2, "m"))
    (cluster,) = lay.layout_graph(g, focus=None)
    a, b = cluster.nodes
    assert a.y == b.y  # 同 y
    assert abs(abs(a.cx - b.cx) - lay.PAIR_SPAN) <= 2  # 中心水平相距 PAIR_SPAN
    assert (
        cluster.height == lay.NODE_H + 2 * lay.PAD
    )  # 簇高只留上下 PAD（不再是 504px）
    (line,) = cluster.lines
    assert line.h <= 2 and line.w > lay.PAIR_SPAN // 2  # 边是**水平**线段


def test_star_cluster_never_has_two_collinear_edges():
    """≥3 成员簇：任意两条边**不共线**（叉积 ≠ 0）—— 实拍里 3 人链是一条竖线。

    ⚠️ 5 个环上节点时扇形步长正好 60°、第 0 与第 3 条边天然相差 180°（穿过 hub
    连成一条直线），靠 `_fan_angles` 的去共线兜底错开 —— 这条连同兜底一起钉住。
    """
    lay = _l()
    for members in (3, 4, 5, 6, 7):
        g = _mk(*[(1, i, "m") for i in range(2, members + 1)])
        (cluster,) = lay.layout_graph(g.ego(1), focus=1)
        vecs = _edge_vectors(cluster)
        assert len(vecs) == members - 1
        for i, va in enumerate(vecs):
            for vb in vecs[i + 1 :]:
                cross = va[0] * vb[1] - va[1] * vb[0]
                assert abs(cross) > 1e-6, f"{members} 成员簇里有共线的两条边"


def test_label_chip_is_centered_on_a_horizontal_edge():
    """水平边的 chip 压在中点居中（两个头像之间是空的，不会撞名字）。"""
    lay = _l()
    (cluster,) = lay.layout_graph(_mk((1, 2, "m")), focus=None)
    (line,) = cluster.lines
    lx, ly = lay._label_pos(line, cluster.nodes)
    assert abs((lx + lay.CHIP_W / 2) - (line.x + line.w / 2)) <= 1
    assert abs((ly + lay.CHIP_H / 2) - (line.y + line.h / 2)) <= 1


def test_label_chip_never_covers_a_name_band():
    """**chip 不许压节点名字带**（实拍：「文字都和图重叠了」）。

    竖向边（扇形里正上方那个节点）最容易撞，单独钉一遍纯函数 `_label_pos`。
    """
    lay = _l()
    g = _mk((1, 2, "m"), (1, 3, "m"), (1, 4, "m"))
    (cluster,) = lay.layout_graph(g.ego(1), focus=1)
    vertical = [ln for ln in cluster.lines if ln.h > ln.w + 10]
    assert vertical, "这个扇形里应当有一条近垂直的边"
    for line in vertical:
        lx, ly = lay._label_pos(line, cluster.nodes)
        chip = (lx, ly, lx + lay.CHIP_W, ly + lay.CHIP_H)
        for box in cluster.nodes:
            assert not _overlaps(chip, _name_band(lay, box)), (
                f"chip 压在 uid={box.uid} 的名字带上"
            )


def test_no_label_chip_covers_a_name_band_in_any_shape():
    """整簇不变量：**任何形态**（水平对 / 扇形 / 大小簇）的 chip 都不压名字带。"""
    lay = _l()
    graphs = (
        _mk((1, 2, "m")),
        _mk((1, 2, "m"), (1, 3, "m"), (1, 4, "m")),
        _mk((1, 2, "m"), (1, 3, "m"), (1, 4, "m"), (1, 5, "m"), (1, 6, "m")),
        _mk((1, 2, "m"), (2, 3, "m"), (4, 5, "m")),
    )
    for g in graphs:
        for cluster in lay.layout_graph(g, focus=None):
            for line in cluster.lines:
                chip = (
                    line.label_x,
                    line.label_y,
                    line.label_x + lay.CHIP_W,
                    line.label_y + lay.CHIP_H,
                )
                for box in cluster.nodes:
                    assert not _overlaps(chip, _name_band(lay, box)), (
                        f"chip 压在 uid={box.uid} 的名字带上"
                    )
