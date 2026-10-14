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

标签 chip 的位置由 `_label_pos` 定：**永远贴在自己的线上**（法向偏移 0，需要时只沿
**这条线**前后滑）—— 用户实拍（2026-10-14）：「乱七八糟的连线都对不齐」：
旧实现把非水平线的 chip 沿法线推开 ~31px，标签看着不属于任何一条线；同时 chip
不得压到节点盒（含名字带）或另一个 chip。

**有向边（倾心/喜欢）不画箭头，改画「端口圆」**（`_port_marks`）：**起点实心圆、
终点空心圆**，就放在线的两端（离节点盒心 `AVATAR/2 + GAP`）—— 用户 2026-10-14：
「不要箭头了，线条两边一个空心圆一个实心圆，作为节点的端口」。同一对人的两条有向边
（A→B 与 B→A）各自垂直错开 ±`PAIR_LINE_OFFSET`（`_pair_shifts`），否则两条线完全重叠、
分不出谁指谁。

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
#: 环半径（px）：**必须让「hub→环上节点」的边塞得下一个关系名 chip**（`CHIP_W`=60）。
#: 边两端各让 `AVATAR/2 + GAP`（46px）→ R = 46*2 + 128 = 220。实测：128px 的边上
#: chip 贴着自己的线且不碰两端头像盒；半径 150（边只有 58px）时 chip 比线还宽，
#: 只能压头像或漂到线外 —— 用户 2026-10-14 实拍「乱七八糟的连线都对不齐」。
RING_R: Final[int] = 220
GAP: Final[int] = 14  # 线段端点与头像之间的空隙（22 → 14：线要真的露出来）
PAD: Final[int] = 24  # 簇内容到簇边界的留白
PAIR_SPAN: Final[int] = 260  # 2 成员簇：两节点中心的水平距离
CHIP_W: Final[int] = 46  # 关系名 chip 宽（14px 两字 + 内边距；与 render 的 .elab 对齐）
CHIP_H: Final[int] = 24  # 关系名 chip 高（14px 文字 + 内边距）
NAME_BAND_H: Final[int] = 32  # 节点盒底部的「名字带」高度（名字行 24 + 余量）
#: chip 沿线滑动的搜索范围与步长（线段参数 t ∈ [lo, hi]，步长 2% 线段长）：
#: 候选点多，挤在一起的标签才能各自沿线让开（只有 9 个固定点时最后一枚会挤不出去）。
LABEL_SLIDE_RANGE: Final[tuple[float, float]] = (0.02, 0.98)
LABEL_SLIDE_STEP: Final[float] = 0.02
FAN_MIN_SPREAD: Final[float] = 120.0  # 扇形张角下限（度）
FAN_SPREAD_STEP: Final[float] = 40.0  # 环上每多一人张角加多少度
FAN_MAX_SPREAD: Final[float] = 300.0  # 扇形张角上限（度）
DECOLLINEAR_DEG: Final[float] = 2.0  # 去共线兜底：两条边正好相差 180° 时错开多少
PAIR_LINE_OFFSET: Final[float] = 7.0  # 同对有向双线：各自垂直偏移 ±7px（单边为 0）
#: 有向边的**端口圆**直径（px，与 render 的 `.port` 对齐）：起点实心、终点空心。
PORT_D: Final[int] = 12

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
class PortMark:
    """有向边**两端的端口圆**：起点实心（`filled=True`）、终点空心。

    用户 2026-10-14：「不要箭头了，线条两边一个空心圆一个实心圆，作为节点的端口」。
    `cx/cy` = 圆心；`src/dst` 沿用主线（渲染取配色、测试定位用）。
    """

    kind: str
    src: int
    dst: int
    cx: int
    cy: int
    filled: bool


