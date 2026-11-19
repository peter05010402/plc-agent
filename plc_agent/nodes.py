"""图节点实现。每个节点都是 (state) -> partial_state 的纯函数，
副作用只有 finalize 落盘。节点之间不共享任何全局变量。
"""
import csv
import json
import os
import re
from typing import Any, Dict, List

from . import prompts
from .llm import extract_code, extract_json, get_llm
from .mock import mock_spec, mock_test_plan
from .rag import KnowledgeBase, build_query
from .state import PLCState, note
from .validator import format_issues, validate_scl


# ---------------------------------------------------------------- 工具

def _llm(state: PLCState):
    return get_llm(state.get("backend", "mock"), state.get("model"))


def _kb(state: PLCState) -> KnowledgeBase:
    root = state.get("kb_dir") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")
    return KnowledgeBase(root)


def extract_block_name(code: str) -> str:
    m = re.search(r'^\s*FUNCTION_BLOCK\s+("[^"]+"|[A-Za-z_][A-Za-z0-9_]*)', code, re.M | re.I)
    if not m:
        return "PLC_Code"
    return m.group(1).strip('"')


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff.-]", "_", name).strip("_")
    return cleaned or "PLC_Code"


# ---------------------------------------------------------------- 1. 需求解析

def parse_requirement(state: PLCState) -> Dict[str, Any]:
    requirement = state.get("requirement", "")
    llm = _llm(state)
    spec = None
    try:
        raw = llm.chat(prompts.SPEC_SYSTEM, requirement, role="SPEC_EXTRACT")
        spec = extract_json(raw)
    except Exception as exc:
        return {"log": note(state, "需求解析失败（%s），回退到启发式解析" % exc)}

    if not isinstance(spec, dict) or "io" not in spec:
        spec = mock_spec(requirement)
        msg = "需求解析未返回合法 JSON，回退到启发式解析"
    else:
        msg = "需求解析完成：%s（IO %d 点，安全项 %d 条）" % (
            spec.get("station", "-"), len(spec.get("io", [])), len(spec.get("safety", [])))
    spec.setdefault("assumptions", [])
    return {"spec": spec, "log": note(state, msg)}


# ---------------------------------------------------------------- 2. 规范检索

def retrieve_knowledge(state: PLCState) -> Dict[str, Any]:
    if not state.get("use_rag", True):
        return {"knowledge": [], "log": note(state, "已跳过规范检索（--no-rag）")}
    kb = _kb(state)
    query = build_query(state.get("spec", {}), state.get("requirement", ""))
    hits = kb.search(query, k=3)
    msg = "检索规范库：命中 %d 篇（%s）" % (
        len(hits), ", ".join(h["title"] for h in hits) if hits else "无")
    return {"knowledge": hits, "log": note(state, msg)}


# ---------------------------------------------------------------- 3. 代码生成

def generate(state: PLCState) -> Dict[str, Any]:
    llm = _llm(state)
    spec = state.get("spec", {})
    code = llm.chat(
        prompts.CODE_SYSTEM,
        prompts.code_user_prompt(state.get("requirement", ""), spec,
                                state.get("knowledge", []), state.get("review_note", "")),
        role="CODE_GEN",
        payload={"fb_name": "MotorPumpCtrl", "dirty": bool(state.get("dirty"))},
    )
    code = extract_code(code)
    return {"draft": code,
            "iterations": 0,
            "log": note(state, "生成 SCL 草稿：%d 行（块名 %s）"
                        % (len(code.splitlines()), extract_block_name(code)))}


# ---------------------------------------------------------------- 4. 同行评审

def critique(state: PLCState) -> Dict[str, Any]:
    llm = _llm(state)
    try:
        raw = llm.chat(prompts.CRITIQUE_SYSTEM, "```scl\n%s\n```" % state.get("draft", ""),
                       role="CRITIQUE")
    except Exception as exc:
        return {"review_note": "", "log": note(state, "评审节点跳过（%s）" % exc)}
    raw = (raw or "").strip()
    blocking = raw.upper().startswith("ISSUES")
    return {"review_note": raw,
            "log": note(state, "评审意见：%s" % ("发现待改项" if blocking else "通过"))}


# ---------------------------------------------------------------- 5. 静态校验

def validate(state: PLCState) -> Dict[str, Any]:
    result = validate_scl(state.get("draft", ""))
    review_blocking = (state.get("review_note", "") or "").upper().startswith("ISSUES")
    if review_blocking:
        result["issues"].append({"level": "warning", "code": "W_REVIEW",
                                 "message": "评审意见待处理", "line": None})
    msg = "静态校验：%d error / %d warning → %s" % (
        len(result["errors"]), len(result["warnings"]),
        "通过" if result["passed"] else "需修复")
    return {"validation": result, "log": note(state, msg)}


