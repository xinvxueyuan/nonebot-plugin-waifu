"""槽位重排求解器（`slots`）：指纹、置换、历史序回退、共线回归、确定性、增量表。

几何全部沿用 `layout`（槽位坐标一个不动），所以这里钉的是**求解器与接线**：
- `signature_of`：成员与边都必须进指纹，且与顺序无关（缓存命中的唯一依据）；
- `solve`：返回「按槽位排列的 uid 序列」（首位 = hub）、`budget_s <= 0` 立刻回历史序、
  密单环（槽位自身轴间距 < `gap_min`）**不弃搜**（gap 降级为重罚项，其余项照常搜）；
- **共线回归**（用户 2026-10-17 原话的用例）：A–B–C 共线时 A–C 的连线本来压在 B 头上，
  重排之后不许再穿 B 的头像圆；
- 确定性：同参数同种子 → 同一个解（预算是**确定的步数**，不是墙钟）；
- 增量评估无漂移：SA 终解的成本与「从零重算」逐位相同（L1 原型的自证）。
"""

from __future__ import annotations

import math
import time


def _l():
    from nonebot_plugin_waifu import layout

    return layout


def _s():
    from nonebot_plugin_waifu import slots

    return slots


def _rel():
    from nonebot_plugin_waifu import relations

    return relations


def _graph(pairs, kind: str = "marriage"):
    """由无向对造单簇图（无向 `marriage`；有向边不影响槽位几何）。"""
    rel = _rel()
    es = tuple(rel.RelationEdge(int(u), int(v), kind) for u, v in pairs)
    return rel.RelationGraph(
        edges=es,
        nodes=tuple(sorted({m for e in es for m in (e.src, e.dst)})),
        total_edges=len(es),
    )


def _pairs(g):
    """去重无向对（测试里自己再来一份：它是契约的一部分，不该只由实现说了算）。"""
    return tuple(sorted({(min(e.src, e.dst), max(e.src, e.dst)) for e in g.edges}))


def _only_spec(g):
    (spec,) = _l().cluster_specs(g)
    return spec


def _solved(g, spec, budget: float, seed: int = 20261017):
    """求解 + 按 `signature_of` 交给 `layout_graph`（= handle.py 的接线）。"""
    sig = _s().signature_of(spec.members, spec.edges)
    uids = _s().solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=budget,
        seed=seed,
    )
    return uids, _l().layout_graph(g, assignments={sig: uids})


def _box_center(box):
    return (box.x + box.w / 2, box.y + box.h / 2)