@dataclass(frozen=True)
class ClusterLayout:
    hub: int
    caption: str
    nodes: tuple[NodeBox, ...]
    lines: tuple[EdgeLine, ...]
    width: int
    height: int
    #: 有向边两端的端口圆（倾心/喜欢各两个；夫妻无向、没有端口）。
    #: 单独一列而不是并进 `lines` —— 它们没有关系名 chip、也不参与「几条关系」的计数。
    ports: tuple[PortMark, ...] = ()


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


def _port_marks(
    edge: RelationEdge, src_x: float, src_y: float, dst_x: float, dst_y: float
) -> tuple[PortMark, PortMark]:
    """有向边两端的端口圆：**起点实心、终点空心**（用户 2026-10-14 口径）。

    圆心就落在主线的两个端点上（离节点盒心 `AVATAR/2 + GAP` = 46px，离头像 14px）
    —— 落在节点盒边界内的 2px 处，看着像插在节点上的「端口」。
    """
    return (
        PortMark(edge.kind, edge.src, edge.dst, round(src_x), round(src_y), True),
        PortMark(edge.kind, edge.src, edge.dst, round(dst_x), round(dst_y), False),
    )


def _draw_edge(
    a: NodeBox,
    b: NodeBox,
    edge: RelationEdge,
    *,
    shift: tuple[float, float] = _NO_SHIFT,
) -> tuple[EdgeLine, tuple[PortMark, ...]]:
    """一条边的全部图元：主线段 + 两端端口圆（有向边才有）。

    主线段 = a 箱心到 b 箱心、两端各缩进 `AVATAR/2 + GAP`。

    - `shift`：同对有向双线的垂直错开（见 `_pair_shifts`）—— 主线和端口一起挪。
    - 端点 = 离箱心 `AVATAR/2 + GAP`（46px）——比 96 盒边界（48px）再向节点让 2px、
      离头像 14px，**不插进头像**（`AVATAR/2 = 32px`）；端口圆的圆心就落在这里。
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
    return line, _port_marks(edge, x0, y0, x1, y1)


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


def _overlap_area(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """两个矩形的相交面积（0 = 不相交）；chip 落点打分用。"""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _rotated_chip_aabb(
    ax: float, ay: float, angle: float, w: int = CHIP_W, h: int = CHIP_H
) -> tuple[float, float, float, float]:
    """关系名 chip **旋转后**的 AABB：锚点 = 底边中点（盒在锚点**上方** `h`）。

    碰撞判定与 `taken` 都用这个形状 —— 用未旋转矩形去比会漏（斜线上的 chip
    旋转后 AABB 明显更大，实测会漏出几百 px² 的重叠）。
    """
    rot = math.radians(label_rotation(angle))
    cos_r, sin_r = math.cos(rot), math.sin(rot)
    xs: list[float] = []
    ys: list[float] = []
    for cor_x, cor_y in ((-w / 2, 0.0), (w / 2, 0.0), (w / 2, -h), (-w / 2, -h)):
        xs.append(ax + cor_x * cos_r - cor_y * sin_r)
        ys.append(ay + cor_x * sin_r + cor_y * cos_r)
    return (min(xs), min(ys), max(xs), max(ys))


def _label_ts(t_hint: float) -> tuple[float, ...]:
    """关系名 chip 沿线滑动的候选 `t`：先给 `t_hint`，再按「离它多远」细扫整条线。

    候选点多（≈45 个）才挤得下 —— 9 个固定点时，密集簇里最后一枚标签会找不到
    空位、退回「压一点点」的兜底解。
    """
    lo, hi = LABEL_SLIDE_RANGE
    span = round((hi - lo) / LABEL_SLIDE_STEP)
    grid = [lo + i * LABEL_SLIDE_STEP for i in range(span + 1)]
    grid.append(min(max(t_hint, lo), hi))
    return tuple(sorted(grid, key=lambda t: (abs(t - t_hint), t)))


def label_rotation(angle: float) -> float:
    """标签的旋转角：跟着线条走，但**永远正着读**（不上下颠倒）。

    角度是 src→dst 的方向；超出 ±90° 时翻 180°（关系名两字左右对称，翻转无副作用）。
    layout 用它算 chip 的旋转后 AABB（碰撞判定必须与渲染出来的形状一致），
    render 用它写 `transform:rotate()` —— 两处**必须同源**。
    """
    a = (angle + 180.0) % 360.0 - 180.0  # → (-180, 180]
    if a > 90.0:
        return a - 180.0
    if a <= -90.0:
        return a + 180.0
    return a


def _label_pos(
    line: EdgeLine,
    boxes: Sequence[NodeBox],
    chip: tuple[int, int] = (CHIP_W, CHIP_H),
    *,
    t_hint: float = 0.5,
    taken: Sequence[tuple[float, float, float, float]] = (),
) -> tuple[int, int]:
    """关系名 chip 的左上角（**纯函数**，可单测）。

    **chip 的底边贴在自己这条线上、并跟着线旋转**（用户 2026-10-14：「标签改为底线
    贴在线上，文本跟随线条旋转，文本底部贴靠在线条上」）：
    锚点 = 线段上的一点，chip 的**底边中点**放在锚点上，再整体按 `label_rotation`
    转过去（渲染的 `transform-origin` 就是底边中点）。锚点默认取线段中点，与
    节点盒（含名字带）或已放好的 chip 冲突时，按 `LABEL_SLIDE_TS` **沿线前后滑**。

    Args:
        line: 这条边的主线段（用它的 bbox + angle 还原真方向）。
        boxes: 本簇所有节点盒（chip 不许压上去）。
        chip: chip 尺寸 `(宽, 高)`，默认 `(CHIP_W, CHIP_H)`。
        t_hint: 第一个候选位置（同对双线用 0.30：两条线方向相反，同一个 t 会
            自然落到**两端**，不重叠）。
        taken: 本簇已放置的 chip 矩形（旋转后的 AABB，互不重叠）。
    """
    cw, ch = chip
    rad = math.radians(line.angle)
    # ⚠️ bbox 丢了方向的正负号（w/h 都是正的），真方向要从 angle 还原。
    dx = math.copysign(line.w, math.cos(rad))
    dy = math.copysign(line.h, math.sin(rad))
    mx, my = line.x + line.w / 2, line.y + line.h / 2  # bbox 中心 = 线段中点
    blocked = [(b.x, b.y, b.x + b.w, b.y + b.h) for b in boxes]
    blocked += list(taken)

    order = _label_ts(t_hint)
    best: tuple[float, tuple[int, int]] | None = None
    for t in order:
        ax, ay = mx + (t - 0.5) * dx, my + (t - 0.5) * dy
        lx, ly = ax - cw / 2, ay - ch
        box = _rotated_chip_aabb(ax, ay, line.angle, cw, ch)
        bad = sum(_overlap_area(box, other) for other in blocked)
        if bad == 0:
            return round(lx), round(ly)
        if best is None or bad < best[0]:
            best = (bad, (round(lx), round(ly)))
    # 全撞：交给打分最小的那个（宁可压一点点，也不离线）
    assert best is not None
    return best[1]


def _labelled(
    line: EdgeLine,
    boxes: Sequence[NodeBox],
    *,
    t_hint: float = 0.5,
    taken: Sequence[tuple[float, float, float, float]] = (),
) -> EdgeLine:
    """给线段补上关系名 chip 的左上角（**贴着这条线**，规则见 `_label_pos`）。"""
    lx, ly = _label_pos(line, boxes, t_hint=t_hint, taken=taken)
    return replace(line, label_x=lx, label_y=ly)


def _chip_rect(line: EdgeLine) -> tuple[float, float, float, float]:
    """chip（**旋转后**）的 AABB —— 与 `_label_pos` 的碰撞口径同源。"""
    return _rotated_chip_aabb(
        line.label_x + CHIP_W / 2, line.label_y + CHIP_H, line.angle
    )


def _normalize(
    *,
    hub: int,
    caption: str,
    boxes: dict[int, NodeBox],
    lines: tuple[EdgeLine, ...],
    ports: tuple[PortMark, ...] = (),
) -> ClusterLayout:
    """整体平移 + 定宽高：内容左上角落到 `(PAD, PAD)`，宽高 = 内容范围 + `2*PAD`。

    不写死 height 公式 —— 每个簇留多少空白由**内容**决定（实拍里 4 个簇各占
    504px 高，其中大半是空白）。chip 也算进范围，保证它不会被切掉。
    端口圆同样算进来（它们能探出主线 bbox 之外几个像素）。
    """
    xs = min(
        [b.x for b in boxes.values()]
        + [ln.x for ln in lines]
        + [ln.label_x for ln in lines]
        + [p.cx - PORT_D // 2 for p in ports]
    )
    ys = min(
        [b.y for b in boxes.values()]
        + [ln.y for ln in lines]
        + [ln.label_y for ln in lines]
        + [p.cy - PORT_D // 2 for p in ports]
    )
    xe = max(
        [b.x + b.w for b in boxes.values()]
        + [ln.x + ln.w for ln in lines]
        + [ln.label_x + CHIP_W for ln in lines]
        + [p.cx + PORT_D // 2 for p in ports]
    )
    ye = max(
        [b.y + b.h for b in boxes.values()]
        + [ln.y + ln.h for ln in lines]
        + [ln.label_y + CHIP_H for ln in lines]
        + [p.cy + PORT_D // 2 for p in ports]
    )
    dx, dy = PAD - xs, PAD - ys
    return ClusterLayout(
        hub=hub,
        caption=caption,
        nodes=tuple(_shift_box(boxes[u], dx, dy) for u in sorted(boxes)),
        lines=tuple(_shift_line(ln, dx, dy) for ln in lines),
        width=xe - xs + 2 * PAD,
        height=ye - ys + 2 * PAD,
        ports=tuple(replace(p, cx=p.cx + dx, cy=p.cy + dy) for p in ports),
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
        boxes[hub] = _box(0.0, 0.0, hub)
        for uid, theta in zip(ring, _fan_angles(len(ring)), strict=True):
            ang = math.radians(theta)
            boxes[uid] = _box(RING_R * math.cos(ang), RING_R * math.sin(ang), uid)
    known = tuple(boxes.values())
    shifts = _pair_shifts(es, boxes)
    lines: list[EdgeLine] = []
    ports: list[PortMark] = []
    by_pair: dict[tuple[int, int], list[int]] = {}  # pair → lines 下标（chip 成对处理）
    for e in es:
        if e.src not in boxes or e.dst not in boxes:
            continue
        line, marks = _draw_edge(
            boxes[e.src], boxes[e.dst], e, shift=shifts.get(e, _NO_SHIFT)
        )
        by_pair.setdefault(e.pair, []).append(len(lines))
        lines.append(line)
        ports.extend(marks)
    placed: list[tuple[float, float, float, float]] = []

    def _span(ii: list[int]) -> float:
        return min(math.hypot(lines[i].w, lines[i].h) for i in ii)

    for idxs in sorted(by_pair.values(), key=_span):
        # 同对多线：两个 chip 各自贴着自己的线，靠**沿线错开**分开 ——
        # 两条线方向相反，同一个 t_hint 会自然落到线段两端；把**本簇已放好的 chip**
        # （含别对的）一起传进去，撞上就继续沿线滑（不再沿法线推：那正是「标签离线」）。
        # 顺序：**短线上先放**（可用位置最少），长线上的后放、有的是地方让开。
        for i in idxs:
            lines[i] = _labelled(
                lines[i],
                known,
                t_hint=0.30 if len(idxs) > 1 else 0.5,
                taken=tuple(placed),
            )
            placed.append(_chip_rect(lines[i]))
    return _normalize(
        hub=hub,
        caption=caption,
        boxes=boxes,
        lines=tuple(lines),
        ports=tuple(ports),
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
