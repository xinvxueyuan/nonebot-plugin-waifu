"""关系模型纯函数：夫妻去重、倾心方向、同对只画夫妻、截断、ego 视图。"""

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
