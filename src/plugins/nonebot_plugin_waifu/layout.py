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

**有向边（倾心/喜欢）在 dst 端画箭头**（`_arrow_wings`，两翼各 11px、与主线夹角
±155°，实线）—— 方向不敢再靠标签里的「→」字符（用户 2026-10-14：
「倾心→」不够明确，要真箭头指向被倾心的人）。同一对人的两条有向边（A→B 与 B→A）
各自垂直错开 ±`PAIR_LINE_OFFSET`（`_pair_shifts`），否则两条线完全重叠、分不出谁指谁。

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
CHIP_GAP: Final[int] = 2  # 同对双线两个 chip 之间要留的最小缝（去重叠后）
NAME_BAND_H: Final[int] = 32  # 节点盒底部的「名字带」高度（名字行 24 + 余量）
FAN_MIN_SPREAD: Final[float] = 120.0  # 扇形张角下限（度）
FAN_SPREAD_STEP: Final[float] = 40.0  # 环上每多一人张角加多少度
FAN_MAX_SPREAD: Final[float] = 300.0  # 扇形张角上限（度）
DECOLLINEAR_DEG: Final[float] = 2.0  # 去共线兜底：两条边正好相差 180° 时错开多少
PAIR_LINE_OFFSET: Final[float] = 7.0  # 同对有向双线：各自垂直偏移 ±7px（单边为 0）
ARROW_WING_LEN: Final[float] = 11.0  # 箭头翼长（px）
ARROW_WING_DEG: Final[float] = 155.0  # 翼与主线（前进方向）的夹角（度）

_NO_SHIFT: Final[tuple[float, float]] = (0.0, 0.0)  # 不做双线错开时的垂直偏移


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
    #: dst 端箭头两翼（只对**有向边**：倾心/喜欢；夫妻无向、没有箭头）。
    #: 单独一列而不是并进 `lines` —— 它们没有关系名 chip、也不参与「几条关系」的计数。
    arrows: tuple[EdgeLine, ...] = ()


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


def _segment(
    kind: str, src: int, dst: int, x0: float, y0: float, x1: float, y1: float
) -> EdgeLine:
    """由**精确端点**造一段线。

    bbox 取整给渲染用，`angle` 用未取整的端点算（方向不失真）。
    """
    return EdgeLine(
        kind=kind,
        src=src,
        dst=dst,
        x=round(min(x0, x1)),
        y=round(min(y0, y1)),
        w=max(1, round(abs(x1 - x0))),
        h=max(1, round(abs(y1 - y0))),
        angle=math.degrees(math.atan2(y1 - y0, x1 - x0)),
        label_x=0,
        label_y=0,
    )


def _arrow_wings(
    edge: RelationEdge, tip_x: float, tip_y: float, ux: float, uy: float
) -> tuple[EdgeLine, EdgeLine]:
    """有向边的 dst 端箭头：尖端 = 主线 dst 端点，两翼各 `ARROW_WING_LEN` 长。

    翼与主线的夹角 = ±`ARROW_WING_DEG`：两翼从尖端向**后**回折
    （朝 src 方向偏 ±25°）——标准箭头两笔，方向由它承担
    （用户 2026-10-14：标签里的「→」字符不够明确）。翼只在这里定位，
    实线由 render 侧保证（`dashes=1`）—— 虚线主线的 dst 端也要看得清。

    `src/dst` 沿用主线的（翼只借这个字段取配色/粗细）；方向看 `angle`。
    """
    wings: list[EdgeLine] = []
    for sign in (1.0, -1.0):
        rad = math.radians(sign * ARROW_WING_DEG)
        wx = ux * math.cos(rad) - uy * math.sin(rad)
        wy = ux * math.sin(rad) + uy * math.cos(rad)
        wings.append(
            _segment(
                edge.kind,
                edge.src,
                edge.dst,
                tip_x,
                tip_y,
                tip_x + wx * ARROW_WING_LEN,
                tip_y + wy * ARROW_WING_LEN,
            )
        )
    return (wings[0], wings[1])


def _draw_edge(
    a: NodeBox,
    b: NodeBox,
    edge: RelationEdge,
    *,
    shift: tuple[float, float] = _NO_SHIFT,
) -> tuple[EdgeLine, tuple[EdgeLine, ...]]:
    """一条边的全部线段：主线段 + dst 端箭头两翼。

    主线段 = a 箱心到 b 箱心、两端各缩进 `AVATAR/2 + GAP`。

    - `shift`：同对有向双线的垂直错开（见 `_pair_shifts`）—— 主线和箭头一起挪。
    - 尖端 = 主线 dst 端点：离目标箱心 `AVATAR/2 + GAP`（46px）——比 96 盒边界（48px）
      再向节点让 2px、离头像 14px，**不插进头像**（`AVATAR/2 = 32px`）。
      再让 6px 会让主线端点戳出尖端 4px（或把箭头与线拉出缝），故不取。
    - `label_x/label_y` 先留 0 —— chip 的位置要看到**整簇**的节点盒才能定
      （见 `_label_pos` 与 `_labelled`）。
    """
    dx, dy = b.cx - a.cx, b.cy - a.cy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    cut = AVATAR / 2 + GAP
    sx, sy = shift
    x0, y0 = a.cx + ux * cut + sx, a.cy + uy * cut + sy
    x1, y1 = b.cx - ux * cut + sx, b.cy - uy * cut + sy
    line = _segment(edge.kind, edge.src, edge.dst, x0, y0, x1, y1)
    if not edge.directed:
        return line, ()
    return line, _arrow_wings(edge, x1, y1, ux, uy)