def _pt_seg_dist(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    t = (
        0.0
        if l2 == 0
        else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    )
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def _line_ends(line):
    """画出来的线段两端（与渲染同源：`graph_g6.line_endpoints`）。"""
    from nonebot_plugin_waifu import graph_g6

    x0, y0, x1, y1 = graph_g6.line_endpoints(line)
    return (x0, y0), (x1, y1)


def _dist_to_edge(cluster, uid: int, a: int, b: int) -> float:
    """`uid` 的**头像圆心**到 (a,b) 那条**画出来的**线段的距离（px）。"""
    boxes = {n.uid: n for n in cluster.nodes}
    line = next(ln for ln in cluster.lines if {ln.src, ln.dst} == {a, b})
    return _pt_seg_dist(_box_center(boxes[uid]), *_line_ends(line))


# --------------------------------------------------------------------------- #
# 成本口径：压头像圆是**第一优先**（手搓几何直接钉住权重）
# --------------------------------------------------------------------------- #
def test_circle_piercing_is_the_dominant_cost_term():
    """一条边压到**别人头像圆**时，成本至少要高出 `W_CIRCLE_FLAG`（6000）。

    手搓几何：A–B 水平相距 300px，同一个第三者在正中间（150, 0）→ 画出来的线
    从 ta 头像圆心穿过（`circle_flag=1`、`pen≈32`）；把 ta 挪到 400px 外 → 同一条边
    干净（`circle_flag=0`、无侵入）。两个 profile 的成本差必须 ≥ `W_CIRCLE_FLAG`
    —— 这就是「压头像圆第一优先」的权重口径（权重被清零 → 评图/求解都治不住压头像）。
    """
    s = _s()
    lay = _l()
    a = lay._box(0.0, 0.0, 1)
    b = lay._box(300.0, 0.0, 2)
    victim_on = lay._box(150.0, 0.0, 3)
    victim_away = lay._box(150.0, 400.0, 3)
    hit = s._edge_profile(a, b, (a, b, victim_on))
    far = s._edge_profile(a, b, (a, b, victim_away))
    assert hit.circle_flag == 1 and hit.pen > 30.0, "用例失效：线没有压到那个头像"
    assert far.circle_flag == 0 and far.pen == 0.0, "用例失效：挪开的头像仍被判压"
    # ⚠️ 阈值写死 6000（= `slots.W_CIRCLE_FLAG`），**不引用常量**：引用会让
    # 「把常量改小/清零」的变异体拿自己改小的阈值把自己放过 —— 这正是本条要抓的。
    assert s._edge_cost(hit) - s._edge_cost(far) >= 6000.0, (
        "压头像圆的权重被动了（第一优先口径）"
    )


# --------------------------------------------------------------------------- #
# 指纹
# --------------------------------------------------------------------------- #
def test_signature_is_order_independent_covers_members_and_edges():
    """`signature_of`：16 hex、与顺序/边方向无关，但**成员与边任一处变化都要变**。

    它是缓存的唯一键（`store.get_layout_solution`）—— 少进一样东西，两张不同的图
    就会互相命中对方的解（摆错人）。
    """
    s = _s()
    members = [3, 1, 2]
    edges = [(2, 3), (1, 2)]
    sig = s.signature_of(members, edges)
    assert len(sig) == 16 and all(c in "0123456789abcdef" for c in sig)
    # 顺序无关 / 边方向无关 / 重复边折叠
    assert s.signature_of([1, 2, 3], [(2, 3), (1, 2)]) == sig
    assert s.signature_of([3, 2, 1], [(3, 2), (2, 1)]) == sig
    assert s.signature_of([1, 2, 3], [(1, 2), (2, 3), (1, 2)]) == sig
    # 成员变化 → 变
    assert s.signature_of([1, 2, 3, 4], edges) != sig
    assert s.signature_of([1, 2, 9], edges) != sig
    # 边变化 → 变（加边 / 换端点）
    assert s.signature_of(members, [(2, 3), (1, 2), (1, 3)]) != sig
    assert s.signature_of(members, [(2, 3)]) != sig
    assert s.signature_of(members, [(2, 3), (1, 3)]) != sig


# --------------------------------------------------------------------------- #
# 返回形状：槽位序的置换
# --------------------------------------------------------------------------- #
def test_solve_returns_a_slot_ordered_permutation_of_members():
    """`solve` 返回**按槽位下标排列的 uid 序列**：长度 = 槽位数、是置换、首位 = hub。"""
    s = _s()
    for n in (3, 5, 9, 12):
        g = _graph([(1, i) for i in range(2, n + 1)])
        spec = _only_spec(g)
        uids = s.solve(
            spec.slot_xy,
            members=spec.members,
            hub=spec.hub,
            edges=spec.edges,
            budget_s=0.3,
            seed=7,
        )
        assert len(uids) == len(spec.slot_xy) == len(spec.members), f"{n} 人簇长度不对"
        assert sorted(uids) == sorted(spec.members), f"{n} 人簇不是置换"
        assert uids[0] == spec.hub, f"{n} 人簇的 hub 不在簇心那个槽"


def test_solve_with_non_positive_budget_returns_the_historical_order():
    """`budget_s <= 0` → **立刻**回历史序（防倒退的兜底，也是「关掉搜索」的语义）。

    历史序 = `layout._cluster` 今天的落位顺序：**单环 = 成员升序**、
    **多环 = 从 hub 出发的 BFS**。顺带钉住「调用方按长度校验」那一步
    （`handle._uids_fit`：长度相等 + 是置换）。
    """
    s = _s()
    lay = _l()
    rel = _rel()
    # 单环（8 个环上节点）
    g = _graph([(1, i) for i in range(2, 10)])
    spec = _only_spec(g)
    want = (spec.hub, *sorted(u for u in spec.members if u != spec.hub))
    for budget in (0.0, -5.0):
        uids = s.solve(
            spec.slot_xy,
            members=spec.members,
            hub=spec.hub,
            edges=spec.edges,
            budget_s=budget,
            seed=1,
        )
        assert uids == want, f"budget={budget} 没回历史序"
    # 多环（12 个环上节点 → (3, 9) 两环）：历史序 = BFS 切片
    g = _graph([(1, i) for i in range(2, 14)])
    spec = _only_spec(g)
    ring = [u for u in spec.members if u != spec.hub]
    pairs = spec.edges
    es = tuple(rel.RelationEdge(u, v, "marriage") for u, v in pairs)
    want = (spec.hub, *lay._bfs_order(spec.hub, ring, es))
    uids = s.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=0.0,
        seed=1,
    )
    assert uids == want, "多环簇的 budget<=0 回退不是 BFS 历史序"


