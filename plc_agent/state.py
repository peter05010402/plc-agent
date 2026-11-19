"""LangGraph 共享状态定义。

所有节点只读写这一个 State，图里不传别的数据，
这样任意一个节点都能被单独替换 / 单独测试。
"""
from typing import Any, Dict, List, TypedDict


class PLCState(TypedDict, total=False):
    # 输入
    requirement: str          # 用户自然语言需求（Markdown / 纯文本）
    backend: str              # mock | openai
    model: str
    dirty: bool               # mock 后端是否故意产出有 bug 的草稿（演示自修复闭环）
    max_repairs: int          # 最多修复轮次
    out_dir: str              # 产物目录
    use_rag: bool

    # 中间态
    spec: Dict[str, Any]      # 结构化工艺规格（IO 表 / 时序 / 安全要求）
    knowledge: List[Dict[str, Any]]  # 检索到的规范片段
    draft: str                # 当前 SCL 草稿
    review_note: str          # 评审意见
    validation: Dict[str, Any]  # 静态校验结果
    iterations: int           # 已修复轮次
    log: List[str]            # 可读执行轨迹

    # 输出
    result: Dict[str, Any]    # 产物路径 + 结论


def note(state: PLCState, msg: str) -> List[str]:
    """返回追加一条日志后的新列表（不可变写法，避免节点间互相污染）。"""
    return list(state.get("log", [])) + [msg]