def _pair_shifts(
    edges: Sequence[RelationEdge], boxes: dict[int, NodeBox]
) -> dict[RelationEdge, tuple[float, float]]:
    """同一对人的两条有向边 → 各自的垂直偏移（±`PAIR_LINE_OFFSET`）；单边不在表里。

    ⚠️ 两条边方向相反（u 与 −u），**各自**取法线会把两条线又叠回一条 ——
    偏移方向统一用组内第一条边（按 `src,dst` 排序）的法线，一个 +7 一个 −7。
    """
    groups: dict[tuple[int, int], list[RelationEdge]] = {}
    for e in edges:
        if e.directed and e.src in boxes and e.dst in boxes:
            groups.setdefault(e.pair, []).append(e)
    out: dict[RelationEdge, tuple[float, float]] = {}
    for group in groups.values():
        if len(group) < 2:
            continue
        ordered = sorted(group, key=lambda e: (e.src, e.dst))
        head, tail = boxes[ordered[0].src], boxes[ordered[0].dst]
        dx, dy = tail.cx - head.cx, tail.cy - head.cy
        dist = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / dist, dx / dist
        n = len(ordered)
        for i, e in enumerate(ordered):
            step = PAIR_LINE_OFFSET * (2 * i - (n - 1)) / (n - 1)
            out[e] = (nx * step, ny * step)
    return out


def _name_band(box: NodeBox) -> tuple[float, float, float, float]:
    """节点盒底部的「名字带」矩形：名字画在头像**下方**，chip 不许压它。"""
    return (box.x, box.y + box.h - NAME_BAND_H, box.x + box.w, box.y + box.h)


