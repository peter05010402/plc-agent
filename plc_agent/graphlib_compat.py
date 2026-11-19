"""没装 langgraph 时的等价状态机（只保留本项目用到的 API 子集）。

保持 build_graph() 的写法不变：
    g = StateGraph(State)
    g.add_node(name, fn)
    g.add_edge(START, name)
    g.add_conditional_edges(src, router, {"a": "node_a", ...})
    app = g.compile(); app.invoke(state, {"recursion_limit": 50})

节点返回值按 dict 合并进全局 state（与 LangGraph 的 reducer 默认行为一致）。
"""
from typing import Any, Callable, Dict, List, Optional, Tuple

START = "__start__"
END = "__end__"


class _Compiled:
    def __init__(self, nodes: Dict[str, Callable], edges: Dict[str, str],
                 cond: Dict[str, Tuple[Callable, Dict[str, str]]]):
        self._nodes = nodes
        self._edges = edges
        self._cond = cond

    def invoke(self, state: Dict[str, Any], config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        limit = int((config or {}).get("recursion_limit", 50))
        current = START
        steps = 0
        while current != END:
            steps += 1
            if steps > limit:
                state.setdefault("log", []).append("已达到 recursion_limit，强制结束")
                break
            if current != START:
                node = self._nodes.get(current)
                if node is None:
                    raise KeyError("未知节点：%s" % current)
                update = node(state) or {}
                if not isinstance(update, dict):
                    raise TypeError("节点 %s 必须返回 dict" % current)
                state.update(update)

            if current in self._cond:
                router, mapping = self._cond[current]
                key = router(state)
                nxt = mapping.get(key)
                if nxt is None:
                    raise KeyError("条件边返回了未注册的分支：%r" % key)
            else:
                nxt = self._edges.get(current)
                if nxt is None:
                    raise KeyError("节点 %s 没有后继边" % current)
            current = nxt
        return state


class StateGraph:
    def __init__(self, schema: Any = None):
        self.schema = schema
        self._nodes: Dict[str, Callable] = {}
        self._edges: Dict[str, str] = {}
        self._cond: Dict[str, Tuple[Callable, Dict[str, str]]] = {}

    def add_node(self, name: str, fn: Callable) -> "StateGraph":
        self._nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self._edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Callable,
                              mapping: Dict[str, str]) -> "StateGraph":
        self._cond[src] = (router, mapping)
        return self

    def compile(self) -> _Compiled:
        return _Compiled(self._nodes, self._edges, self._cond)
