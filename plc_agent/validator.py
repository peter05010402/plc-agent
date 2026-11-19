"""SCL 静态校验器（不依赖编译器，可在 CI / 离线环境跑）。

设计原则：
1. 只有"确定是语法/工程硬错误"的才报 error，可疑的只报 warning，
   避免 LLM 生成代码时被误报淹没（误报比漏报更毁闭环）。
2. 每条 issue 带 line 行号，方便回喂给 LLM 做定向修复。
3. 纯函数、无外部依赖，可以被 pytest 直接覆盖。
"""
import re
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------- 词表

KEYWORDS: Set[str] = {
    "if", "then", "else", "elsif", "end_if", "case", "of", "end_case",
    "for", "to", "by", "do", "end_for", "while", "end_while", "repeat",
    "until", "end_repeat", "return", "exit", "continue", "goto", "begin",
    "var", "end_var", "var_input", "var_output", "var_in_out", "var_temp",
    "var_stat", "end_function_block", "end_function", "end_organization_block",
    "function_block", "function", "organization_block", "type", "end_type",
    "data_block", "end_data_block", "struct", "end_struct", "version",
    "region", "end_region", "and", "or", "xor", "not", "mod", "true",
    "false", "at", "retain", "non_retain", "constant", "array", "string",
    "external", "alias", "protected", "private", "public",
}

TYPES: Set[str] = {
    "bool", "byte", "word", "dword", "int", "dint", "sint", "uint", "udint",
    "usint", "lint", "ulint", "real", "lreal", "time", "s5time", "date",
    "tod", "dt", "s7_time", "char", "wchar", "string", "wstring", "void",
    "ton", "tof", "tp", "tonr", "ctu", "ctd", "ctud", "r_trig", "f_trig",
    "pointer", "any", "variant", "struct",
}

STD_FUNCS: Set[str] = {
    "move", "add", "sub", "mul", "div", "abs", "min", "max", "limit", "sel",
    "mux", "round", "trunc", "ceil", "floor", "sqrt", "exp", "ln", "log",
    "shl", "shr", "rol", "ror", "eq", "ne", "ge", "le", "gt", "lt",
    "concat", "len", "left", "right", "mid", "delete", "insert", "replace",
    "find", "copy", "blkmov", "fill", "rd_sys", "wr_sys", "get", "set",
}

# IEC 定时器 / 计数器引脚名：不是标签，做赋值目标分析时需排除
PARAM_NAMES: Set[str] = {
    "in", "pt", "q", "et", "cu", "cd", "r", "ld", "pv", "cv", "qu", "qd",
    "reset", "load", "tv", "ta", "m", "s", "s1", "s2", "s3", "en", "eno",
}

STRUCT_PAIRS = [
    ("IF", "END_IF"),
    ("CASE", "END_CASE"),
    ("FOR", "END_FOR"),
    ("WHILE", "END_WHILE"),
    ("REPEAT", "END_REPEAT"),
]
REQUIRED_SEMICOLON_END = {"END_IF", "END_CASE", "END_FOR", "END_WHILE", "END_REPEAT"}

NO_SEMICOLON_RE = [
    r"^\s*\{",                                   # 属性块 { S7_Optimized_Access := 'TRUE' }
    r"^\s*(VAR|VAR_INPUT|VAR_OUTPUT|VAR_IN_OUT|VAR_TEMP|VAR_STAT|END_VAR)\b",
    r"^\s*END_(FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|TYPE|DATA_BLOCK|STRUCT|REGION)\b",
    r"^\s*(FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|TYPE|DATA_BLOCK)\b",
    r"^\s*(BEGIN|REGION|END_REGION|VERSION|ELSE|REPEAT|CONSTANT)\b",
    r"^\s*((IF|ELSIF)\b.*\bTHEN|CASE\b.*\bOF|FOR\b.*\bDO|WHILE\b.*\bDO|UNTIL\b.*)\s*$",
    r"^\s*\w+\s*:\s*$",                          # CASE 分支标签
]

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DECL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff][^\s:]*)\s*:\s*(.*)$")
ASSIGN_TARGET_RE = re.compile(r'(#|")?([A-Za-z_][A-Za-z0-9_]*)"?\s*:=')
IO_NO_PREFIX_RE = re.compile(r"(?<!%)(?<![\w%])(?:[IQM][BWD]?\d+(?:\.\d+)?)\b")
IO_ADDR_RE = re.compile(r"%[IQM][BWD]?\d+(?:\.\d+)?")
EST_PAT = re.compile(r"(e_?stop|estop|emergency|急停)", re.I)


