"""关系图的坐标布局（**纯函数层**）：关系边 → 画布坐标。不渲染、不碰 IO。

形态按**成员数**分两种（确定性；坐标最后由 `_normalize` 统一归一化）：

- **2 成员（单边）→ 水平对**：两节点同 y、中心水平相距 `PAIR_SPAN`。
  旧实现用 `θ_i = -90° + i*360°/n` 撒环：n=1 的节点正好落在 hub **正上方** ——
  于是 2 人簇是「上下两点一条竖线」、3 人链是一条竖线，看起来像列表而不是连线
  （用户实拍反馈：「请不要让线在一条直线上，我要的是连线」）。
- **≥3 成员 → 扇形（fan）**：hub 落在簇的下方中间，环上节点按张角 `spread`
  散在上方（人越多张角越宽、上限 `FAN_MAX_SPREAD`）；**任意两条边不共线**
  （含 `_fan_angles` 里的去共线兜底）。绝不出现「穿过 hub 的一条直线」。

宽高不再写死公式：先按形态摆进局部坐标系，再由 `_normalize` 整体平移到
`min == PAD`、并按内容范围定宽高 —— 任何形态都不会留大片空白（实拍里 4 个簇
各占 504px 高，其中大半是空白）。

标签 chip 的位置由 `_label_pos` 定：**不与任何节点的名字带相交**（用户实拍：
「文字都和图重叠了」）。

所有数值都是**确定性**的（同一输入必得同一坐标）—— 否则测试与实拍对不上。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .relations import RelationEdge, RelationGraph

NODE_W: Final[int] = 96  # 节点盒宽（头像 64 + 名字行留白）
AVATAR: Final[int] = 64  # 头像直径
NODE_H: Final[int] = 96  # 节点盒高
CANVAS_W: Final[int] = 600  # 历史画布宽（坐标已改成按内容归一化，仅作参考常量保留）
MIN_RING_R: Final[int] = 150  # 环半径下限（线太短会看不见）
MAX_RING_R: Final[int] = 220  # 环半径上限
RING_BASE: Final[int] = 96  # 环半径 = RING_BASE + RING_PER_NODE * 环上人数
RING_PER_NODE: Final[int] = 26
GAP: Final[int] = 14  # 线段端点与头像之间的空隙（22 → 14：线要真的露出来）
PAD: Final[int] = 24  # 簇内容到簇边界的留白
PAIR_SPAN: Final[int] = 260  # 2 成员簇：两节点中心的水平距离
CHIP_W: Final[int] = 60  # 关系名 chip 宽（与 render 的 .elab 对齐）
CHIP_H: Final[int] = 26  # 关系名 chip 高
NAME_BAND_H: Final[int] = 32  # 节点盒底部的「名字带」高度（名字行 24 + 余量）
FAN_MIN_SPREAD: Final[float] = 120.0  # 扇形张角下限（度）
FAN_SPREAD_STEP: Final[float] = 40.0  # 环上每多一人张角加多少度
FAN_MAX_SPREAD: Final[float] = 300.0  # 扇形张角上限（度）
DECOLLINEAR_DEG: Final[float] = 2.0  # 去共线兜底：两条边正好相差 180° 时错开多少


@dataclass(frozen=True)
class NodeBox:
    uid: int
    x: int
    y: int
    w: int
    h: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass(frozen=True)
class EdgeLine:
    kind: str
    src: int
    dst: int
    x: int
    y: int
    w: int
    h: int
    angle: float  # CSS 渐变角度（0=向上、顺时针；等于线段方向）
    label_x: int  # 关系名小标签左上角
    label_y: int


@dataclass(frozen=True)
class ClusterLayout:
    hub: int
    caption: str
    nodes: tuple[NodeBox, ...]
    lines: tuple[EdgeLine, ...]
    width: int
    height: int


def _box(cx: float, cy: float, uid: int) -> NodeBox:
    return NodeBox(
        uid=uid,
        x=round(cx - NODE_W / 2),
        y=round(cy - NODE_H / 2),
        w=NODE_W,
        h=NODE_H,
    )


def _shift_box(box: NodeBox, dx: int, dy: int) -> NodeBox:
    return replace(box, x=box.x + dx, y=box.y + dy)


def _shift_line(line: EdgeLine, dx: int, dy: int) -> EdgeLine:
    return replace(
        line,
        x=line.x + dx,
        y=line.y + dy,
        label_x=line.label_x + dx,
        label_y=line.label_y + dy,
    )


def _ring_radius(n: int) -> int:
    """环半径：人越多圈越大（夹在 `[MIN_RING_R, MAX_RING_R]`）——线太短会看不见。"""
    return max(MIN_RING_R, min(MAX_RING_R, RING_BASE + RING_PER_NODE * n))


def _angle_between(a: float, b: float) -> float:
    """两个方向的夹角（0..180°，度）：共线 ⇔ 夹角为 0 或 180。"""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _fan_angles(n: int) -> tuple[float, ...]:
    """扇形张角序列（度；-90° = 正上方）。

    `θ_i = -90 - spread/2 + i*spread/(n-1)`，`spread = min(300, 120 + 40*(n-2))`：
    hub 在下、环上节点在上方散开（**不是**整圆 —— 整圆会把节点摆到 hub 正上方）。

    ⚠️ 去共线兜底：n=5 时步长正好 60°，第 0 与第 3 条边**相差 180°** ——
    两条边穿过 hub 连成一条直线，正是用户实拍里抱怨的观感。撞上就加
    `DECOLLINEAR_DEG` 错开（确定性、有界）。
    """
    if n <= 1:
        return (-90.0,)
    spread = min(FAN_MAX_SPREAD, FAN_MIN_SPREAD + FAN_SPREAD_STEP * (n - 2))
    step = spread / (n - 1)
    angles = [-90.0 - spread / 2 + i * step for i in range(n)]
    for i in range(1, n):
        for _ in range(4):
            if not any(
                math.isclose(_angle_between(angles[i], angles[j]), 180.0, abs_tol=1e-6)
                for j in range(i)
            ):
                break
            angles[i] += DECOLLINEAR_DEG
    return tuple(angles)


def _line(a: NodeBox, b: NodeBox, edge: RelationEdge) -> EdgeLine:
    """从 a 中心到 b 中心，两端各缩进 `AVATAR/2 + GAP`（线不压头像）。

    `label_x/label_y` 先留 0 —— chip 的位置要看到**整簇**的节点盒才能定
    （见 `_label_pos` 与 `_labelled`）。
    """
    dx, dy = b.cx - a.cx, b.cy - a.cy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    cut = AVATAR / 2 + GAP
    x0, y0 = a.cx + ux * cut, a.cy + uy * cut
    x1, y1 = b.cx - ux * cut, b.cy - uy * cut
    return EdgeLine(
        kind=edge.kind,
        src=edge.src,
        dst=edge.dst,
        x=round(min(x0, x1)),
        y=round(min(y0, y1)),
        w=max(1, round(abs(x1 - x0))),
        h=max(1, round(abs(y1 - y0))),
        angle=math.degrees(math.atan2(y1 - y0, x1 - x0)),
        label_x=0,
        label_y=0,
    )


def _name_band(box: NodeBox) -> tuple[float, float, float, float]:
    """节点盒底部的「名字带」矩形：名字画在头像**下方**，chip 不许压它。"""
    return (box.x, box.y + box.h - NAME_BAND_H, box.x + box.w, box.y + box.h)


def _overlaps(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _label_pos(
    line: EdgeLine, boxes: Sequence[NodeBox], chip: tuple[int, int] = (CHIP_W, CHIP_H)
) -> tuple[int, int]:
    """关系名 chip 的左上角（**纯函数**，可单测）。

    - 线段接近水平（`|dy| < 0.35*|dx|`，含完全水平）→ chip 居中压在中点：
      水平对形态下名字在头像下方、两个头像之间是空的，压中点最省地方。
    - 否则沿边 **t=0.40** 处取基点、沿法线偏出 `chip_h/2 + 18px`；若压到任何
      节点的名字带就再多推 12px（最多 3 轮）——用户实拍：「文字都和图重叠了」。
    """
    cw, ch = chip
    rad = math.radians(line.angle)
    # ⚠️ bbox 丢了方向的正负号（w/h 都是正的），真方向要从 angle 还原 ——
    # 否则 chip 会被推到线段的另一侧（法线符号反了）。
    dx = math.copysign(line.w, math.cos(rad))
    dy = math.copysign(line.h, math.sin(rad))
    mx, my = line.x + line.w / 2, line.y + line.h / 2  # bbox 中心 = 线段中点
    if abs(dy) < 0.35 * abs(dx):
        lx, ly = mx - cw / 2, my - ch / 2
    else:
        bx, by = mx - 0.10 * dx, my - 0.10 * dy  # t=0.40（相对 src 端）
        dist = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / dist, dx / dist  # 法线方向
        lx = bx + nx * (ch / 2 + 18) - cw / 2
        ly = by + ny * (ch / 2 + 18) - ch / 2
        for _ in range(3):
            if not any(
                _overlaps((lx, ly, lx + cw, ly + ch), _name_band(b)) for b in boxes
            ):
                break
            lx, ly = lx + nx * 12, ly + ny * 12
    return round(lx), round(ly)


def _labelled(line: EdgeLine, boxes: Sequence[NodeBox]) -> EdgeLine:
    """给线段补上关系名 chip 的左上角（chip 不与任何节点的名字带相交）。"""
    lx, ly = _label_pos(line, boxes)
    return replace(line, label_x=lx, label_y=ly)


def _normalize(
    *, hub: int, caption: str, boxes: dict[int, NodeBox], lines: tuple[EdgeLine, ...]
) -> ClusterLayout:
    """整体平移 + 定宽高：内容左上角落到 `(PAD, PAD)`，宽高 = 内容范围 + `2*PAD`。

    不写死 height 公式 —— 每个簇留多少空白由**内容**决定（实拍里 4 个簇各占
    504px 高，其中大半是空白）。chip 也算进范围，保证它不会被切掉。
    """
    xs = min(
        [b.x for b in boxes.values()]
        + [ln.x for ln in lines]
        + [ln.label_x for ln in lines]
    )
    ys = min(
        [b.y for b in boxes.values()]
        + [ln.y for ln in lines]
        + [ln.label_y for ln in lines]
    )
    xe = max(
        [b.x + b.w for b in boxes.values()]
        + [ln.x + ln.w for ln in lines]
        + [ln.label_x + CHIP_W for ln in lines]
    )
    ye = max(
        [b.y + b.h for b in boxes.values()]
        + [ln.y + ln.h for ln in lines]
        + [ln.label_y + CHIP_H for ln in lines]
    )
    dx, dy = PAD - xs, PAD - ys
    return ClusterLayout(
        hub=hub,
        caption=caption,
        nodes=tuple(_shift_box(boxes[u], dx, dy) for u in sorted(boxes)),
        lines=tuple(_shift_line(ln, dx, dy) for ln in lines),
        width=xe - xs + 2 * PAD,
        height=ye - ys + 2 * PAD,
    )


def _cluster(
    hub: int, ring: list[int], edges: Iterable[RelationEdge], *, caption: str
) -> ClusterLayout:
    es = tuple(edges)
    boxes: dict[int, NodeBox] = {}
    if len(ring) == 1:
        # 2 成员（单边）→ 水平对：同 y、中心水平相距 PAIR_SPAN。
        # src 端放左边 —— 「倾心→」的箭头读起来与图一致。
        left, right = hub, ring[0]
        for e in es:
            if {e.src, e.dst} == {hub, ring[0]}:
                left, right = e.src, e.dst
                break
        boxes[left] = _box(-PAIR_SPAN / 2, 0.0, left)
        boxes[right] = _box(PAIR_SPAN / 2, 0.0, right)
    else:
        # ≥3 成员 → 扇形：hub 在下、环上节点按张角散在上方（任意两条边不共线）
        radius = _ring_radius(len(ring))
        boxes[hub] = _box(0.0, 0.0, hub)
        for uid, theta in zip(ring, _fan_angles(len(ring)), strict=True):
            ang = math.radians(theta)
            boxes[uid] = _box(radius * math.cos(ang), radius * math.sin(ang), uid)
    known = tuple(boxes.values())
    lines = tuple(
        _labelled(_line(boxes[e.src], boxes[e.dst], e), known)
        for e in es
        if e.src in boxes and e.dst in boxes
    )
    return _normalize(hub=hub, caption=caption, boxes=boxes, lines=lines)


def layout_graph(
    graph: RelationGraph, *, focus: int | None = None
) -> tuple[ClusterLayout, ...]:
    """`focus` 给了就是单簇 ego 视图；没给就按**连通分量**出多簇（每簇一个中心）。"""
    if focus is not None:
        if focus not in graph.nodes:
            return ()
        ring = list(graph.neighbours(focus))
        return (_cluster(focus, ring, graph.edges, caption=""),)

    parent: dict[int, int] = {}

    def find(v: int) -> int:
        parent.setdefault(v, v)
        while parent[v] != v:
            parent[v] = parent[parent[v]]
            v = parent[v]
        return v

    for e in graph.edges:  # 连通分量
        ra, rb = find(e.src), find(e.dst)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    comps: dict[int, list[RelationEdge]] = {}
    for e in graph.edges:
        comps.setdefault(find(e.src), []).append(e)
    out: list[ClusterLayout] = []
    ordered = sorted(comps.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for idx, (_root, es) in enumerate(ordered, start=1):
        members = sorted({m for e in es for m in (e.src, e.dst)})
        deg = {u: sum(1 for e in es if u in (e.src, e.dst)) for u in members}
        hub = max(members, key=lambda u: (deg[u], -u))
        ring = [u for u in members if u != hub]
        out.append(_cluster(hub, ring, es, caption=f"#{idx}"))
    return tuple(out)


def node_ids(clusters: Iterable[ClusterLayout]) -> tuple[int, ...]:
    """全部簇里出现过的节点（保持出现顺序、去重）—— 取头像用。"""
    seen: dict[int, None] = {}
    for cluster in clusters:
        for node in cluster.nodes:
            seen.setdefault(node.uid, None)
    return tuple(seen)
