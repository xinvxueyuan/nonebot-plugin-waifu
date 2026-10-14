"""关系图布局纯函数：2 成员水平对 / ≥3 成员扇形、坐标确定、不越界、chip 不压名字。

（2026-10-14 二：有向边 dst 端加**真箭头**两翼 + 同对有向双线错开，见文件末尾。）
"""

from __future__ import annotations

import math


def _mk(*edges):
    from nonebot_plugin_waifu import relations

    marriages = [
        (a, b, float(i)) for i, (a, b, kind) in enumerate(edges) if kind == "m"
    ]
    crushes = {a: b for a, b, kind in edges if kind == "c"}
    likes = {a: b for a, b, kind in edges if kind == "k"}
    return relations.build_relations(marriages=marriages, crushes=crushes, likes=likes)


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


def test_label_chip_bottom_edge_sits_on_a_horizontal_edge():
    """水平边的 chip：**底边中点在线上**、且压在中点（用户 2026-10-14 口径）。"""
    lay = _l()
    (cluster,) = lay.layout_graph(_mk((1, 2, "m")), focus=None)
    (line,) = cluster.lines
    ax = line.label_x + lay.CHIP_W / 2
    ay = line.label_y + lay.CHIP_H
    assert abs(ax - (line.x + line.w / 2)) <= 1
    assert abs(ay - (line.y + line.h / 2)) <= 1


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


# --------------------------------------------------------------------------- #
# 用户 2026-10-14（二）：「倾心→」文字标签不够明确 → 方向改由 dst 端**真箭头**承担；
# 新增「喜欢」（抽到每日老婆即喜欢，无需确认）—— 同样有箭头；同对有向双线错开。
# --------------------------------------------------------------------------- #
def _dg(*edges):
    """手工构 `RelationGraph`（`kind` 可直接用 `like` 字面量）。"""
    from nonebot_plugin_waifu import relations

    es = tuple(relations.RelationEdge(int(a), int(b), kind) for a, b, kind in edges)
    return relations.RelationGraph(
        edges=es,
        nodes=tuple(sorted({m for e in es for m in (e.src, e.dst)})),
        total_edges=len(es),
    )


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _ends(line):
    """线段两端点：bbox 只有正数、方向丢了 —— 用 `angle` 把符号还回来。"""
    rad = math.radians(line.angle)
    cx, cy = line.x + line.w / 2, line.y + line.h / 2
    half = math.hypot(line.w, line.h) / 2
    return (cx - math.cos(rad) * half, cy - math.sin(rad) * half), (
        cx + math.cos(rad) * half,
        cy + math.sin(rad) * half,
    )


def test_marriage_has_no_ports_but_crush_and_like_do():
    """端口圆按**有向性**给：夫妻（无向）没有；倾心/喜欢两端各一个（起点实心、终点空心）。

    用户 2026-10-14：「不要箭头了，线条两边一个空心圆一个实心圆，作为节点的端口」。
    """
    lay = _l()
    (marriage,) = lay.layout_graph(_dg((1, 2, "marriage")), focus=None)
    assert marriage.ports == ()
    for kind in ("crush", "like"):
        (cluster,) = lay.layout_graph(_dg((1, 2, kind)), focus=None)
        assert len(cluster.ports) == 2, f"{kind} 边两端应各有一个端口圆"
        assert sorted(p.filled for p in cluster.ports) == [False, True]


def test_ports_sit_on_the_two_line_ends_with_filled_at_src():
    """**实心圆在上游端（发起方）、空心圆在下游端（被指向的一方）** —— 方向靠它读。"""
    lay = _l()
    for kind in ("crush", "like"):
        (cluster,) = lay.layout_graph(_dg((3, 7, kind)), focus=None)
        boxes = {n.uid: n for n in cluster.nodes}
        src = (boxes[3].cx, boxes[3].cy)
        dst = (boxes[7].cx, boxes[7].cy)
        (solid,) = [p for p in cluster.ports if p.filled]
        (hollow,) = [p for p in cluster.ports if not p.filled]
        assert _dist((solid.cx, solid.cy), src) < _dist((solid.cx, solid.cy), dst)
        assert _dist((hollow.cx, hollow.cy), dst) < _dist((hollow.cx, hollow.cy), src)
        # 两边都落在线上（端点 = 离节点盒心 AVATAR/2 + GAP）
        (line,) = cluster.lines
        for p in cluster.ports:
            assert _perp_dist((p.cx, p.cy), line) <= 2
            assert (
                abs(_dist((p.cx, p.cy), src) - (lay.AVATAR / 2 + lay.GAP)) <= 2
                or abs(_dist((p.cx, p.cy), dst) - (lay.AVATAR / 2 + lay.GAP)) <= 2
            )