def test_solve_time_is_not_spent_when_there_is_nothing_to_search():
    """没有可搜的东西就别花时间：2 人簇（水平对）瞬间返回。

    `handle.py` 的出图路径是同步等这个解的（线程里跑），白等 8s 是事故。
    """
    s = _s()
    g = _graph([(3, 5)])
    spec = _only_spec(g)
    t0 = time.monotonic()
    uids = s.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=8.0,
        seed=1,
    )
    assert time.monotonic() - t0 < 1.0
    assert sorted(uids) == sorted(spec.members) and uids[0] == spec.hub


# --------------------------------------------------------------------------- #
# 密单环（槽位自身轴间距 < gap_min）：**不弃搜**，gap 降级为重罚项（审查第 1 条）
# --------------------------------------------------------------------------- #
def _dense_ring(n_ring: int):
    """单环构造：hub 9000 + `n_ring` 个环节点 + 一条 ring[0]–ring[5] 弦（审查 probe4）。

    `n_ring=9` → 10 人单环、槽位最坏轴间距 11px（< 16）；`n_ring=10` → 11 人、−4px。
    两种都低于 `GAP_MIN` —— 旧实现直接弃搜、历史序原样返回。
    """
    hub = 9000
    ring = [9000 + k for k in range(1, n_ring + 1)]
    pairs = [(hub, u) for u in ring] + [(ring[0], ring[5])]
    return _graph(pairs), hub, (ring[0], ring[5])


def test_dense_single_ring_is_optimised_instead_of_abstaining():
    """槽位自身轴间距 < `gap_min` 的簇**不再弃搜**（审查第 1 条的靶子）。

    照审查 probe4 的构造：10 人单环 + ring[0]–ring[5] 弦。历史序下那条弦离 hub 中心
    **14.56px**（< 32 → 压在 hub 头像上），旧实现「弃搜」把它原样交还；修复后同一构造
    同参数（budget 2.0 / seed 20261017）解出 **191.5px**、且解 ≠ 历史序。
    """
    s = _s()
    g, hub, (a, b) = _dense_ring(9)
    spec = _only_spec(g)
    boxes = [_l()._box(x, y, i) for i, (x, y) in enumerate(spec.slot_xy)]
    assert _l()._boxes_gap(tuple(boxes)) < s.GAP_MIN, "用例失效：这簇槽位并不密"
    hist = (spec.hub, *sorted(u for u in spec.members if u != spec.hub))
    base = s.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=0.0,
        seed=1,
    )
    assert base == hist
    (before,) = _l().layout_graph(g)
    d0 = _dist_to_edge(before, hub, a, b)
    assert d0 < _l().AVATAR / 2, f"历史序竟然没压 hub 头像：d={d0:.2f}px（用例失效了）"

    uids = s.solve(
        spec.slot_xy,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        budget_s=2.0,
        seed=20261017,
    )
    assert sorted(uids) == list(spec.members) and uids[0] == spec.hub
    assert uids != hist, "密单环簇又被弃搜了（原样返回历史序）"
    (after,) = _l().layout_graph(
        g, assignments={s.signature_of(spec.members, spec.edges): uids}
    )
    d1 = _dist_to_edge(after, hub, a, b)
    assert d1 > _l().AVATAR / 2, f"那条弦仍然穿过 hub 的头像圆：d={d1:.2f}px"
    assert d1 > 150.0, f"弦距只修到 {d1:.2f}px（probe4 实测 191.5px）"


def test_dense_ring_never_loses_to_the_historical_order():
    """防倒退在密单环上照成立：解成本 ≤ 历史序成本（基线序永远在候选池里）。

    顺带钉「gap 降级」：密单环的 `_SlotSearch.gap_def > 0`（重罚进成本但不弃搜）；
    8 人单环（轴间距 22px ≥ 16）则 = 0（约束整体成立，无须重罚）。
    """
    s = _s()
    for n_ring in (9, 10):
        g, _hub, _ab = _dense_ring(n_ring)
        spec = _only_spec(g)
        ring = [u for u in spec.members if u != spec.hub]
        search = s._SlotSearch(
            hub=spec.hub, ring=ring, pairs=spec.edges, slots=spec.slot_xy[1:]
        )
        assert search.min_gap < s.GAP_MIN
        assert search.gap_def > 0.0, "密单环的 gap 降级重罚没有记进成本"
        base_cost = search.cost_of(ring)
        for budget in (0.0, 0.2, 1.0):
            uids = s.solve(
                spec.slot_xy,
                members=spec.members,
                hub=spec.hub,
                edges=spec.edges,
                budget_s=budget,
                seed=20261017,
            )
            assert sorted(uids) == list(spec.members) and uids[0] == spec.hub
            got = search.cost_of([u for u in uids if u != spec.hub])
            assert got <= base_cost + 1e-9, (
                f"n_ring={n_ring} budget={budget} 的解比历史序还差（防倒退失效）"
            )
    g8 = _graph([(1, i) for i in range(2, 10)])  # 8 个环上节点：22px ✓
    spec8 = _only_spec(g8)
    ring8 = [u for u in spec8.members if u != spec8.hub]
    search8 = s._SlotSearch(
        hub=spec8.hub, ring=ring8, pairs=spec8.edges, slots=spec8.slot_xy[1:]
    )
    assert search8.min_gap >= s.GAP_MIN, "8 人单环的轴间距应当 ≥ GAP_MIN"
    assert search8.gap_def == 0.0, "够疏的槽位不该被 gap 重罚"