def _overlaps(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _label_pos(
    line: EdgeLine,
    boxes: Sequence[NodeBox],
    chip: tuple[int, int] = (CHIP_W, CHIP_H),
    side: float = 1.0,
) -> tuple[int, int]:
    """关系名 chip 的左上角（**纯函数**，可单测）。

    - 线段接近水平（`|dy| < 0.35*|dx|`，含完全水平）→ chip 居中压在中点：
      水平对形态下名字在头像下方、两个头像之间是空的，压中点最省地方。
    - 否则沿边 **t=0.40** 处取基点、沿法线偏出 `chip_h/2 + 18px`；若压到任何
      节点的名字带就再多推 12px（最多 3 轮）——用户实拍：「文字都和图重叠了」。
    - `side`：法线方向（±1）。同对双线的 chip 要镜到**自己线的外侧**
      （见 `_labelled`），否则会压到另一条线 dst 端的箭头。
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
        nx, ny = -dy / dist * side, dx / dist * side  # 法线方向
        lx = bx + nx * (ch / 2 + 18) - cw / 2
        ly = by + ny * (ch / 2 + 18) - ch / 2
        for _ in range(3):
            if not any(
                _overlaps((lx, ly, lx + cw, ly + ch), _name_band(b)) for b in boxes
            ):
                break
            lx, ly = lx + nx * 12, ly + ny * 12
    return round(lx), round(ly)


def _labelled(
    line: EdgeLine, boxes: Sequence[NodeBox], *, side: float = 1.0
) -> EdgeLine:
    """给线段补上关系名 chip 的左上角（chip 不与任何节点的名字带相交）。

    `side` 只在**同对双线**时为 −1（把 chip 镜到线的外侧），单边保持默认。
    """
    lx, ly = _label_pos(line, boxes, side=side)
    return replace(line, label_x=lx, label_y=ly)


def _chip_rect(line: EdgeLine) -> tuple[float, float, float, float]:
    return (line.label_x, line.label_y, line.label_x + CHIP_W, line.label_y + CHIP_H)


def _shift_label(line: EdgeLine, dx: int, dy: int) -> EdgeLine:
    return replace(line, label_x=line.label_x + dx, label_y=line.label_y + dy)


def _deoverlap_pair_chips(lines: list[EdgeLine], idxs: Sequence[int]) -> None:
    """同对双线的两个 chip 会互相压住 → 沿**公共法线**推开到不重叠。

    推开后至少留 `CHIP_GAP` 的缝。两条线只隔 `2 * PAIR_LINE_OFFSET`（14px），
    而 chip 高 `CHIP_H`（26px）—— chip 只跟着线挪 7px 还不够，必须再错开。
    跨对的 chip 相隔很远，不在这里处理。
    """
    if len(idxs) < 2:
        return
    rad = math.radians(lines[idxs[0]].angle)
    nx, ny = -math.sin(rad), math.cos(rad)
    for _ in range(4):
        hit = False
        for i, ai in enumerate(idxs):
            for bi in idxs[i + 1 :]:
                ca, cb = _chip_rect(lines[ai]), _chip_rect(lines[bi])
                if not _overlaps(ca, cb):
                    continue
                hit = True
                ext = abs(nx) * CHIP_W + abs(ny) * CHIP_H  # chip 在 n 方向的投影长度
                ax, ay = (ca[0] + ca[2]) / 2, (ca[1] + ca[3]) / 2
                bx, by = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
                gap = abs((bx - ax) * nx + (by - ay) * ny)
                push = round((ext + CHIP_GAP - gap) / 2)
                if push <= 0:
                    continue
                lines[ai] = _shift_label(
                    lines[ai], round(-nx * push), round(-ny * push)
                )
                lines[bi] = _shift_label(lines[bi], round(nx * push), round(ny * push))
        if not hit:
            return


def _normalize(
    *,
    hub: int,
    caption: str,
    boxes: dict[int, NodeBox],
    lines: tuple[EdgeLine, ...],
    arrows: tuple[EdgeLine, ...] = (),
) -> ClusterLayout:
    """整体平移 + 定宽高：内容左上角落到 `(PAD, PAD)`，宽高 = 内容范围 + `2*PAD`。

    不写死 height 公式 —— 每个簇留多少空白由**内容**决定（实拍里 4 个簇各占
    504px 高，其中大半是空白）。chip 也算进范围，保证它不会被切掉。
    箭头两翼同样算进来（它们能探出主线 bbox 之外几个像素）。
    """
    xs = min(
        [b.x for b in boxes.values()]
        + [ln.x for ln in lines]
        + [ln.label_x for ln in lines]
        + [ar.x for ar in arrows]
    )
    ys = min(
        [b.y for b in boxes.values()]
        + [ln.y for ln in lines]
        + [ln.label_y for ln in lines]
        + [ar.y for ar in arrows]
    )
    xe = max(
        [b.x + b.w for b in boxes.values()]
        + [ln.x + ln.w for ln in lines]
        + [ln.label_x + CHIP_W for ln in lines]
        + [ar.x + ar.w for ar in arrows]
    )
    ye = max(
        [b.y + b.h for b in boxes.values()]
        + [ln.y + ln.h for ln in lines]
        + [ln.label_y + CHIP_H for ln in lines]
        + [ar.y + ar.h for ar in arrows]
    )
    dx, dy = PAD - xs, PAD - ys
    return ClusterLayout(
        hub=hub,
        caption=caption,
        nodes=tuple(_shift_box(boxes[u], dx, dy) for u in sorted(boxes)),
        lines=tuple(_shift_line(ln, dx, dy) for ln in lines),
        width=xe - xs + 2 * PAD,
        height=ye - ys + 2 * PAD,
        arrows=tuple(replace(ar, x=ar.x + dx, y=ar.y + dy) for ar in arrows),
    )


def _cluster(
    hub: int, ring: list[int], edges: Iterable[RelationEdge], *, caption: str
) -> ClusterLayout:
    es = tuple(edges)
    boxes: dict[int, NodeBox] = {}
    if len(ring) == 1:
        # 2 成员（单边）→ 水平对：同 y、中心水平相距 PAIR_SPAN。
        # src 端放左边 —— 有向边的**箭头**（dst 端）读起来与图一致。
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
    shifts = _pair_shifts(es, boxes)
    lines: list[EdgeLine] = []
    arrows: list[EdgeLine] = []
    by_pair: dict[tuple[int, int], list[int]] = {}  # pair → lines 下标（chip 成对处理）
    for e in es:
        if e.src not in boxes or e.dst not in boxes:
            continue
        line, heads = _draw_edge(
            boxes[e.src], boxes[e.dst], e, shift=shifts.get(e, _NO_SHIFT)
        )
        by_pair.setdefault(e.pair, []).append(len(lines))
        lines.append(line)
        arrows.extend(heads)
    for idxs in by_pair.values():
        # 同对多线：chip 镜到**自己线的外侧**（不镜就会压到另一条线 dst 端的箭头），
        # 之后再沿公共法线把两个 chip 推开（两线只隔 14px、chip 高 26px，会叠住）。
        for i in idxs:
            lines[i] = _labelled(lines[i], known, side=-1.0 if len(idxs) > 1 else 1.0)
        _deoverlap_pair_chips(lines, idxs)
    return _normalize(
        hub=hub,
        caption=caption,
        boxes=boxes,
        lines=tuple(lines),
        arrows=tuple(arrows),
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
