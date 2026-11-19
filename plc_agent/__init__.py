"""PLC 代码生成 Agent（LangGraph 编排 + 静态校验 + 自修复闭环）。"""

from .graph import LANGGRAPH_AVAILABLE, build_graph, run_agent  # noqa: F401
from .validator import validate_scl  # noqa: F401

__version__ = "0.1.0"