# --------------------------------------------------------------------------- #
# 用户 2026-10-17 的用例：A–B–C 共线，A–C 的连线贴着 B
# --------------------------------------------------------------------------- #
def _collinear9():
    """9 节点：hub B + 8 环（单环 220）。

    基线（单环按 uid 升序）A=9004 落槽 3、C=9008 落槽 7 → A–C 弦的角距 171.4°、
    离圆心（B 的头像）只有 16.8px < 32 → **A–C 压在 B 头上**（同类还有 9001–9005）。
    """
    hub, a, c = 9000, 9004, 9008
    ring = [9001, 9002, 9003, a, 9005, 9006, 9007, c]
    pairs = [
        (a, c),
        (hub, c),
        *[(hub, u) for u in ring if u != c],
        (9001, 9005),
        (9003, 9007),
    ]
    return _graph(pairs), hub, a, c


def test_collinear_case_ac_chord_no_longer_pierces_the_b_avatar():
    """**共线回归**：重排之后 A–C 的连线不再穿过 B 的头像圆（r=32px）。

    这是用户原话那类结构（「有 abc 近似在直线上的成员，如果 ac 连线，容易与 bc
    之间重叠」）。基线必须真的压着（16.8px < 32px），否则这条回归没有意义。
    """
    g, hub, a, c = _collinear9()
    (base,) = _l().layout_graph(g)
    d0 = _dist_to_edge(base, hub, a, c)
    assert d0 < _l().AVATAR / 2, f"基线竟然没压：d={d0:.2f}px（用例失效了）"
    uids, got = _solved(g, _only_spec(g), 1.0)
    assert len(uids) == 9
    (after,) = got
    d1 = _dist_to_edge(after, hub, a, c)
    assert d1 > _l().AVATAR / 2, f"A–C 仍然穿过 B 的头像圆：d={d1:.2f}px"


def test_strict_collinear_case_b_is_at_the_midpoint_and_gets_moved_off():
    """强版共线：12 节点、槽位方案 3 环 (1,1,9)，**A–B–C 严格共线且 B 恰为中点**。

    基线 A(0,−220)/B(0,−360)/C(0,−500)：`|AB|=|BC|=140`，A–C 正从 B 中心穿过
    （距离 0.5px，理论上限侵入 31.5px）。重排后 B 必须不在 A–C 上。
    """
    lay = _l()
    s = _s()
    hub, a, b = 8000, 8001, 8002
    outer = list(range(8003, 8012))
    pairs = [
        (hub, a),
        (hub, b),
        *[(hub, u) for u in outer],
        (a, 8007),  # A–C
        (b, 8007),  # B–C
    ]
    # 12 人的单环上限是 10，所以生产 `_ring_plan` 不会给出这个 plan：
    # 这里**手搭槽位**（求解器的入参就是槽位坐标，与它从哪来无关）。
    forced_plan = ((220.0, 1), (360.0, 1), (500.0, 9))
    slots = [(0.0, 0.0)]
    for radius, count in forced_plan:
        for theta in lay._fan_angles(count):
            ang = math.radians(theta)
            slots.append((radius * math.cos(ang), radius * math.sin(ang)))
    members = sorted({m for p in pairs for m in p})

    def place(uids):
        order = [u for u in uids if u != hub]
        boxes = {hub: lay._box(0.0, 0.0, hub)}
        for k, uid in enumerate(order):
            boxes[uid] = lay._box(slots[k + 1][0], slots[k + 1][1], uid)
        return boxes

    def d_ac(boxes):
        line, _ports = lay._draw_edge(
            boxes[a], boxes[8007], _rel().RelationEdge(a, 8007, "marriage")
        )
        return _pt_seg_dist(_box_center(boxes[b]), *_line_ends(line))

    base_uids = s.solve(
        slots, members=members, hub=hub, edges=pairs, budget_s=0.0, seed=20261017
    )
    assert base_uids == (hub, *sorted(u for u in members if u != hub)), (
        "历史序应当是 uid 升序"
    )
    base_boxes = place(base_uids)
    # 基线：严格共线 + B 恰在中点（这就是用户看到的那类重叠）
    ax, ay = _box_center(base_boxes[a])
    bx, by = _box_center(base_boxes[b])
    cx, cy = _box_center(base_boxes[8007])
    cross = abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
    assert cross < 1e-6
    assert math.hypot(bx - (ax + cx) / 2, by - (ay + cy) / 2) < 0.6
    assert d_ac(base_boxes) < lay.AVATAR / 2
    # 重排后：B 不再被 A–C 穿过
    uids = s.solve(
        slots, members=members, hub=hub, edges=pairs, budget_s=1.0, seed=20261017
    )
    assert sorted(uids) == members and uids[0] == hub
    assert d_ac(place(uids)) > lay.AVATAR / 2


