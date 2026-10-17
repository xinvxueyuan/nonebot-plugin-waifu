"""关系图落点求解器（**纯计算层**，v0.8.0）：几何一个不动，只搜「谁坐哪个坑」。

背景（用户 2026-10-17）：A–B–C 近似共线时，A–C 的连线必然贴着/压着 B ——
这是「节点摆在哪儿」造成的缺陷，靠画线修不了。本模块**不改任何槽位几何**
（环半径、环上角度、单环上限仍由 `layout._ring_plan` + `layout._fan_angles` 决定，
槽位坐标由 `layout.cluster_specs()` 给出），只解「成员 ↔ 槽位」的双射。

算法移植自 L1 原型（`SA5-L1`），三件法宝都在：

1. **预计算增量表**：任意两个位置 → 边的 profile（长度/压盒/墨长/压头像圆/标签压盒）；
   任意两个位置对 → 是否真交叉。搜索中只查表，不再算几何。
2. **多重启**（`RESTARTS` 次）：第 1 个从**历史序**出发，其余随机初始化。
3. **基线序入候选池防倒退**：历史序永远在池子里，只有**严格更优**的解会被采纳
   → 任何预算（哪怕 0）下结果都**绝不劣于历史序**。
4. 模拟退火：先用「全接受随机游走」标定温度（|Δ| 中位数），再几何降温；硬约束
   节点盒轴间距 ≥ `gap_min` —— 槽位几何整体做不到时（分配无关的常数）**不弃搜**，
   只把它降级成成本里的重罚项（`_SlotSearch.gap_def`），其余成本项照常优化。

**确定性**：同参数同种子 → 同一串迭代 → 同一个解 —— `budget_s` 先按 `EVALS_PER_SEC`
换算成**确定的步数**（机器快慢只影响墙钟），墙钟只做**安全上限**。所以「同参同出」
在**同一台机器、同一负载、同一个 Python 版本**下可复现（本机空载实测成立），
但**不是跨机器/跨负载的硬保证**：机器慢或负载高时尾部重启会被墙钟截断
（实测有负载时 8s 预算只跑完 ~64% 的步数）；截断只会**少跑几步**、退回更早的候选
（最坏 = 退回基线序），**绝不劣化**。

无 IO、无新依赖、不读配置：调用方（`handle.py`）负责把它丢进 `asyncio.to_thread`。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import random
import time
from typing import TYPE_CHECKING, Final, NamedTuple

from . import graph_g6, layout

if TYPE_CHECKING:
    from collections.abc import Sequence

# --------------------------------------------------------------------------- #
# 口径常量：全部与 layout / 指标同源，不许各写一套
# --------------------------------------------------------------------------- #
#: 头像圆半径（px）：「压头像圆」的判定半径（= `layout.AVATAR / 2`）。
AVATAR_R: Final[float] = layout.AVATAR / 2.0
#: 关系名胶囊能触及的最大距离 → chip 预筛半径（hypot(23, 24) ≈ 33.2px）。
CHIP_REACH: Final[float] = 34.0
#: 硬约束：节点盒两两**最小轴间距**（px）——低于它就算「密团」。
GAP_MIN: Final[float] = 16.0
#: 「短边」门槛（px）：短于它连一个关系名胶囊都塞不下。
SHORT_AT: Final[float] = 128.0
#: 「长边」门槛（px）：长于它是跨半张图的长弦（观感上「线拉飞了」）。
LONG_AT: Final[float] = 420.0
#: 均边（px）目标：小簇几何上限低时降到 `0.8 × 上限`（见 `_SlotSearch.mean_target`）。
MEAN_TARGET: Final[float] = 250.0
#: 允许的「<128px」边数（超过才开始罚）：与 ROUND2 硬指标同值。
SHORT_OK: Final[int] = 15
#: 重启次数（多重启）：预算按它均分；第 1 个从历史序出发。
RESTARTS: Final[int] = 8
#: 每次「随机交换 + 增量评估」的估计速度（次/秒）：把**秒预算**换算成**确定步数**，
#: 于是同参同出（墙钟只作安全上限）。实测生产大簇：本机空载 ≈ 10~11.4k 次/秒、
#: 同机有负载（跑变异门禁）≈ 5.7k 次/秒 —— 有负载时 8s 预算只跑完 ~64% 的步数
#: （墙钟截断；截断只少跑几步、最坏退回基线序，**绝不劣化**）。
#: 为什么不降到 6~7k（2026-10-17 审查第 3 条后的复测，生产全图 × 6 颗种子）：
#: 8s 下 9k/7k/6k 都是 6/6 种子「压头像圆 0」；但 2s 下 9k 只有 1 颗种子留压头像、
#: 7k/6k 各 3 颗；8s 下交叉/墨长 9k 也略优（694/8010 vs 764/8265 vs 765/9589）
#: —— 降常数是**可测劣化**，所以维持 9k，只把「留 ~25% 余量」这句过头的话去掉。
EVALS_PER_SEC: Final[float] = 9000.0

# 成本权重（L1 原型逐项照抄；「压头像圆」第一优先）
W_CIRCLE_FLAG: Final[float] = 6000.0  # 每条「边贴到别人头像圆」的边
W_CIRCLE_PEN: Final[float] = 60.0  # 每 px 最深侵入（≤32px → ≤1920）
W_HIT_FLAG: Final[float] = 900.0  # 每条「边压别人节点盒」的边
W_INK: Final[float] = 2.0  # 每 px 盒内墨长
W_CHIP_FLAG: Final[float] = 400.0  # 每条「关系名胶囊压节点盒」的边
W_CHIP_AREA: Final[float] = 0.05  # 每 px² 胶囊-盒重叠
W_CROSS: Final[float] = 20.0  # 每个边-边交叉点（直线边：交叉点数 = 交叉边对数）
W_SHORT_EDGE: Final[float] = 0.6  # (128 − len)² 当 len < 128
W_LONG_EDGE: Final[float] = 0.008  # (len − 420)² 当 len > 420
W_SHORT_EXCESS: Final[float] = 2500.0  # 每多一条「<128px」的边（超出 `SHORT_OK`）
W_MEAN_DEFICIT: Final[float] = 15000.0  # 均边每低于有效目标 1px
#: gap 硬约束**做不到**时的降级重罚（每 px 缺口）：双射占满槽位 → 轴间距是分配无关的
#: 常数，达不到就是这套几何达不到。它进成本只为「不弃搜」有显式表示（常数项不影响
#: 择优次序，其余成本项照常优化）—— 见 `_SlotSearch.gap_def` 与 `solve`。
W_GAP_MISS: Final[float] = 5000.0

#: 退火降温比 `T1 = T0 / _T_RATIO`（质变温度，挡住「一上来就把好解扔掉」）。
_T_RATIO: Final[float] = 400.0
#: 标定阶段（全接受随机游走）占本次步数的比例与上下限。
_CALIB_FRAC: Final[float] = 0.12
_MIN_CALIB: Final[int] = 8
_MAX_CALIB: Final[int] = 12000
#: 单次重启的最少步数（预算再小也走几步，别交白卷）。
_MIN_STEPS: Final[int] = 24
#: 死亡线检查的步长间隔（每次都查 `time.monotonic()` 太贵）。
_DEADLINE_CHECK: Final[int] = 64
#: 墙钟上限的余量（秒）：预算之外再给一点，避免机器抖动把最后一步砍掉。
_SLACK_S: Final[float] = 1.0
#: 多重启的种子间隔（与 L1 原型同值）与随机起点的混淆常数。
_SEED_STEP: Final[int] = 7919
_START_MIX: Final[int] = 0x5A5A
#: 浮点比较余量。
_EPS: Final[float] = 1e-9

Rect = tuple[float, float, float, float]
Point = tuple[float, float]
Pair = tuple[int, int]

__all__ = ["EVALS_PER_SEC", "GAP_MIN", "RESTARTS", "signature_of", "solve"]


# --------------------------------------------------------------------------- #
# 几何基元（与 `sa4_lib` / 指标口径**同式**，不许各写一套）
# --------------------------------------------------------------------------- #
def _pt_seg_dist(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """点到线段的距离。"""
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_rect_len(p0: Point, p1: Point, rect: Rect) -> float:
    """线段落在轴对齐矩形内的**长度**（Liang–Barsky 精确裁剪）。"""
    x0, y0 = p0
    x1, y1 = p1
    rx0, ry0, rx1, ry1 = rect
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for p, q in (
        (-dx, x0 - rx0),
        (dx, rx1 - x0),
        (-dy, y0 - ry0),
        (dy, ry1 - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return 0.0
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return 0.0
            t0 = max(t0, r)
        else:
            if r < t0:
                return 0.0
            t1 = min(t1, r)
    if t1 <= t0:
        return 0.0
    return math.hypot(dx, dy) * (t1 - t0)


def _orient(a: Point, b: Point, c: Point) -> int:
    v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    if v > 1e-9:
        return 1
    if v < -1e-9:
        return -1
    return 0


def _proper_cross(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """两线段**真交叉**（严格相交；共线/端点相触不算）。"""
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def _drawn_segment(a: layout.NodeBox, b: layout.NodeBox) -> tuple[Point, Point]:
    """`layout._draw_edge` 的端点数学：两端各内缩 `AVATAR/2 + GAP`（46px）。

    （同对有向双线的 ±7px 垂直错开**不在这里**：那是画线层的事，L1 原型同样按
    直连段评估；口径一致才比得了数。）
    """
    dx, dy = b.cx - a.cx, b.cy - a.cy
    dist = math.hypot(dx, dy) or 1.0
    ux, uy = dx / dist, dy / dist
    cut = layout.AVATAR / 2 + layout.GAP
    return (a.cx + ux * cut, a.cy + uy * cut), (b.cx - ux * cut, b.cy - uy * cut)


def _label_bad(
    line: layout.EdgeLine, boxes: Sequence[layout.NodeBox], *, t_hint: float = 0.5
) -> float:
    """`layout._label_pos` 的**同式副本**：返回「最优落点仍压盒的面积」（0 = 干净）。

    与生产同源（沿线候选 t 的网格、旋转后 AABB、逐盒重叠面积），只多把最小重叠面积
    拿出来当成本。`boxes` 只传**离边线 ≤`CHIP_REACH`** 的盒也不影响结果
    （更远的盒装不下胶囊）。
    """
    rad = math.radians(line.angle)
    dx = math.copysign(line.w, math.cos(rad))
    dy = math.copysign(line.h, math.sin(rad))
    mx, my = line.x + line.w / 2, line.y + line.h / 2
    rects = [(b.x, b.y, b.x + b.w, b.y + b.h) for b in boxes]
    best = math.inf
    for t in layout._label_ts(t_hint):
        ax, ay = mx + (t - 0.5) * dx, my + (t - 0.5) * dy
        chip = layout._rotated_chip_aabb(
            ax, ay, line.angle, layout.CHIP_W, layout.CHIP_H
        )
        bad = 0.0
        for r in rects:
            if chip[0] < r[2] and chip[1] < r[3] and chip[2] > r[0] and chip[3] > r[1]:
                bad += layout._overlap_area(chip, r)
                if bad >= best:
                    break
        if bad < best:
            best = bad
            if best == 0.0:
                return 0.0
    return best


class _Profile(NamedTuple):
    """一条边的全部成本项（「边级」口径，与指标同源）。"""

    length: float
    hit_flag: int
    hit_instances: int
    ink: float
    circle_flag: int
    circle_instances: int
    pen: float
    chip_flag: int
    chip_area: float


def _edge_profile(
    box_a: layout.NodeBox,
    box_b: layout.NodeBox,
    all_boxes: Sequence[layout.NodeBox],
    *,
    t_hint: float = 0.5,
) -> _Profile:
    """一条边的 profile：长度 / 压盒 / 墨长 / 压头像圆 / 标签压盒。

    ⚠️ 几何取 `graph_g6.line_endpoints(layout._segment(...))` ——
    与渲染、指标是**同一条线**（bbox 中心 + 角度 + hypot(w, h) 反算，
    含轴对齐线那个伪造的 1px），不是 `_draw_edge` 的浮点端点。
    """
    (x0, y0), (x1, y1) = _drawn_segment(box_a, box_b)
    line = layout._segment("k", box_a.uid, box_b.uid, x0, y0, x1, y1)
    x0, y0, x1, y1 = graph_g6.line_endpoints(line)
    length = math.hypot(x1 - x0, y1 - y0)
    hit_inst = 0
    ink = 0.0
    cir_inst = 0
    pen = 0.0
    near: list[layout.NodeBox] = []
    for b in all_boxes:
        rect = (b.x, b.y, b.x + b.w, b.y + b.h)
        if b.uid not in (box_a.uid, box_b.uid):
            got = _seg_rect_len((x0, y0), (x1, y1), rect)
            if got > 0.0:
                hit_inst += 1
                ink += got
            d = _pt_seg_dist(b.cx, b.cy, x0, y0, x1, y1)
            if d <= AVATAR_R:
                cir_inst += 1
                pen = max(pen, AVATAR_R - d)
        # chip 预筛：盒外扩 CHIP_REACH 后与线段相交 → 才可能与胶囊重叠
        if (
            _seg_rect_len(
                (x0, y0),
                (x1, y1),
                (
                    rect[0] - CHIP_REACH,
                    rect[1] - CHIP_REACH,
                    rect[2] + CHIP_REACH,
                    rect[3] + CHIP_REACH,
                ),
            )
            > 0.0
        ):
            near.append(b)
    bad = _label_bad(line, near, t_hint=t_hint)
    return _Profile(
        length=length,
        hit_flag=1 if hit_inst else 0,
        hit_instances=hit_inst,
        ink=ink,
        circle_flag=1 if cir_inst else 0,
        circle_instances=cir_inst,
        pen=pen,
        chip_flag=1 if bad > 0.0 else 0,
        chip_area=bad,
    )


def _edge_cost(prof: _Profile) -> float:
    """一条边的成本（权重见模块头，与 L1 原型逐项相同）。"""
    cost = (
        W_CIRCLE_FLAG * prof.circle_flag
        + W_CIRCLE_PEN * prof.pen
        + W_HIT_FLAG * prof.hit_flag
        + W_INK * prof.ink
        + W_CHIP_FLAG * prof.chip_flag
        + W_CHIP_AREA * prof.chip_area
    )
    if prof.length < SHORT_AT:
        cost += W_SHORT_EDGE * (SHORT_AT - prof.length) ** 2
    elif prof.length > LONG_AT:
        cost += W_LONG_EDGE * (prof.length - LONG_AT) ** 2
    return cost


# --------------------------------------------------------------------------- #
# 指纹：与顺序无关的图结构指纹（成员与边都必须进）
# --------------------------------------------------------------------------- #
def signature_of(members: Sequence[int], edges: Sequence[Pair]) -> str:
    """这一簇的**图结构指纹**（16 hex，与顺序无关）。

    成员（去重升序）与边（去重无向对，升序）**都进指纹** —— 任一处变化指纹必变，
    于是「同一张图」才命中同一份缓存解（`store.get_layout_solution`）。
    定向/多重边折叠成无向对：L1 的槽位几何只认「谁跟谁有关系」，不认方向。
    """
    uid_part = ",".join(str(int(u)) for u in sorted({int(m) for m in members}))
    pair_part = ";".join(
        f"{a}-{b}"
        for a, b in sorted({
            (min(int(a), int(b)), max(int(a), int(b))) for a, b in edges
        })
    )
    raw = f"{uid_part}|{pair_part}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _pairs_of(edges: Sequence[Pair]) -> tuple[Pair, ...]:
    """去重无向对（升序）：`ClusterSpec.edges` 与指纹、搜索三方共用的口径。"""
    return tuple(sorted({(min(int(a), int(b)), max(int(a), int(b))) for a, b in edges}))


# --------------------------------------------------------------------------- #
# 历史序（= `layout._cluster` 今天的落位顺序）
# --------------------------------------------------------------------------- #
def _bfs_order(hub: int, ring: Sequence[int], pairs: Sequence[Pair]) -> list[int]:
    """从 hub 出发的 BFS（同层按 uid 升序）——与 `layout._bfs_order` **同一个算法**。

    多环簇按它切片摆位（让图里挨着的人落在相邻槽位）。输入用去重无向对，
    邻接集合与 `layout` 传关系边时逐个相同（那边只读 `.src/.dst`）。
    """
    adj: dict[int, list[int]] = {}
    for a, b in pairs:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    seen = {hub}
    order: list[int] = []
    queue = [hub]
    while queue:
        u = queue.pop(0)
        for v in sorted(adj.get(u, ())):
            if v in seen:
                continue
            seen.add(v)
            order.append(v)
            queue.append(v)
    order.extend(u for u in sorted(ring) if u not in seen)
    return order


def _ring_count(ring_slots: Sequence[Point]) -> int:
    """槽位集里有**几个环**（按半径分桶）：单环 = 1。"""
    return len({round(math.hypot(x, y), 6) for x, y in ring_slots})


def _baseline_order(
    hub: int, ring: Sequence[int], pairs: Sequence[Pair], ring_slots: Sequence[Point]
) -> list[int]:
    """历史序：**单环 = 成员升序**（小簇逐像素不变）；**多环 = BFS 切片**。

    环数从槽位坐标判（半径分桶）——不依赖调用方另传 plan，槽位给什么就用什么。
    """
    if _ring_count(ring_slots) <= 1:
        return list(ring)
    return _bfs_order(hub, ring, pairs)


def _history_slots(hub: int, base_ring: Sequence[int], out_len: int) -> tuple[int, ...]:
    """把历史序摆成**槽位序**（hub 在首位，槽位 0 = 簇心）。"""
    return tuple([hub, *base_ring][:out_len])


# --------------------------------------------------------------------------- #
# 搜索器：位置↔槽位置换 + 预计算表 + 增量评估
# --------------------------------------------------------------------------- #
@dataclass
class _State:
    """一次分配的增量状态（SA 里原地进进退退）。"""

    node_at: list[int]  # 位置 → 节点（位置 0 = hub）
    pos_of: dict[int, int]  # 节点 → 位置
    edge_pos: list[Pair]  # 每条边 → (位置, 位置)
    ec: list[float]  # 每条边的成本
    cross: int  # 边-边交叉点数
    sum_len: float  # 边长合计
    n_short: int  # <128px 的边数
    total: float  # 总成本


@dataclass(frozen=True)
class _Run:
    """一次重启的结果（终解 = 本次跑出来的 best）。"""

    cost: float
    order: tuple[int, ...]  # 槽位序（环上节点，不含 hub）
    start: str  # "baseline" / "random"
    evals: int
    accepted: int


class _SlotSearch:
    """一个簇的「成员 ↔ 槽位」搜索（hub 固定在簇心，只搜环上节点 ↔ 槽位）。

    表 1：任意两位置 → 边的 profile（长度/压盒/墨长/压头像圆/标签压盒）。
    表 2：任意两位置对 → 是否**真交叉**（直线边：每条边对最多 1 个交叉点）。
    """

    def __init__(
        self,
        *,
        hub: int,
        ring: Sequence[int],
        pairs: Sequence[Pair],
        slots: Sequence[Point],
        gap_min: float = GAP_MIN,
    ) -> None:
        self.hub = hub
        self.ring = tuple(ring)
        # 自环（u == u）在槽位几何里没有意义，还会让 `pair_idx` 取到对角线上的 −1。
        self.pairs = tuple((u, v) for u, v in _pairs_of(pairs) if u != v)
        # 位置：0 = hub（簇心）；1..m = 槽位。位置盒的 uid 只作「屋里是谁」的自证
        # （逐位唯一即可），几何完全由坐标决定 —— 真实成员身份由 `node_at` 单独维护。
        self.pos_boxes: list[layout.NodeBox] = [
            layout._box(0.0, 0.0, 0),
            *[layout._box(x, y, k + 1) for k, (x, y) in enumerate(slots)],
        ]
        self.P = len(self.pos_boxes)
        if len(self.ring) != self.P - 1:
            msg = f"槽位数 {self.P - 1} 与环上节点数 {len(self.ring)} 不匹配"
            raise ValueError(msg)
        # 硬约束：轴间距只取决于槽位集几何（双射占满槽位），与分配无关 → 一次性算出。
        # 达不到 `gap_min` 时**不弃搜**，只把它降级成常数重罚项（见 `gap_def` 的注释）。
        self.min_gap = layout._boxes_gap(tuple(self.pos_boxes))
        #: 降级重罚（常数项）：0 = 槽位几何满足硬约束；> 0 = 做不到（重罚进成本）。
        self.gap_def = W_GAP_MISS * max(0.0, float(gap_min) - self.min_gap)
        first = [self.pos_boxes[i] for i in range(self.P)]
        idx = [[-1] * self.P for _ in range(self.P)]
        pos_pairs: list[Pair] = []
        for i in range(self.P):
            for j in range(i + 1, self.P):
                idx[i][j] = idx[j][i] = len(pos_pairs)
                pos_pairs.append((i, j))
        self.pair_idx = idx
        self.prof: list[_Profile] = [
            _edge_profile(first[i], first[j], first) for i, j in pos_pairs
        ]
        seg = [_drawn_segment(first[i], first[j]) for i, j in pos_pairs]
        rows: list[bytes] = []
        for p, ((ax0, ay0), (ax1, ay1)) in enumerate(seg):
            bxlo, bxhi = min(ax0, ax1), max(ax0, ax1)
            bylo, byhi = min(ay0, ay1), max(ay0, ay1)
            row = bytearray(len(pos_pairs))
            for q in range(p + 1, len(pos_pairs)):
                (cx0, cy0), (cx1, cy1) = seg[q]
                if max(cx0, cx1) < bxlo or min(cx0, cx1) > bxhi:
                    continue
                if max(cy0, cy1) < bylo or min(cy0, cy1) > byhi:
                    continue
                if _proper_cross((ax0, ay0), (ax1, ay1), (cx0, cy0), (cx1, cy1)):
                    row[q] = 1
            rows.append(bytes(row))
        self.cross_rows = rows
        self.E = len(self.pairs)
        link: dict[int, list[int]] = {}
        for k, (u, v) in enumerate(self.pairs):
            link.setdefault(u, []).append(k)
            link.setdefault(v, []).append(k)
        self.link = link
        # other[m] = 与第 m 条边**不共端点**的边（交叉口径只算这些）
        shares = [
            {i for i in link[u] if i != k} | {i for i in link[v] if i != k}
            for k, (u, v) in enumerate(self.pairs)
        ]
        self.other = [
            [n for n in range(self.E) if n != k and n not in shares[k]]
            for k in range(self.E)
        ]
        # 均边的「有效目标」：先量出每类边**可能达到的最大长度**，再拿它的 0.8 倍给
        # 250px 封顶（小簇单环里 hub 放射边恒为 128px、最长弦也只有 348px，均边根本
        # 到不了 250 —— 不封顶的话这一项会变成压死一切的大常数）。
        hub_len = [
            self.prof[self.pair_idx[i][j]].length for i, j in pos_pairs if i == 0
        ]
        ring_len = [
            self.prof[self.pair_idx[i][j]].length for i, j in pos_pairs if i != 0
        ]
        hub_max = max(hub_len, default=0.0)
        ring_max = max(ring_len, default=0.0)
        upper = [hub_max if hub in pair else ring_max for pair in self.pairs]
        self.mean_upper = (sum(upper) / self.E) if self.E else 0.0
        self.mean_target = min(MEAN_TARGET, 0.8 * self.mean_upper)

    # ------------------------------------------------------------------ #
    def _positions(self, edge_pos: Sequence[Pair], k: int) -> int:
        p, q = edge_pos[k]
        return self.pair_idx[p][q]

    def _state(self, order: Sequence[int]) -> _State:
        """从零建状态（也用来**校验**增量评估没有漂移）。"""
        node_at = [0] * self.P
        pos_of = {self.hub: 0}
        node_at[0] = self.hub
        for k, uid in enumerate(order):
            node_at[k + 1] = uid
            pos_of[uid] = k + 1
        edge_pos = [(pos_of[u], pos_of[v]) for u, v in self.pairs]
        ec = [
            _edge_cost(self.prof[self._positions(edge_pos, k)]) for k in range(self.E)
        ]
        lens = [self.prof[self._positions(edge_pos, k)].length for k in range(self.E)]
        sum_len = sum(lens)
        n_short = sum(1 for x in lens if x < SHORT_AT)
        cross = self._cross_all(edge_pos)
        total = self._total(ec, cross, sum_len, n_short)
        return _State(node_at, pos_of, edge_pos, ec, cross, sum_len, n_short, total)

    def _penalty(self, sum_len: float, n_short: int) -> float:
        """整簇两项：`<128px` 边数超出 `SHORT_OK` 的部分 + 均边亏空。"""
        mean = sum_len / max(1, self.E)
        return W_SHORT_EXCESS * max(0, n_short - SHORT_OK) + W_MEAN_DEFICIT * max(
            0.0, self.mean_target - mean
        )

    def _total(
        self, ec: Sequence[float], cross: int, sum_len: float, n_short: int
    ) -> float:
        """总成本 = 边成本 + 交叉 + 整簇两项 + gap 降级重罚（常数，见 `gap_def`）。"""
        return (
            sum(ec) + W_CROSS * cross + self._penalty(sum_len, n_short) + self.gap_def
        )

    def cost_of(self, order: Sequence[int]) -> float:
        """从零重算某个分配的总成本（**校验用**：与增量评估逐位相同才对）。"""
        st = self._state(order)
        return st.total

    def _cross_all(self, edge_pos: Sequence[Pair]) -> int:
        total = 0
        for m in range(self.E):
            pm = self._positions(edge_pos, m)
            for n in self.other[m]:
                if n > m:
                    pn = self._positions(edge_pos, n)
                    total += (
                        self.cross_rows[pm][pn] if pm <= pn else self.cross_rows[pn][pm]
                    )
        return total

    def _cross_contrib(self, edge_pos: Sequence[Pair], affected: frozenset[int]) -> int:
        """「至少一端在 affected」的边对交叉数（每对只计一次）。"""
        total = 0
        for m in affected:
            pm = self._positions(edge_pos, m)
            for n in self.other[m]:
                if n > m or (n < m and n not in affected):
                    pn = self._positions(edge_pos, n)
                    total += (
                        self.cross_rows[pm][pn] if pm <= pn else self.cross_rows[pn][pm]
                    )
        return total

    def _step(self, st: _State, rng: random.Random, temp: float) -> tuple[float, bool]:
        """一次随机交换（应用或回滚），返回 `(Δ, 是否接受)`。"""
        m = len(self.ring)
        i1 = rng.randrange(1, m + 1)
        i2 = rng.randrange(1, m)
        if i2 >= i1:
            i2 += 1
        u, v = st.node_at[i1], st.node_at[i2]
        affected = frozenset(self.link.get(u, ())) | frozenset(self.link.get(v, ()))
        old_contrib = self._cross_contrib(st.edge_pos, affected)
        old_local = 0.0
        old_len = 0.0
        old_short = 0
        for k in affected:
            old_local += st.ec[k]
            length = self.prof[self._positions(st.edge_pos, k)].length
            old_len += length
            if length < SHORT_AT:
                old_short += 1
        st.node_at[i1], st.node_at[i2] = v, u
        st.pos_of[u], st.pos_of[v] = i2, i1
        new_local = 0.0
        new_len = 0.0
        new_short = 0
        new_ec: dict[int, float] = {}
        for k in affected:
            a, b = self.pairs[k]
            st.edge_pos[k] = (st.pos_of[a], st.pos_of[b])
            prof = self.prof[self._positions(st.edge_pos, k)]
            new_ec[k] = _edge_cost(prof)
            new_local += new_ec[k]
            new_len += prof.length
            if prof.length < SHORT_AT:
                new_short += 1
        new_contrib = self._cross_contrib(st.edge_pos, affected)
        n_sum = st.sum_len - old_len + new_len
        n_cnt = st.n_short - old_short + new_short
        delta = (
            (new_local - old_local)
            + W_CROSS * (new_contrib - old_contrib)
            + self._penalty(n_sum, n_cnt)
            - self._penalty(st.sum_len, st.n_short)
        )
        ok = (
            delta <= 0.0
            or temp == math.inf
            or rng.random() < math.exp(-delta / max(temp, 1e-9))
        )
        if not ok:
            st.node_at[i1], st.node_at[i2] = u, v
            st.pos_of[u], st.pos_of[v] = i1, i2
            for k in affected:
                a, b = self.pairs[k]
                st.edge_pos[k] = (st.pos_of[a], st.pos_of[b])
            return delta, False
        for k in affected:
            st.ec[k] = new_ec[k]
        st.cross += new_contrib - old_contrib
        st.sum_len, st.n_short = n_sum, n_cnt
        st.total += delta
        return delta, True

    def run(
        self,
        start: Sequence[int],
        *,
        seed: int,
        steps: int,
        deadline: float,
        source: str,
    ) -> _Run:
        """模拟退火：先用「全接受」随机游走标定 |Δ| 中位数 → T0，再几何降温到 T1。"""
        rng = random.Random(seed)
        st = self._state(start)
        best_cost, best_order = st.total, list(st.node_at[1:])
        calib = max(_MIN_CALIB, min(int(steps * _CALIB_FRAC), _MAX_CALIB))
        samples: list[float] = []
        t0, t1 = 1000.0, 2.5
        cooled = False
        evals = accepted = 0
        for step in range(steps):
            if step % _DEADLINE_CHECK == 0 and time.monotonic() >= deadline:
                break
            temp = math.inf
            if step >= calib:
                if not cooled:  # 标定结束：|Δ| 中位数 → T0，再定 T1
                    samples.sort()
                    t0 = samples[len(samples) // 2] if samples else 1000.0
                    t1 = max(1.0, t0 / _T_RATIO)
                    cooled = True
                frac = (step - calib) / max(1, steps - calib)
                temp = t0 * (t1 / t0) ** frac
            delta, ok = self._step(st, rng, temp)
            if step < calib and delta > 0.0:
                samples.append(delta)
            evals += 1
            accepted += ok
            if st.total < best_cost - _EPS:
                best_cost, best_order = st.total, list(st.node_at[1:])
        return _Run(
            cost=best_cost,
            order=tuple(best_order),
            start=source,
            evals=evals,
            accepted=accepted,
        )


# --------------------------------------------------------------------------- #
# 公开 API
# --------------------------------------------------------------------------- #
def solve(
    slots: Sequence[Point],
    *,
    members: Sequence[int],
    hub: int,
    edges: Sequence[Pair],
    budget_s: float,
    seed: int,
    gap_min: float = GAP_MIN,
) -> tuple[int, ...]:
    """解「成员 ↔ 槽位」的双射：**返回按槽位下标排列的 uid 序列**。

    Args:
        slots: 槽位坐标。契约口径 = **含 hub 槽**（`slots[0]` = 簇心，长度 ==
            `len(members)`）；只有环槽位（长度 == `len(members) - 1`、hub 隐含在
            `(0, 0)`）也收 —— 两种口径返回的都是同一件事：槽位序（首位 = hub 槽）。
        members: 本簇成员（升序与否都行，内部会排序）。
        hub: 簇心那位（固定坐在簇心，不参与搜索）。
        edges: 本簇的**去重无向对**（`ClusterSpec.edges` / `signature_of` 同一口径）。
        budget_s: 秒预算；`<= 0` → 立刻回历史序（防倒退：不劣化）。
        seed: 随机种子（同种子同参数 → 同一个解）。
        gap_min: 硬约束（节点盒两两最小轴间距，px）；槽位几何整体做不到时**不弃搜**，
            只把它降级成成本里的常数重罚项（其余成本项照常优化，见模块头第 4 条）。

    Returns:
        `len == len(slots)`、`members` 的置换；首位 = hub（簇心）。
        形状对不上（槽位数 ≠ 环上节点数、成员不足 2 个）→ 回历史序的前缀
        （调用方按长度校验后回退，`handle._uids_fit` 就是这么用的）。
    """
    member_list = sorted({int(m) for m in members})
    hub = int(hub)
    ring = [u for u in member_list if u != hub]
    slot_list: list[Point] = [(float(x), float(y)) for x, y in slots]
    ring_slots = slot_list[1:] if len(slot_list) == len(member_list) else slot_list
    base = _baseline_order(hub, ring, _pairs_of(edges), ring_slots)
    out_len = len(slot_list)
    if len(ring_slots) != len(ring) or len(ring) <= 1:
        # 形状对不上，或 2 人簇（水平对：唯一的那个解，没有可搜的自由度）
        return _history_slots(hub, base, out_len)
    if budget_s <= 0.0:
        return _history_slots(hub, base, out_len)
    search = _SlotSearch(
        hub=hub, ring=ring, pairs=edges, slots=ring_slots, gap_min=gap_min
    )
    # gap 硬约束：槽位集自身的轴间距是**分配无关的常数**（双射占满槽位，谁坐都一样），
    # 达不到 `gap_min` 就是这套几何整体做不到 —— **不弃搜**：它降级成成本里的重罚项
    # （`search.gap_def`，常数项，不改变择优次序），其余成本项照常优化（2026-10-17 审查
    # 第 1 条：实测 10 人单环簇弃搜时那条弦离 hub 中心 14.56px（< 32，压头像），
    # 照搜解出 191.5px）。达到 `gap_min` 时约束对所有分配自动成立，无须另做手脚。
    deadline = time.monotonic() + budget_s + _SLACK_S
    steps = max(_MIN_STEPS, int(budget_s * EVALS_PER_SEC / RESTARTS))
    best_order, best_cost = list(base), search.cost_of(base)
    for i in range(RESTARTS):
        if i == 0:
            start, source = list(base), "baseline"
        else:
            start = random.Random((seed + _SEED_STEP * i) ^ _START_MIX).sample(
                ring, len(ring)
            )
            source = "random"
        run = search.run(
            start,
            seed=seed + _SEED_STEP * i,
            steps=steps,
            deadline=deadline,
            source=source,
        )
        # 只有**严格更优**才替换 → 基线序永远在候选池里（防倒退）
        if run.cost < best_cost - _EPS:
            best_order, best_cost = list(run.order), run.cost
    return _history_slots(hub, best_order, out_len)
