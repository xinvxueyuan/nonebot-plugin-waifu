"""关系图布局纯函数：焦点居中、一度环绕、坐标确定、不越界。"""

from __future__ import annotations


def _mk(*edges):
    from nonebot_plugin_waifu import relations

    marriages = [
        (a, b, float(i)) for i, (a, b, kind) in enumerate(edges) if kind == "m"
    ]
    crushes = {a: b for a, b, kind in edges if kind == "c"}
    return relations.build_relations(marriages=marriages, crushes=crushes)


def _l():
    from nonebot_plugin_waifu import layout

    return layout


def test_ego_layout_puts_focus_in_center_and_ring_around():
    lay = _l()
    g = _mk((1, 2, "m"), (3, 1, "c"), (4, 1, "c"))
    (cluster,) = lay.layout_graph(g.ego(1), focus=1)
    assert cluster.hub == 1
    by_uid = {n.uid: n for n in cluster.nodes}
    assert set(by_uid) == {1, 2, 3, 4}
    cx, cy = by_uid[1].x + by_uid[1].w / 2, by_uid[1].y + by_uid[1].h / 2
    assert (round(cx), round(cy)) == (cluster.width // 2, cluster.height // 2)
    ring = [n for n in cluster.nodes if n.uid != 1]
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
