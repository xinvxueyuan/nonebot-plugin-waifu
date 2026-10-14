"""关系模型纯函数：夫妻去重、倾心/喜欢方向与去重优先级、截断、ego 视图。"""

from __future__ import annotations


def _r():
    from nonebot_plugin_waifu import relations

    return relations


def test_marriage_rows_dedupe_to_one_undirected_edge():
    r = _r()
    g = r.build_relations(marriages=[(2, 1, 10.0), (1, 2, 10.0)], crushes={})
    assert len(g.edges) == 1
    e = g.edges[0]
    assert (e.src, e.dst, e.kind) == (1, 2, r.KIND_MARRIAGE)
    assert g.nodes == (1, 2)


def test_crush_is_directed_and_not_deduped_across_directions():
    r = _r()
    g = r.build_relations(marriages=[], crushes={1: 2, 2: 1})
    assert {(e.src, e.dst, e.kind) for e in g.edges} == {
        (1, 2, r.KIND_CRUSH),
        (2, 1, r.KIND_CRUSH),
    }


def test_marriage_wins_over_crush_on_the_same_pair():
    """同一对人既是夫妻又互相倾心时只画夫妻（避免重边）。"""
    r = _r()
    g = r.build_relations(marriages=[(1, 2, 1.0)], crushes={1: 2, 2: 1})
    assert [e.kind for e in g.edges] == [r.KIND_MARRIAGE]


def test_self_edges_are_dropped():
    r = _r()
    g = r.build_relations(marriages=[(7, 7, 1.0)], crushes={8: 8})
    assert g.edges == ()
    assert g.nodes == ()


def test_max_edges_truncates_but_reports_total():
    r = _r()
    g = r.build_relations(
        marriages=[(1, 2, 1.0), (3, 4, 2.0), (5, 6, 3.0)], crushes={}, max_edges=2
    )
    assert len(g.edges) == 2
    assert g.total_edges == 3


def test_ego_keeps_only_edges_touching_focus():
    r = _r()
    g = r.build_relations(marriages=[(1, 2, 1.0), (4, 5, 2.0)], crushes={3: 1})
    ego = g.ego(1)
    assert {(e.src, e.dst) for e in ego.edges} == {(1, 2), (3, 1)}
    assert ego.nodes == (1, 2, 3)
    assert r.build_relations(marriages=[], crushes={}).ego(1).edges == ()


def test_degree_and_neighbours():
    r = _r()
    g = r.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 1, 1: 4})
    assert g.degree(1) == 3
    assert g.neighbours(1) == (2, 3, 4)


# --------------------------------------------------------------------------
# 「喜欢」（2026-10-14）：抽到即喜欢（无需确认），与倾心/夫妻的去重优先级
# --------------------------------------------------------------------------


def test_like_is_a_directed_edge():
    r = _r()
    g = r.build_relations(marriages=[], crushes={}, likes={1: 2})
    assert [(e.src, e.dst, e.kind) for e in g.edges] == [(1, 2, r.KIND_LIKE)]
    assert g.edges[0].directed is True
    assert g.nodes == (1, 2)


def test_like_edges_are_not_merged_across_directions():
    """A→B 与 B→A 的喜欢是两条边（有向，不去重成一条）。"""
    r = _r()
    g = r.build_relations(marriages=[], crushes={}, likes={1: 2, 2: 1})
    assert {(e.src, e.dst, e.kind) for e in g.edges} == {
        (1, 2, r.KIND_LIKE),
        (2, 1, r.KIND_LIKE),
    }


def test_self_like_is_dropped():
    r = _r()
    g = r.build_relations(marriages=[], crushes={}, likes={8: 8})
    assert g.edges == ()
    assert g.nodes == ()


def test_crush_wins_over_like_in_the_same_direction():
    """同一方向同时有倾心与喜欢 → 只留倾心。"""
    r = _r()
    g = r.build_relations(marriages=[], crushes={1: 2}, likes={1: 2})
    assert [(e.src, e.dst, e.kind) for e in g.edges] == [(1, 2, r.KIND_CRUSH)]


def test_like_survives_opposite_to_a_crush():
    """A→B 的倾心**不吞**B→A 的喜欢：方向不同就是两条边。"""
    r = _r()
    g = r.build_relations(marriages=[], crushes={1: 2}, likes={2: 1})
    assert {(e.src, e.dst, e.kind) for e in g.edges} == {
        (1, 2, r.KIND_CRUSH),
        (2, 1, r.KIND_LIKE),
    }


def test_marriage_wins_over_like_on_the_same_pair():
    """夫妻比喜欢强：同一对人只留夫妻（两个方向的喜欢都被吞）。"""
    r = _r()
    g = r.build_relations(marriages=[(2, 1, 1.0)], crushes={}, likes={1: 2, 2: 1})
    assert [e.kind for e in g.edges] == [r.KIND_MARRIAGE]


def test_edge_order_is_marriage_then_crush_then_like():
    r = _r()
    g = r.build_relations(marriages=[(5, 6, 1.0)], crushes={3: 4}, likes={1: 2})
    assert [(e.src, e.dst, e.kind) for e in g.edges] == [
        (5, 6, r.KIND_MARRIAGE),
        (3, 4, r.KIND_CRUSH),
        (1, 2, r.KIND_LIKE),
    ]


def test_max_edges_counts_likes_in_total():
    r = _r()
    g = r.build_relations(
        marriages=[(1, 2, 1.0)], crushes={3: 4}, likes={5: 6, 7: 8}, max_edges=3
    )
    assert len(g.edges) == 3
    assert g.total_edges == 4


def test_like_edges_count_in_degree_and_ego():
    r = _r()
    g = r.build_relations(marriages=[], crushes={}, likes={1: 2, 3: 4})
    assert g.degree(1) == 1
    assert g.neighbours(1) == (2,)
    assert {e.kind for e in g.ego(1).edges} == {r.KIND_LIKE}
    assert g.ego(1).nodes == (1, 2)


def test_marriage_is_not_directed_crush_and_like_are():
    r = _r()
    g = r.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 4}, likes={5: 6})
    assert [e.directed for e in g.edges] == [False, True, True]


def test_likes_default_to_empty_for_callers_that_do_not_pass_it():
    """未接线（没传 `likes`）的调用点保持原行为，不炸。"""
    r = _r()
    g = r.build_relations(marriages=[(1, 2, 1.0)], crushes={3: 1})
    assert [(e.src, e.dst, e.kind) for e in g.edges] == [
        (1, 2, r.KIND_MARRIAGE),
        (3, 1, r.KIND_CRUSH),
    ]
