"""Shared bounded, cycle-safe graph traversal for trace and correlation (no API/DB calls)."""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.flow import FlowEdgeResponse


@dataclass
class PathSearchResult:
    paths: list[list[FlowEdgeResponse]] = field(default_factory=list)
    explored_steps: int = 0
    truncated: bool = False


class FlowPathSearch:
    def __init__(self, edges: list[FlowEdgeResponse]):
        self.adjacency: dict[str, list[FlowEdgeResponse]] = defaultdict(list)
        self.reverse: dict[str, list[FlowEdgeResponse]] = defaultdict(list)
        for edge in edges:
            self.adjacency[edge.source].append(edge)
            self.reverse[edge.target].append(edge)

    def search(
        self,
        source: str,
        is_target: Callable[[str], bool],
        *,
        max_depth: int,
        max_paths: int,
        max_steps: int,
        continue_after_match: bool = False,
    ) -> PathSearchResult:
        result = PathSearchResult()
        if is_target(source):
            result.paths.append([])
            if not continue_after_match:
                return result
        stack = [(source, iter(self.adjacency.get(source, ())))]
        visited = {source}
        path: list[FlowEdgeResponse] = []
        while stack:
            if result.explored_steps >= max_steps:
                result.truncated = True
                break
            current, iterator = stack[-1]
            edge = next(iterator, None)
            if edge is None:
                stack.pop()
                visited.remove(current)
                if path:
                    path.pop()
                continue
            result.explored_steps += 1
            if edge.target in visited:
                continue
            if len(path) + 1 > max_depth:
                result.truncated = True
                continue
            match = is_target(edge.target)
            if match:
                result.paths.append([*path, edge])
                if len(result.paths) >= max_paths:
                    result.truncated = True
                    break
            if not match or continue_after_match:
                path.append(edge)
                visited.add(edge.target)
                stack.append((edge.target, iter(self.adjacency.get(edge.target, ()))))
        return result
