"""关系图布局纯函数：2 成员水平对 / ≥3 成员扇形、坐标确定、不越界、chip 不压名字。

（2026-10-14 二：有向边 dst 端加**真箭头**两翼 + 同对有向双线错开，见文件末尾。）
"""

from __future__ import annotations

import itertools
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


# --------------------------------------------------------------------------- #
# 用户实拍 2026-10-15：「重叠太多了」——28 人的簇全塞在一个半径 220 的单环上，
# 实测 51 对盒子互压（最坏 4836px²、最小间隙 −63px）。人多必须**分环**。
# --------------------------------------------------------------------------- #
def _star(n: int):
    """造 n 人星形（1 号为中心、其余都是它的「喜欢」）；n > 11 时必然要分环。"""
    from nonebot_plugin_waifu import relations

    likes = {1: 2}
    likes.update(dict.fromkeys(range(2, n + 1), 1))
    return relations.build_relations(marriages=[], crushes={}, likes=likes)


def _min_axis_gap(cluster):
    """簇内两两盒子的**最小轴间距**（`max(|dx|,|dy|) - NODE_W`；负值 = 重叠量）。

    45° 斜位上的两个盒子中心距 ~100px 也会相压，所以判据只能按轴算，不能按距离。
    """
    worst = math.inf
    for a, b in itertools.combinations(cluster.nodes, 2):
        dx = max(a.x - (b.x + b.w), b.x - (a.x + a.w))
        dy = max(a.y - (b.y + b.h), b.y - (a.y + a.h))
        worst = min(worst, max(dx, dy))
    return worst


def _radii(cluster):
    """环上节点到 hub 的距离（取整到偶数，消除取整毛刺）。"""
    hub = next(b for b in cluster.nodes if b.uid == cluster.hub)
    return {
        round(math.hypot(b.cx - hub.cx, b.cy - hub.cy) / 2) * 2
        for b in cluster.nodes
        if b.uid != cluster.hub
    }


def test_big_clusters_split_into_rings_and_never_overlap():
    """12~30 人的簇必须分环，且**两两盒子零重叠**（实拍「重叠太多了」回归钉）。"""
    lay = _l()
    for n in range(12, 31):
        (cluster,) = lay.layout_graph(_star(n), focus=None)
        gap = _min_axis_gap(cluster)
        assert gap > 0, f"{n} 人簇还有盒子相压（最小轴间距 {gap:.0f}px）"
        radii = _radii(cluster)
        assert len(radii) >= 2, f"{n} 人簇没分环（半径 {radii}）"


def test_small_clusters_keep_the_historical_single_ring():
    """≤11 人的簇**逐像素不变**（2026-10-15 的承诺）：仍然是半径 `RING_R` 的单环。"""
    lay = _l()
    for n in range(3, 12):
        (cluster,) = lay.layout_graph(_star(n), focus=None)
        assert _radii(cluster) == {lay.RING_R}, f"{n} 人簇不在单环上：{_radii(cluster)}"


def test_ring_plan_is_complete_deterministic_and_on_the_ladder():
    """`_ring_plan`：人数不丢不重、半径都在梯子上、环数有上限、跑两次一模一样。"""
    lay = _l()
    for n in range(2, 61):
        plan = lay._ring_plan(n)
        assert sum(c for _r, c in plan) == n, (
            f"{n} 人：分配合计 {sum(c for _r, c in plan)}"
        )
        assert 1 <= len(plan) <= lay.RING_MAX_RINGS
        assert all(c >= 1 for _r, c in plan)
        for i, (radius, _c) in enumerate(plan):
            assert radius == lay.RING_R + lay.RING_GAP * i, (
                f"{n} 人：半径 {radius} 不在梯子上"
            )
        assert lay._ring_plan(n) == plan, "同一人数必须给同一个分配（确定性）"


def test_ring_plan_uses_the_fewest_rings_that_fit():
    """能 2 环就不上 3 环（每多一环卡片就更宽）：24 人 2 环、25 人起 3 环。"""
    lay = _l()
    assert len(lay._ring_plan(12)) == 2
    assert len(lay._ring_plan(24)) == 2
    assert len(lay._ring_plan(25)) == 3