# ---------------------------------------------------------------- 6. 修复

def repair(state: PLCState) -> Dict[str, Any]:
    llm = _llm(state)
    validation = state.get("validation", {})
    issues_text = format_issues(validation)
    review = state.get("review_note", "")
    if review.upper().startswith("ISSUES"):
        issues_text += "\n[评审]\n" + review
    code = llm.chat(
        prompts.REPAIR_SYSTEM,
        prompts.repair_user_prompt(state.get("draft", ""), issues_text,
                                   state.get("knowledge", []), state.get("spec", {})),
        role="CODE_REPAIR",
        payload={"fb_name": extract_block_name(state.get("draft", ""))},
    )
    code = extract_code(code)
    iterations = int(state.get("iterations", 0)) + 1
    return {"draft": code, "iterations": iterations,
            "review_note": "",
            "log": note(state, "第 %d 轮修复：%d 行 → %d 行"
                        % (iterations, len(state.get("draft", "").splitlines()),
                           len(code.splitlines())))}


# ---------------------------------------------------------------- 7. 收敛产物

def finalize(state: PLCState) -> Dict[str, Any]:
    out_dir = state.get("out_dir") or "out"
    os.makedirs(out_dir, exist_ok=True)
    code = state.get("draft", "")
    spec = state.get("spec", {})
    validation = state.get("validation", {"passed": False, "errors": [], "warnings": []})
    fb_name = extract_block_name(code)
    stem = _safe_filename(fb_name)

    scl_path = os.path.join(out_dir, stem + ".scl")
    with open(scl_path, "w", encoding="utf-8") as f:
        f.write(code if code.endswith("\n") else code + "\n")

    io_path = os.path.join(out_dir, "io_table.csv")
    with open(io_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["地址", "符号名", "数据类型", "说明"])
        for row in spec.get("io", []):
            w.writerow([row.get("addr", ""), row.get("name", ""),
                        row.get("type", ""), row.get("desc", "")])

    report_path = os.path.join(out_dir, "validation_report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(_render_report(state, fb_name))

    plan_path = os.path.join(out_dir, "test_plan.md")
    try:
        plan = _llm(state).chat(prompts.TESTPLAN_SYSTEM,
                                prompts.testplan_user_prompt(spec, code),
                                role="TEST_PLAN", payload={"spec": spec})
        plan = plan.strip()
    except Exception:
        plan = mock_test_plan(spec)
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(plan + "\n")

    summary_path = os.path.join(out_dir, "run_summary.json")
    summary = {
        "block": fb_name,
        "station": spec.get("station", ""),
        "backend": state.get("backend", "mock"),
        "model": getattr(_llm(state), "name", ""),
        "passed": bool(validation.get("passed")),
        "errors": len(validation.get("errors", [])),
        "warnings": len(validation.get("warnings", [])),
        "repairs": int(state.get("iterations", 0)),
        "files": {"scl": scl_path, "io_table": io_path,
                  "report": report_path, "test_plan": plan_path},
        "trace": state.get("log", []),
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    return {"result": summary,
            "log": note(state, "产物已落盘：%s" % out_dir)}


def _render_report(state: PLCState, fb_name: str) -> str:
    validation = state.get("validation", {})
    spec = state.get("spec", {})
    lines = [
        "# 校验报告：%s" % fb_name, "",
        "- 结论：**%s**" % ("通过" if validation.get("passed") else "未通过"),
        "- 后端：%s" % state.get("backend", "mock"),
        "- 修复轮次：%d" % int(state.get("iterations", 0)),
        "- 工位：%s" % spec.get("station", "-"),
        "", "## 错误", "",
    ]
    if validation.get("errors"):
        lines += ["| 级别 | 行 | 规则 | 说明 |", "|---|---|---|---|"]
        for i in validation["errors"]:
            lines.append("| %s | %s | %s | %s |" % (i["level"], i.get("line") or "-",
                                                    i["code"], i["message"]))
    else:
        lines.append("无。")
    lines += ["", "## 警告", ""]
    if validation.get("warnings"):
        lines += ["| 级别 | 行 | 规则 | 说明 |", "|---|---|---|---|"]
        for i in validation["warnings"]:
            lines.append("| %s | %s | %s | %s |" % (i["level"], i.get("line") or "-",
                                                    i["code"], i["message"]))
    else:
        lines.append("无。")
    lines += ["", "## 执行轨迹", ""]
    lines += ["%d. %s" % (idx + 1, msg) for idx, msg in enumerate(state.get("log", []))]
    return "\n".join(lines) + "\n"