# --------------------------------------------------------------------------- #
# 确定性 + 增量表自证
# --------------------------------------------------------------------------- #
def test_solve_is_deterministic_for_the_same_params():
    """同参数同种子 → **同一个解**（预算是确定的步数，不是墙钟）。

    签名一样的缓存行「每次出图都摆得一样」全靠这条；测试与实拍对得上也靠它
    （L1 原型在墙钟预算下只是「大概率一样」，这里改成步数）。
    """
    s = _s()
    g, _hub, _a, _c = _collinear9()
    spec = _only_spec(g)
    kw = {
        "members": spec.members,
        "hub": spec.hub,
        "edges": spec.edges,
        "seed": 20261017,
    }
    first = s.solve(spec.slot_xy, budget_s=1.0, **kw)
    second = s.solve(spec.slot_xy, budget_s=1.0, **kw)
    assert first == second
    # 不同种子可以不同（不是死值），但都必须是合法置换
    other = s.solve(
        spec.slot_xy,
        budget_s=1.0,
        members=spec.members,
        hub=spec.hub,
        edges=spec.edges,
        seed=999,
    )
    assert sorted(other) == sorted(spec.members)


def test_incremental_evaluation_has_no_drift():
    """SA 终解的成本 == 从零重算的成本（L1 原型的自证：预计算表没有漂移）。"""
    s = _s()
    g, _hub, _a, _c = _collinear9()
    spec = _only_spec(g)
    ring = [u for u in spec.members if u != spec.hub]
    search = s._SlotSearch(
        hub=spec.hub, ring=ring, pairs=spec.edges, slots=spec.slot_xy[1:]
    )
    t0 = time.monotonic()
    run = search.run(
        ring, seed=4242, steps=3000, deadline=time.monotonic() + 30.0, source="baseline"
    )
    assert time.monotonic() - t0 < 10.0
    assert run.evals > 0
    assert abs(search.cost_of(run.order) - run.cost) < 1e-6


def test_short_budget_never_loses_to_the_historical_order():
    """**防倒退**：基线序永远在候选池里 → 任何预算下解都不劣于历史序。

    代价口径就是求解器自己的成本函数（`_SlotSearch.cost_of`，从零重算）：
    预算越小动的步子越少，但**不可能变差** —— 这就是「短预算下出图不会更糟」的地基。
    """
    s = _s()
    g, _hub, _a, _c = _collinear9()
    spec = _only_spec(g)
    ring = [u for u in spec.members if u != spec.hub]
    search = s._SlotSearch(
        hub=spec.hub, ring=ring, pairs=spec.edges, slots=spec.slot_xy[1:]
    )
    base = (spec.hub, *ring)
    base_cost = search.cost_of(ring)
    for budget in (0.0, 0.05, 0.5):
        uids = s.solve(
            spec.slot_xy,
            members=spec.members,
            hub=spec.hub,
            edges=spec.edges,
            budget_s=budget,
            seed=20261017,
        )
        assert uids[0] == base[0] and sorted(uids) == sorted(base)
        assert search.cost_of([u for u in uids if u != spec.hub]) <= base_cost + 1e-9, (
            f"budget={budget} 的解比历史序还差（防倒退失效）"
        )
    # 历史序本身「不劣于自己」是废话 —— 这里连预算 0 的那一档也一起钉了：
    assert (
        s.solve(
            spec.slot_xy,
            members=spec.members,
            hub=spec.hub,
            edges=spec.edges,
            budget_s=0.0,
            seed=1,
        )
        == base
    )