def test_ports_stay_clear_of_every_avatar():
    """端口圆不插进头像：**圆边**离每个节点中心 ≥ 头像半径 + 4px 余量。"""
    lay = _l()
    graphs = (
        _dg((1, 2, "marriage"), (1, 3, "crush"), (1, 4, "like")),
        _dg((1, 2, "crush"), (2, 1, "like"), (5, 6, "like")),
    )
    for g in graphs:
        for cluster in lay.layout_graph(g, focus=None):
            centers = [(n.cx, n.cy) for n in cluster.nodes]
            for port in cluster.ports:
                for c in centers:
                    edge = _dist((port.cx, port.cy), c) - lay.PORT_D / 2
                    assert edge >= lay.AVATAR / 2 + 4, (
                        f"端口圆插进了 uid={c} 的头像（圆边距 {edge:.1f}px）"
                    )


def test_double_directed_pair_gets_parallel_lines_and_separated_chips():
    """同一对人的两条有向边：两条线各自垂直错开 ±7px、chip 也跟着错开。

    A→B 倾心 + B→A 喜欢；两条线只隔 14px 而 chip 高 26px，不错开就会互相压住。
    """
    lay = _l()
    (cluster,) = lay.layout_graph(_dg((1, 2, "crush"), (2, 1, "like")), focus=None)
    assert len(cluster.lines) == 2
    assert len(cluster.ports) == 4  # 每条线两端各一个端口圆
    l1, l2 = cluster.lines
    assert abs((l1.angle - l2.angle) % 180) < 1e-6  # 共轴（A→B 与 B→A 反向）
    mid1 = (l1.y + l1.h / 2, l2.y + l2.h / 2)
    assert abs(abs(mid1[0] - mid1[1]) - 2 * lay.PAIR_LINE_OFFSET) <= 2
    boxes = {n.uid: n for n in cluster.nodes}
    axis = (boxes[1].cy + boxes[2].cy) / 2
    sides = sorted(round((ln.y + ln.h / 2) - axis) for ln in cluster.lines)
    assert sides[0] < 0 < sides[1], "两条线要分居轴线两侧（一个 +7 一个 −7）"
    assert abs(abs(sides[0]) - abs(sides[1])) <= 2
    # 两个 chip：互不重叠，各自**底边中点**落在自己那条线上（靠不同 t 错开）
    rects = [
        (ln.label_x, ln.label_y, ln.label_x + lay.CHIP_W, ln.label_y + lay.CHIP_H)
        for ln in cluster.lines
    ]
    assert not _overlaps(rects[0], rects[1]), "同对双线的 chip 互相压住了"
    for ln in cluster.lines:
        anchor = (ln.label_x + lay.CHIP_W / 2, ln.label_y + lay.CHIP_H)
        assert _perp_dist(anchor, ln) <= 2, "chip 底边中点必须落在自己那条线上"


def test_single_directed_edge_is_not_offset():
    """单边不受双线错开影响：偏移 0，线仍穿过两箱心连线的中点。"""
    lay = _l()
    for kind in ("crush", "like"):
        (cluster,) = lay.layout_graph(_dg((1, 2, kind)), focus=None)
        boxes = {n.uid: n for n in cluster.nodes}
        (line,) = cluster.lines
        mid = (line.x + line.w / 2, line.y + line.h / 2)
        axis = ((boxes[1].cx + boxes[2].cx) / 2, (boxes[1].cy + boxes[2].cy) / 2)
        assert abs(mid[0] - axis[0]) <= 1.5 and abs(mid[1] - axis[1]) <= 1.5


# --------------------------------------------------------------------------- #
# 用户 2026-10-14（三）：「乱七八糟的连线都对不齐」——
# 标签必须**贴在自己的线上**（旧的沿法线推 31px 是根因），且不许压任何节点盒。
# --------------------------------------------------------------------------- #
#: 覆盖：水平对 / 3-6 人扇形 / 链 / 双有向边 / 大簇（生产同形）
_LABEL_SHAPES = (
    ((1, 2, "m"),),
    ((1, 2, "m"), (1, 3, "m"), (1, 4, "m")),
    ((1, 2, "m"), (1, 3, "m"), (1, 4, "m"), (1, 5, "m"), (1, 6, "m")),
    ((1, 2, "m"), (2, 3, "m"), (4, 5, "m")),
    ((1, 2, "c"), (2, 1, "k")),
    ((1, 2, "c"), (2, 1, "k"), (5, 6, "k")),
    ((1, 2, "k"), (1, 3, "k"), (1, 4, "k"), (1, 5, "k"), (1, 6, "k"), (1, 7, "k")),
)