def test_bfs_order_puts_graph_neighbours_next_to_each_other():
    """分环后的摆放顺序 = 从 hub 出发的 BFS（相邻位置就是图里挨着的人）。"""
    from nonebot_plugin_waifu import relations

    lay = _l()
    es = tuple(
        relations.RelationEdge(a, b, "like")
        for a, b in ((1, 2), (2, 3), (3, 4), (4, 5), (1, 6))
    )
    g = relations.RelationGraph(edges=es, nodes=(1, 2, 3, 4, 5, 6), total_edges=len(es))
    # 1 的两条出边先按 uid 升序（2, 6），再顺着链往下（3, 4, 5）
    assert lay._bfs_order(1, [2, 3, 4, 5, 6], g.edges) == (2, 6, 3, 4, 5)


def test_big_cluster_labels_still_glue_to_their_own_lines():
    """人多分了环以后，chip 底边中点**仍然贴在自己那条线上**（≤2px）。"""
    lay = _l()
    for n in (14, 28):
        for cluster in lay.layout_graph(_star(n), focus=None):
            for line in cluster.lines:
                d = _perp_dist(_chip_center(line, lay), line)
                assert d <= 2, f"{n} 人簇的 {line.src}->{line.dst} 标签离线 {d:.1f}px"


# --------------------------------------------------------------------------- #
# v0.8.0「槽位重排」：`cluster_specs()` + `layout_graph(assignments=...)`
#
# 几何**一个不动**：槽位坐标与今天 `_cluster` 摆节点用的逐点相同，解只决定
# 「谁坐哪个坑」。所以这里钉三件事：① spec 的槽位就是今天的落点；
# ② `assignments=None` 与历史**逐像素一致**（既有断言一个都不许改）；
# ③ 给了 assignments 就真的按槽位摆（且坏值静默回退历史序）。
# --------------------------------------------------------------------------- #
def _slots():
    from nonebot_plugin_waifu import slots

    return slots


def _specs_and_assignments(graph, *, budget: float = 0.0):
    """按 `handle.py` 的接线取 `assignments`：逐簇 `signature_of` + `solve`。"""
    lay, slots = _l(), _slots()
    out = {}
    specs = lay.cluster_specs(graph)
    for spec in specs:
        out[slots.signature_of(spec.members, spec.edges)] = slots.solve(
            spec.slot_xy,
            members=spec.members,
            hub=spec.hub,
            edges=spec.edges,
            budget_s=budget,
            seed=20261017,
        )
    return specs, out


def _shapes():
    """覆盖：水平对 / 小扇形 / 分环大簇 / 多分量 / 同对双有向边 / 生产同形（38 人）。"""
    return (
        _mk((1, 2, "m")),
        _mk((1, 2, "m"), (3, 1, "c"), (4, 1, "c")),
        _mk((1, 2, "m"), (1, 3, "m"), (1, 4, "m"), (1, 5, "m"), (1, 6, "m")),
        _mk((1, 2, "m"), (2, 3, "m"), (4, 5, "m")),
        _dg((1, 2, "crush"), (2, 1, "like"), (5, 6, "like")),
        _star(12),
        _star(28),
        _star(38),  # 生产大簇同形（37 个环上节点 → 3 环）
    )