def _issue(level: str, code: str, message: str, line: Optional[int] = None) -> Dict[str, Any]:
    return {"level": level, "code": code, "message": message, "line": line, "column": None}


# ---------------------------------------------------------------- 预处理

def strip_comments(text: str) -> str:
    """去掉 (* *) 块注释与 // 行注释，保留字符串字面量，且保持行数不变。"""
    text = re.sub(r"\(\*.*?\*\)", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    out = []
    for line in text.splitlines():
        # 只处理引号外的 //
        in_str = False
        for i, ch in enumerate(line):
            if ch == "'":
                in_str = not in_str
            elif not in_str and ch == "/" and line[i:i + 2] == "//":
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _declared_vars(code: str) -> Dict[str, Dict[str, Any]]:
    """解析 VAR 分区，返回 {变量名: {section, line, type}}。"""
    res: Dict[str, Dict[str, Any]] = {}
    section = None
    for lineno, line in enumerate(code.splitlines(), 1):
        head = line.strip().lower()
        m = re.match(r"(var_input|var_output|var_in_out|var_temp|var_stat|var)(?![a-z_])", head)
        if m:
            section = m.group(1)
            continue
        if head.startswith("end_var"):
            section = None
            continue
        if section is None:
            continue
        dm = DECL_RE.match(line)
        if dm and not dm.group(1).startswith("//"):
            res[dm.group(1)] = {"section": section, "line": lineno, "type": dm.group(2).strip()}
    return res


def _body(code: str) -> tuple:
    """返回 BEGIN 之后的正文以及正文起始行号（1 基）。"""
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^\s*BEGIN\s*$", line, re.I):
            return "\n".join(lines[i + 1:]), i + 2
    return code, 1


# ---------------------------------------------------------------- 规则

def _check_block(code: str) -> List[Dict[str, Any]]:
    issues = []
    headers = {
        "FUNCTION_BLOCK": "END_FUNCTION_BLOCK",
        "FUNCTION": "END_FUNCTION",
        "ORGANIZATION_BLOCK": "END_ORGANIZATION_BLOCK",
        "DATA_BLOCK": "END_DATA_BLOCK",
    }
    found = None
    for h, tail in headers.items():
        if re.search(r"^\s*%s\b" % h, code, re.M | re.I):
            found = (h, tail)
            break
    if not found:
        issues.append(_issue("error", "E_NO_BLOCK",
                             "缺少块声明（应为 FUNCTION_BLOCK / FUNCTION / ORGANIZATION_BLOCK）"))
        return issues
    h, tail = found
    cnt_h = len(re.findall(r"^\s*%s\b" % h, code, re.M | re.I))
    cnt_t = len(re.findall(r"^\s*%s\b" % tail, code, re.M | re.I))
    if cnt_t < cnt_h:
        issues.append(_issue("error", "E_BLOCK_UNCLOSED",
                             "%s 缺少对应的 %s" % (h, tail)))
    if cnt_t > cnt_h:
        issues.append(_issue("warning", "W_BLOCK_EXTRA",
                             "出现多余的 %s" % tail))
    # 块名
    m = re.search(r'^\s*%s\s+("[^"]*"|[A-Za-z_][A-Za-z0-9_]*)' % h, code, re.M | re.I)
    if not m:
        issues.append(_issue("error", "E_BLOCK_NAME", "%s 未声明块名" % h))
    return issues


def _check_var_blocks(code: str) -> List[Dict[str, Any]]:
    issues = []
    depth = 0
    open_line = None
    for lineno, line in enumerate(code.splitlines(), 1):
        if re.match(r"^\s*VAR(_INPUT|_OUTPUT|_IN_OUT|_TEMP|_STAT)?\b", line, re.I):
            depth += 1
            open_line = lineno
        if re.match(r"^\s*END_VAR\b", line, re.I):
            depth -= 1
            if depth < 0:
                issues.append(_issue("error", "E_VAR_EXTRA", "多余的 END_VAR", lineno))
                depth = 0
    if depth > 0:
        issues.append(_issue("error", "E_VAR_UNCLOSED",
                             "VAR 分区未闭合（缺 END_VAR）", open_line))
    return issues


def _check_structs(code: str) -> List[Dict[str, Any]]:
    issues = []
    for head, tail in STRUCT_PAIRS:
        h = len(re.findall(r"\b%s\b" % head, code, re.I))
        t = len(re.findall(r"\b%s\b" % tail, code, re.I))
        # ELSIF 里包含 IF 字样但 \bIF\b 不会匹配 ELSIF，这里无需再处理
        if h != t:
            issues.append(_issue(
                "error", "E_STRUCT_UNBALANCED",
                "%s / %s 数量不匹配（%d vs %d），检查分支是否漏闭合" % (head, tail, h, t)))
    return issues


def _check_semicolon(code: str) -> List[Dict[str, Any]]:
    issues = []
    lines = code.splitlines()
    in_var = False
    for lineno, line in enumerate(lines, 1):
        raw = line.strip()
        if not raw or raw.startswith("//"):
            continue
        low = raw.lower()
        if re.match(r"var(_input|_output|_in_out|_temp|_stat)?\b", low):
            in_var = True
            continue
        if low.startswith("end_var"):
            in_var = False
            continue
        if raw.endswith(";"):
            continue
        if any(re.match(p, raw, re.I) for p in NO_SEMICOLON_RE):
            continue
        if in_var:
            # VAR 区里漏分号同样致命
            issues.append(_issue("error", "E_SEMICOLON",
                                 "声明语句缺少分号：%s" % raw[:60], lineno))
            continue
        issues.append(_issue("error", "E_SEMICOLON",
                             "语句缺少分号：%s" % raw[:60], lineno))
    return issues


def _check_symbols(code: str, body_text: str, body_start: int) -> List[Dict[str, Any]]:
    issues = []
    declared = _declared_vars(code)
    if not declared:
        return issues

    # 重复声明
    seen = {}
    for lineno, line in enumerate(code.splitlines(), 1):
        section = None
        # 重新扫一遍拿重复行号
        dm = DECL_RE.match(line)
        if dm and dm.group(1) in declared and declared[dm.group(1)]["line"] == lineno:
            name = dm.group(1)
            if name in seen:
                issues.append(_issue("error", "E_DUP_DECL",
                                     "变量重复声明：%s" % name, lineno))
            seen[name] = lineno

    body = strip_comments(body_text)
    # 去掉时间/进制字面量（T#500ms、16#0001），否则 T、ms 会被当成未声明标识符
    body = re.sub(r"\b(?:S5T|LTIME|LT|TIME|TOD|DATE|DT|T|D|B|2|8|16)#[0-9A-Za-z_.]*", " ", body)

    # 赋值目标：必须已声明，且不能是 INPUT
    for m in ASSIGN_TARGET_RE.finditer(body):
        name = m.group(2)
        if name.lower() in PARAM_NAMES or name.lower() in KEYWORDS:
            continue
        line_no = body_start + body[:m.start()].count("\n")
        if name not in declared:
            issues.append(_issue("error", "E_UNDECLARED_WRITE",
                                 "向未声明的变量赋值：%s" % name, line_no))
        elif declared[name]["section"] == "var_input":
            issues.append(_issue("error", "E_WRITE_INPUT",
                                 "不允许直接给输入变量赋值（用中间变量）：%s" % name, line_no))
        elif declared[name]["section"] in ("var_temp",):
            pass  # 允许写 TEMP

    # 已声明的输出是否被赋值
    assigned = {m.group(2) for m in ASSIGN_TARGET_RE.finditer(body)}
    for name, meta in declared.items():
        if meta["section"] == "var_output" and name not in assigned:
            issues.append(_issue("warning", "W_OUTPUT_UNASSIGNED",
                                 "输出变量从未被赋值：%s" % name, meta["line"]))

    # 未声明的读取（多为拼写错误；标准功能/类型/关键字除外）
    for m in IDENT_RE.finditer(body):
        name = m.group(0)
        low = name.lower()
        if (low in KEYWORDS or low in TYPES or low in STD_FUNCS
                or low in PARAM_NAMES or name in declared):
            continue
        # 块名、CASE 标签、属性等已在预处理里弱化，这里只报一次
        line_no = body_start + body[:m.start()].count("\n")
        issues.append(_issue("warning", "W_UNDECLARED_READ",
                             "引用了未声明的标识符（检查拼写或补声明）：%s" % name, line_no))
    return issues


def _check_io_address(code: str) -> List[Dict[str, Any]]:
    issues = []
    for lineno, line in enumerate(code.splitlines(), 1):
        low = line.strip().lower()
        if re.match(r"var_(input|output|in_out)", low) or re.match(r"\s*end_var", low):
            continue
        for m in IO_NO_PREFIX_RE.finditer(line):
            # 排除注释里的 %I0.0（已带前缀不会被匹配）
            issues.append(_issue("error", "E_IO_PREFIX",
                                 "绝对地址缺少百分号前缀：%s（应为 %%%s）"
                                 % (m.group(0), m.group(0)), lineno))
        for m in IO_ADDR_RE.finditer(line):
            addr = m.group(0)
            if re.search(r"_$", addr):
                issues.append(_issue("error", "E_IO_ADDR", "非法地址：%s" % addr, lineno))
    return issues


def _check_instances(code: str, body_text: str, body_start: int) -> List[Dict[str, Any]]:
    """定时器/计数器实例必须在静态区（VAR / VAR_STAT）声明。"""
    issues = []
    declared = _declared_vars(code)
    body = strip_comments(body_text)
    for m in re.finditer(r'(#|")([A-Za-z_][A-Za-z0-9_]*)"?\s*\(', body):
        name = m.group(2)
        meta = declared.get(name)
        line_no = body_start + body[:m.start()].count("\n")
        if meta is None:
            issues.append(_issue("warning", "W_INSTANCE_UNDECLARED",
                                 "实例调用但未声明背景数据：%s" % name, line_no))
        elif meta["section"] in ("var_input", "var_output", "var_in_out", "var_temp"):
            issues.append(_issue("error", "E_INSTANCE_TEMP",
                                 "定时器/计数器实例必须声明在 VAR(STAT) 区：%s（当前在 %s）"
                                 % (name, meta["section"]), line_no))
    return issues


def _check_timer_pulse(code: str, body_text: str, body_start: int) -> List[Dict[str, Any]]:
    """闪烁/振荡定时器：IN 恒为真时，Q 会每个扫描周期翻转，得到超高频率闪烁。

    正确写法是自复位：IN := <条件> AND NOT <inst>.Q。
    """
    issues = []
    body = strip_comments(body_text)
    calls = {}
    for m in re.finditer(r"#([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*IN\s*:=\s*(.*?)\s*,\s*PT", body, re.S):
        calls[m.group(1)] = (m.group(2).strip(), m.start())
    for name, (in_expr, pos) in calls.items():
        block = re.search(r"IF\s*#%s\.Q\s+THEN(.*?)END_IF" % re.escape(name), body, re.S | re.I)
        if not block:
            continue
        if not re.search(r":=\s*NOT\s*#", block.group(1)):
            continue
        if ("NOT #%s.Q" % name) in in_expr or ("NOT #%s.Q" % name).replace(" ", "") in in_expr.replace(" ", ""):
            continue
        line_no = body_start + body[:pos].count("\n")
        issues.append(_issue(
            "warning", "W_TIMER_PULSE",
            "闪烁定时器 %s 的 IN 未自复位（应为 IN := <条件> AND NOT #%s.Q），"
            "否则 Q 每扫描周期翻转" % (name, name), line_no))
    return issues


def _check_safety(code: str, body_text: str, body_start: int) -> List[Dict[str, Any]]:
    issues = []
    body = strip_comments(body_text)
    estops = {m.group(0) for m in EST_PAT.finditer(body)}
    if estops:
        if not re.search(r"NOT\s*[#\"]?\s*(?:%s)" % "|".join(re.escape(e) for e in estops), body, re.I):
            issues.append(_issue(
                "warning", "W_ESTOP_LOGIC",
                "急停信号未做取反/常闭处理，请确认安全回路逻辑（急停应使用常闭触点与失效安全原则）",
                None))
    return issues


def _check_cross_block(code: str) -> List[Dict[str, Any]]:
    issues = []
    if re.search(r"^\s*ORGANIZATION_BLOCK\b", code, re.M | re.I) and re.search(r"\bIF\b", code, re.I):
        issues.append(_issue("warning", "W_LOGIC_IN_OB",
                             "OB 中直接写控制逻辑不利于复用/单元测试，建议逻辑封装到 FB"))
    if not re.search(r"^\s*BEGIN\s*$", code, re.M | re.I):
        issues.append(_issue("error", "E_NO_BEGIN", "缺少 BEGIN 关键字"))
    return issues


def validate_scl(code: str) -> Dict[str, Any]:
    """对一段 SCL 代码做静态校验，返回 {passed, errors, warnings, issues, stats}。"""
    if not code or not code.strip():
        return {"passed": False,
                "errors": [{"level": "error", "code": "E_EMPTY", "message": "代码为空",
                            "line": None}],
                "warnings": [], "issues": [], "stats": {}}

    clean = strip_comments(code)
    body_text, body_start = _body(clean)

    issues: List[Dict[str, Any]] = []
    issues += _check_block(clean)
    issues += _check_cross_block(clean)
    issues += _check_var_blocks(clean)
    issues += _check_structs(clean)
    issues += _check_semicolon(clean)
    issues += _check_io_address(clean)
    issues += _check_symbols(clean, body_text, body_start)
    issues += _check_instances(clean, body_text, body_start)
    issues += _check_timer_pulse(clean, body_text, body_start)
    issues += _check_safety(clean, body_text, body_start)

    errors = [i for i in issues if i["level"] == "error"]
    warnings = [i for i in issues if i["level"] == "warning"]
    return {
        "passed": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "issues": issues,
        "stats": {
            "lines": len(code.splitlines()),
            "vars": len(_declared_vars(clean)),
            "errors": len(errors),
            "warnings": len(warnings),
        },
    }


def format_issues(validation: Dict[str, Any], limit: int = 20) -> str:
    """把校验结果压成给 LLM 看的一段文本（喂给修复节点）。"""
    lines = []
    for issue in validation.get("issues", [])[:limit]:
        loc = "L%s" % issue["line"] if issue.get("line") else "全局"
        lines.append("[%s][%s] %s: %s" % (issue["level"].upper(), loc, issue["code"], issue["message"]))
    return "\n".join(lines)