def _chip_center(line, lay):
    return (line.label_x + lay.CHIP_W / 2, line.label_y + lay.CHIP_H)


def _perp_dist(point, line):
    """点到线段（用 bbox + angle 还原两端）的垂距；落在线外时取到端点的距离。"""
    (a, b) = _ends(line)
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ll = dx * dx + dy * dy
    t = ((point[0] - ax) * dx + (point[1] - ay) * dy) / ll if ll else 0.0
    t = max(0.0, min(1.0, t))
    return math.hypot(point[0] - (ax + t * dx), point[1] - (ay + t * dy))


def test_label_chip_bottom_edge_lies_on_its_own_line_in_every_shape():
    """**chip 底边中点必须落在自己那条线上**（垂距 ≤ 2px）。

    实拍「连线都对不齐」的回归钉。

    用户 2026-10-14：「标签改为底线贴在线上，文本跟随线条旋转，文本底部贴靠在线条上」。
    旧实现把非水平线的 chip 沿**法线**推开 31px，标签看着不属于任何一条线。
    """
    lay = _l()
    for shape in _LABEL_SHAPES:
        for cluster in lay.layout_graph(_mk(*shape), focus=None):
            for line in cluster.lines:
                anchor = _chip_center(line, lay)
                d = _perp_dist(anchor, line)
                assert d <= 2, (
                    f"{shape} 的 {line.src}->{line.dst} 标签离线 {d:.1f}px"
                    "（chip 底边中点必须压在线上）"
                )


def _rot_chip_aabb(line, lay):
    """关系名 chip **旋转后**的 AABB（锚点 = 底边中点）。

    与 `layout._label_pos` 的碰撞口径一致。
    """
    rad = math.radians(lay.label_rotation(line.angle))
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    ax, ay = _chip_center(line, lay)
    xs: list[float] = []
    ys: list[float] = []
    for cx, cy in (
        (-lay.CHIP_W / 2, 0.0),
        (lay.CHIP_W / 2, 0.0),
        (lay.CHIP_W / 2, -lay.CHIP_H),
        (-lay.CHIP_W / 2, -lay.CHIP_H),
    ):
        xs.append(ax + cx * cos_r - cy * sin_r)
        ys.append(ay + cx * sin_r + cy * cos_r)
    return (min(xs), min(ys), max(xs), max(ys))


def test_label_chip_never_covers_a_node_box_in_every_shape():
    """**chip（旋转后）不许压任何节点盒**。

    比「不压名字带」更强的口径 —— 头像也不能压。
    """
    lay = _l()
    for shape in _LABEL_SHAPES:
        for cluster in lay.layout_graph(_mk(*shape), focus=None):
            for line in cluster.lines:
                chip = _rot_chip_aabb(line, lay)
                for box in cluster.nodes:
                    assert not _overlaps(
                        chip, (box.x, box.y, box.x + box.w, box.y + box.h)
                    ), (
                        f"{shape} 的 {line.src}->{line.dst}"
                        f" chip 压在 uid={box.uid} 的节点盒上"
                    )


def test_label_pos_slides_off_a_node_box_sitting_on_the_line():
    """线段正中摆一个节点盒 → chip 必须**沿线上滑开**，不许压在盒上，也不许离线。

    （这正是 `blocked` 里那串节点盒存在的意义：MN26「不避让节点盒」必须变红。）
    """
    lay = _l()
    line = lay.EdgeLine(
        kind="crush",
        src=1,
        dst=2,
        x=0,
        y=-2,
        w=400,
        h=4,
        angle=0.0,
        label_x=0,
        label_y=0,
    )
    on_line = lay.NodeBox(uid=9, x=170, y=-50, w=96, h=96)  # 正中，跨在线上下
    lx, ly = lay._label_pos(line, [on_line])
    chip = (lx, ly, lx + lay.CHIP_W, ly + lay.CHIP_H)
    box = (on_line.x, on_line.y, on_line.x + on_line.w, on_line.y + on_line.h)
    assert not _overlaps(chip, box), "chip 压在了线上的节点盒上"
    anchor = (lx + lay.CHIP_W / 2, ly + lay.CHIP_H)
    assert _perp_dist(anchor, line) <= 2, "滑开 ≠ 离线"
