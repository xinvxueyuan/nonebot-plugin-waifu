"""关系图的极坐标布局（**纯函数层**）：关系边 → 画布坐标。不渲染、不碰 IO。

布局 = 自我中心网络（ego network）的径向/星形排布：
焦点（或簇心）居中，一度关系按**等角**撒在半径 R 的圈上（从正上方起、顺时针）。
R 由环上人数决定（周长要放得下节点），并夹在 [MIN_RING_R, MAX_RING_R]。

所有数值都是**确定性**的（同一输入必得同一坐标）——否则测试与实拍对不上。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .relations import RelationEdge, RelationGraph

NODE_W: Final[int] = 96  # 节点盒宽（头像 64 + 名字行留白）
AVATAR: Final[int] = 64  # 头像直径
NODE_H: Final[int] = 96  # 节点盒高
CANVAS_W: Final[int] = 600  # 画布宽（卡片内容宽）
MIN_RING_R: Final[int] = 132
MAX_RING_R: Final[int] = 220
GAP: Final[int] = 22  # 线段端点与头像之间的空隙
PAD: Final[int] = 24


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


def _ring_radius(n: int) -> int:
    need = math.ceil(n * (NODE_W + 6) / (2 * math.pi)) if n else 0
    return max(MIN_RING_R, min(MAX_RING_R, need))


def _line(a: NodeBox, b: NodeBox, edge: RelationEdge, *, width: int) -> EdgeLine:
    """从 a 中心到 b 中心，两端各缩进 `AVATAR/2 + GAP`（线不压头像）。"""
    dx, dy = b.cx - a.cx, b.cy - a.cy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    cut = AVATAR / 2 + GAP
    x0, y0 = a.cx + ux * cut, a.cy + uy * cut
    x1, y1 = b.cx - ux * cut, b.cy - uy * cut
    left, top = min(x0, x1), min(y0, y1)
    w = max(1, round(abs(x1 - x0)))
    h = max(1, round(abs(y1 - y0)))
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    # 标签往线段法线方向偏一点，避免压线
    nx, ny = -uy, ux
    lx = round(mx + nx * 16 - 30)
    ly = round(my + ny * 16 - 13)
    lx = max(0, min(width - 60, lx))
    ly = max(0, ly)
    return EdgeLine(
        kind=edge.kind,
        src=edge.src,
        dst=edge.dst,
        x=round(left),
        y=round(top),
        w=w,
        h=h,
        angle=math.degrees(math.atan2(y1 - y0, x1 - x0)),
        label_x=lx,
        label_y=ly,
    )


def _cluster(
    hub: int, ring: list[int], edges: Iterable[RelationEdge], *, caption: str
) -> ClusterLayout:
    n = len(ring)
    radius = _ring_radius(n)
    height = 2 * (radius + NODE_H) + 2 * PAD
    cx, cy = CANVAS_W / 2, height / 2
    boxes = {hub: _box(cx, cy, hub)}
    for i, uid in enumerate(ring):
        ang = math.radians(-90.0 + i * 360.0 / n)
        boxes[uid] = _box(cx + radius * math.cos(ang), cy + radius * math.sin(ang), uid)
    lines = tuple(
        _line(
            boxes[e.src if e.src in boxes else e.dst],
            boxes[e.dst if e.dst in boxes else e.src],
            e,
            width=CANVAS_W,
        )
        for e in edges
        if e.src in boxes and e.dst in boxes
    )
    return ClusterLayout(
        hub=hub,
        caption=caption,
        nodes=tuple(boxes[u] for u in sorted(boxes)),
        lines=lines,
        width=CANVAS_W,
        height=int(height),
    )


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