def test_cluster_specs_slots_are_exactly_the_positions_used_today():
    """`cluster_specs()` 的槽位坐标 = 今天 `_cluster` 实际用于摆节点的坐标。

    三份证据：
    ① 结构：长度 == 成员数、首位 = hub 的槽（环簇是 `(0, 0)` 的簇心）；
    ② 环簇的槽位半径与人数与 `_ring_plan` 逐个相同（环由内到外、环内按角序）；
    ③ **最强的一条**：拿「历史序」当解交回去（`budget_s=0`），摆出来的盒子与
       `layout_graph(g)`（不给解）**逐点相同** —— 坐标错一个像素都会红。
    """
    lay = _l()
    for g in _shapes():
        specs, assigns = _specs_and_assignments(g)
        assert specs, "空图不该出现在这里"
        for spec in specs:
            assert len(spec.slot_xy) == len(spec.members)
            assert spec.members == tuple(sorted(spec.members))
            assert spec.edges == tuple(sorted(set(spec.edges)))
            ring = [u for u in spec.members if u != spec.hub]
            if len(ring) > 1:
                assert spec.slot_xy[0] == (0.0, 0.0), "环簇的槽位 0 必须是簇心"
                plan = lay._ring_plan(len(ring))
                radii = [round(math.hypot(x, y)) for x, y in spec.slot_xy[1:]]
                want = [round(r) for r, c in plan for _ in range(c)]
                assert radii == want, f"槽位半径与 _ring_plan 不一致：{radii} != {want}"
            else:
                assert {round(x) for x, _y in spec.slot_xy} == {
                    -lay.PAIR_SPAN // 2,
                    lay.PAIR_SPAN // 2,
                }, "2 人簇的槽位必须是水平对的那两个位置"
        historical = lay.layout_graph(g)
        assert lay.layout_graph(g, assignments=assigns) == historical


def test_layout_graph_without_assignments_is_pixel_identical_to_yesterday():
    """缺省（`None` / 空表 / 无关键）与今天**逐像素一致** —— focus 视图也一样。

    `assignments=None` 是「开关关掉」与「任何一步失败」的兜底路径：它必须**完全**
    等于历史行为（既有断言一个都没改，靠的就是这条）。
    """
    lay = _l()
    for g in _shapes():
        base = lay.layout_graph(g)
        assert lay.layout_graph(g, assignments=None) == base
        assert lay.layout_graph(g, assignments={}) == base
        assert lay.layout_graph(g, assignments={"deadbeefdeadbeef": (1, 2, 3)}) == base
    g = _mk((1, 2, "m"), (3, 1, "c"), (4, 1, "c"))
    focus = lay.layout_graph(g.ego(1), focus=1)
    assert lay.layout_graph(g.ego(1), focus=1, assignments=None) == focus
    assert lay.layout_graph(g.ego(1), focus=1, assignments={}) == focus


def test_layout_graph_puts_each_uid_on_its_own_slot():
    """给了 assignments 就**按槽位摆**。

    每个 uid 落在自己那个槽上（相对 hub），槽位位置的多重集不变
    （= 只换座位、不动几何）。
    """
    lay, slots = _l(), _slots()
    g = _mk(
        (1, 2, "m"),
        (1, 3, "c"),
        (1, 4, "c"),
        (1, 5, "c"),
        (1, 6, "c"),
        (1, 7, "c"),
        (1, 8, "c"),
    )
    spec = lay.cluster_specs(g)[0]
    base = _slots().solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=0.0,
        seed=1,
    )
    rotated = (base[0], *base[2:], base[1])  # 环上轮转一位（hub 仍在簇心）
    (cluster,) = lay.layout_graph(
        g, assignments={slots.signature_of(spec.members, spec.edges): rotated}
    )
    by_uid = {n.uid: n for n in cluster.nodes}
    hub_c = (by_uid[spec.hub].cx, by_uid[spec.hub].cy)
    for k, uid in enumerate(rotated):
        dx = spec.slot_xy[k][0] - spec.slot_xy[0][0]
        dy = spec.slot_xy[k][1] - spec.slot_xy[0][1]
        assert abs((by_uid[uid].cx - hub_c[0]) - dx) <= 0.51, f"uid={uid} 不在槽 {k} 上"
        assert abs((by_uid[uid].cy - hub_c[1]) - dy) <= 0.51, f"uid={uid} 不在槽 {k} 上"
    # 位置集合不变：还是那些槽位
    was = {(n.cx, n.cy) for n in lay.layout_graph(g)[0].nodes}
    now = {(n.cx, n.cy) for n in cluster.nodes}
    assert was == now, "换座位不该挪动任何一个槽位"


