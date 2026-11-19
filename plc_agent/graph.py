"""LangGraph 装配：把 7 个节点连成一张带自修复闭环的图。

            ┌──────────────┐
  START ──▶ │ 需求解析      │
            └──────┬───────┘
                   ▼
            ┌──────────────┐
            │ 规范检索(RAG) │
            └──────┬───────┘
                   ▼
            ┌──────────────┐        ┌──────────┐
            │ 代码生成      │ ─────▶ │ 同行评审  │
            └──────────────┘        └────┬─────┘
                                        ▼
                                 ┌──────────────┐   error    ┌────────┐
                                 │ 静态校验      │ ─────────▶ │ 修复   │
                                 └──────┬───────┘ ◀────────── └────────┘
                                        │ pass / 超出轮次
                                        ▼
                                 ┌──────────────┐
                                 │ 产物落盘      │ ──▶ END
                                 └──────────────┘

没有安装 langgraph 时自动降级到内置的等价状态机（graphlib_compat），
图定义与行为一致，便于在离线/受限环境复现。
"""
import os
from typing import Any, Dict, Optional

from . import nodes
from .state import PLCState

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except Exception:  # pragma: no cover - 环境缺包时的降级路径
    from .graphlib_compat import END, START, StateGraph

    LANGGRAPH_AVAILABLE = False

NODE_NAMES = {
    "parse_requirement": "需求解析",
    "retrieve_knowledge": "规范检索",
    "generate": "代码生成",
    "critique": "同行评审",
    "validate": "静态校验",
    "repair": "定向修复",
    "finalize": "产物落盘",
}


def route_after_validate(state: PLCState) -> str:
    """校验通过且评审无阻塞项 → 落盘；否则在预算内回到修复节点。"""
    validation = state.get("validation", {}) or {}
    review_blocking = (state.get("review_note") or "").upper().startswith("ISSUES")
    passed = bool(validation.get("passed")) and not review_blocking
    if passed:
        return "finalize"
    if int(state.get("iterations", 0)) >= int(state.get("max_repairs", 3)):
        return "finalize"
    return "repair"


def build_graph():
    g = StateGraph(PLCState)
    g.add_node("parse_requirement", nodes.parse_requirement)
    g.add_node("retrieve_knowledge", nodes.retrieve_knowledge)
    g.add_node("generate", nodes.generate)
    g.add_node("critique", nodes.critique)
    g.add_node("validate", nodes.validate)
    g.add_node("repair", nodes.repair)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "parse_requirement")
    g.add_edge("parse_requirement", "retrieve_knowledge")
    g.add_edge("retrieve_knowledge", "generate")
    g.add_edge("generate", "critique")
    g.add_edge("critique", "validate")
    g.add_conditional_edges("validate", route_after_validate,
                            {"repair": "repair", "finalize": "finalize"})
    g.add_edge("repair", "validate")
    g.add_edge("finalize", END)
    return g.compile()


def run_agent(requirement: str,
              backend: str = "mock",
              out_dir: str = "out",
              max_repairs: int = 3,
              dirty: bool = False,
              use_rag: bool = True,
              model: Optional[str] = None,
              kb_dir: Optional[str] = None,
              verbose: bool = True) -> Dict[str, Any]:
    """跑一次完整流程，返回最终 state（含 result 产物清单）。"""
    app = build_graph()
    init: PLCState = {
        "requirement": requirement,
        "backend": backend,
        "model": model or "",
        "dirty": dirty,
        "max_repairs": int(max_repairs),
        "out_dir": out_dir,
        "use_rag": use_rag,
        "iterations": 0,
        "log": [],
    }
    if kb_dir:
        init["kb_dir"] = kb_dir  # type: ignore[typeddict-unknown-key]

    if verbose:
        _print_banner(backend, out_dir)

    final_state = app.invoke(init, {"recursion_limit": 4 * int(max_repairs) + 20})

    if verbose:
        for idx, msg in enumerate(final_state.get("log", []), 1):
            print("  %2d. %s" % (idx, msg))
        result = final_state.get("result", {})
        print("\n  结论：%s（%d error / %d warning，修复 %d 轮）"
              % ("通过" if result.get("passed") else "未通过",
                 result.get("errors", 0), result.get("warnings", 0),
                 result.get("repairs", 0)))
    return final_state


def _print_banner(backend: str, out_dir: str) -> None:
    engine = "LangGraph" if LANGGRAPH_AVAILABLE else "内置状态机（未安装 langgraph）"
    print("=" * 62)
    print("PLC 代码生成 Agent  |  引擎：%s  |  后端：%s" % (engine, backend))
    print("产物目录：%s" % os.path.abspath(out_dir))
    print("=" * 62)
