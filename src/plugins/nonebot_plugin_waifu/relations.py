"""群友关系模型（**纯函数层**）：婚姻对 + 倾心 + 喜欢 → 关系边。不碰 IO、不读配置。

- 夫妻：`waifu_marriage` **一对存两行**（双向）→ 这里去重成一条**无向**边。
- 倾心：`waifu_locked` 每人最近一次确认 → 一条**有向**边（src 倾心 dst）。
- 喜欢：`waifu_daily` 每人最近一次抽取 → 一条**有向**边（src 喜欢 dst）。
  抽取即喜欢（用户 2026-10-14 口径），**不需要**确认。
- 同一对人只留一条边，强的胜：**夫妻 > 倾心 > 喜欢**（用户口径：夫妻最强）。
  反向的**分开看**：A→B 的喜欢与 B→A 的倾心是两条不同的边，都保留。
- 节点 = 出现在边里的人；**没关系的成员不进图**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: 边类型
KIND_MARRIAGE: Final[str] = "marriage"
KIND_CRUSH: Final[str] = "crush"
KIND_LIKE: Final[str] = "like"

#: 一张图最多画几条边（超出的截掉并在页脚说明）。
DEFAULT_MAX_EDGES: Final[int] = 20


@dataclass(frozen=True)
class RelationEdge:
    """一条关系边。`kind` 见 `KIND_*`；`crush` / `like` 有向、`marriage` 无向。"""

    src: int
    dst: int
    kind: str

    @property
    def pair(self) -> tuple[int, int]:
        return (min(self.src, self.dst), max(self.src, self.dst))

    @property
    def directed(self) -> bool:
        return self.kind in (KIND_CRUSH, KIND_LIKE)


@dataclass(frozen=True)
class RelationGraph:
    """一次渲染要用的全部关系（已去重 + 已截断）。"""

    edges: tuple[RelationEdge, ...]
    nodes: tuple[int, ...]  # 升序
    total_edges: int  # 截断前的总数

    def degree(self, uid: int) -> int:
        return sum(1 for e in self.edges if uid in (e.src, e.dst))

    def neighbours(self, uid: int) -> tuple[int, ...]:
        """与 uid 直接相连的人（升序、去重）。"""
        got = {
            e.dst if e.src == uid else e.src
            for e in self.edges
            if uid in (e.src, e.dst)
        }
        return tuple(sorted(got))

    def ego(self, focus: int) -> RelationGraph:
        """自我中心网络：只留**与 focus 相连**的边（焦点 + 一度关系）。"""
        keep = tuple(e for e in self.edges if focus in (e.src, e.dst))
        nodes = tuple(sorted({m for e in keep for m in (e.src, e.dst)}))
        return RelationGraph(edges=keep, nodes=nodes, total_edges=len(keep))


def build_relations(
    *,
    marriages: Iterable[tuple[int, int, float]],
    crushes: Mapping[int, int],
    likes: Mapping[int, int],
    max_edges: int = DEFAULT_MAX_EDGES,
) -> RelationGraph:
    """把库里的原始行整理成边集合。

    去重优先级（同一对人只留一条边，强的胜）：**夫妻 > 倾心 > 喜欢**。
    反向**分开看**：A→B 的喜欢与 B→A 的倾心互不影响，都保留。

    Args:
        marriages: `(a, b, since)` 原始行（**含双向两行**），已按 since 升序。
        crushes: `{user_id: partner_id}`（每人最近一次确认）→ 倾心。
        likes: `{user_id: partner_id}`（每人最近一次抽取）→ 喜欢；**必填**
            （该群没有抽取记录时传 `{}`）。同一方向已有倾心时不再画喜欢。
        max_edges: 截断上限；`<= 0` 表示不限。
    """
    seen: set[tuple[int, int]] = set()
    crush_dirs: set[tuple[int, int]] = set()
    edges: list[RelationEdge] = []
    for a, b, _since in marriages:
        x, y = int(a), int(b)
        if x == y:
            continue
        pair = (min(x, y), max(x, y))
        if pair in seen:
            continue
        seen.add(pair)
        edges.append(RelationEdge(pair[0], pair[1], KIND_MARRIAGE))
    for src, dst in sorted(crushes.items()):
        x, y = int(src), int(dst)
        if x == y or (min(x, y), max(x, y)) in seen:
            continue
        crush_dirs.add((x, y))
        edges.append(RelationEdge(x, y, KIND_CRUSH))
    for src, dst in sorted(likes.items()):
        x, y = int(src), int(dst)
        if x == y or (min(x, y), max(x, y)) in seen or (x, y) in crush_dirs:
            continue
        edges.append(RelationEdge(x, y, KIND_LIKE))
    total = len(edges)
    cap = int(max_edges)
    shown = edges if cap <= 0 else edges[:cap]
    nodes = tuple(sorted({m for e in shown for m in (e.src, e.dst)}))
    return RelationGraph(edges=tuple(shown), nodes=nodes, total_edges=total)