def test_layout_graph_falls_back_when_the_assignment_is_unusable():
    """键缺失 / 长度不符 / 不是置换 → 该簇**静默回退历史序**（出图绝不因此失败）。"""
    lay, slots = _l(), _slots()
    g = _mk((1, 2, "m"), (1, 3, "c"), (1, 4, "c"), (1, 5, "c"))
    spec = lay.cluster_specs(g)[0]
    sig = slots.signature_of(spec.members, spec.edges)
    base = lay.layout_graph(g)
    good = slots.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=0.0,
        seed=1,
    )
    assert lay.layout_graph(g, assignments={sig: good}) == base  # 历史序 = 恒等
    assert lay.layout_graph(g, assignments={sig: good[:-1]}) == base  # 长度不符
    assert lay.layout_graph(g, assignments={sig: [1] * len(good)}) == base  # 不是置换
    assert lay.layout_graph(g, assignments={sig: [*good[:-1], 999999]}) == base


def test_two_member_cluster_stays_a_horizontal_pair_whatever_the_assignment():
    """2 人簇是**水平对**：只有「边的那一端在左」这一个解 —— 解给什么都照样摆。

    （`slot_xy` 的第一位仍是 hub 的槽，只是那儿没有第二个自由度；`_cluster` 的
    2 人分支压根不看 `order`，所以坏缓存也镜像不了这一簇。）
    """
    lay, slots = _l(), _slots()
    g = _mk((1, 2, "m"))
    spec = lay.cluster_specs(g)[0]
    assert len(spec.members) == 2 and len(spec.slot_xy) == 2
    sig = slots.signature_of(spec.members, spec.edges)
    base = lay.layout_graph(g)
    (cluster,) = base
    a, b = cluster.nodes
    assert a.y == b.y and abs(abs(a.cx - b.cx) - lay.PAIR_SPAN) <= 2
    for swapped in ((spec.members[1], spec.members[0]), (spec.hub, spec.members[0])):
        assert lay.layout_graph(g, assignments={sig: swapped}) == base


def test_cluster_specs_order_matches_layout_graph_clusters():
    """第 i 个 spec 就是第 i 个簇（hub 与成员集合一致）。

    `handle.py` 的接线（逐 spec 求解 → 按指纹交给 `layout_graph`）靠这条对齐。
    """
    lay = _l()
    for g in _shapes():
        clusters = lay.layout_graph(g)
        specs = lay.cluster_specs(g)
        assert len(specs) == len(clusters)
        for cluster, spec in zip(clusters, specs, strict=True):
            assert cluster.hub == spec.hub
            assert tuple(sorted(n.uid for n in cluster.nodes)) == spec.members


def test_focus_view_takes_assignments_too():
    """`@某人` 的单人视图（`focus=`）也吃 assignments。

    单人视图走的是**焦点簇**（hub = 被 @ 的人），不是分量规则；`cluster_specs`
    对 ego 图算出来的 hub 恰好同一个人（ego 里所有边都连着他，度数最大），
    所以指纹对得上、解能生效 —— 这条把「@ 单人也要走槽位重排」钉住。
    """
    lay, slots = _l(), _slots()
    g = _mk(
        (1, 2, "m"), (1, 3, "c"), (1, 4, "c"), (1, 5, "c"), (1, 6, "c"), (1, 7, "c")
    )
    ego = g.ego(1)
    (spec,) = lay.cluster_specs(ego)
    assert spec.hub == 1
    base = slots.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=0.0,
        seed=1,
    )
    rotated = (base[0], *base[2:], base[1])
    before = lay.layout_graph(ego, focus=1)
    after = lay.layout_graph(
        ego,
        focus=1,
        assignments={slots.signature_of(spec.members, spec.edges): rotated},
    )
    assert before != after, "焦点视图没吃下 assignments"
    by_uid = {n.uid: n for n in after[0].nodes}
    hub_c = (by_uid[spec.hub].cx, by_uid[spec.hub].cy)
    for k, uid in enumerate(rotated):
        assert abs((by_uid[uid].cx - hub_c[0]) - spec.slot_xy[k][0]) <= 0.51
        assert abs((by_uid[uid].cy - hub_c[1]) - spec.slot_xy[k][1]) <= 0.51
