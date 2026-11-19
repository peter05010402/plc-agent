"""离线 Mock 后端：无 API Key 也能把整条图跑通。

它的作用不是"假装有模型"，而是：
- 给 CI / 课堂演示一个确定性、可复现的基线；
- 把 schema（spec JSON、SCL 文本、测试计划）定死，换成真模型时只替换 transport 层。

注意：Mock 只会产出"泵站启停"这一套参考模板，
换别的工艺（例如输送线、阀门组）必须接真模型。
"""
import json
import re
from typing import Any, Dict, List

# ---------------------------------------------------------------- 参考模板

TEMPLATE = """FUNCTION_BLOCK "{FB}"
{ S7_Optimized_Access := 'TRUE' }
VERSION : 0.1
   VAR_INPUT
      StartBtn : Bool;          // 启动按钮（常开，点动）
      StopBtn : Bool;           // 停止按钮（常开）
      EStop : Bool;             // 急停回路（常闭，TRUE = 安全回路正常）
      Overload : Bool;          // 电机过载（常开，TRUE = 过载）
      AutoMode : Bool;          // 自动模式选择
      LevelLow : Bool;          // 液位低，允许启动
   END_VAR
   VAR_OUTPUT
      MotorRun : Bool;          // 电机运行输出（接触器 KM1）
      RunLamp : Bool;           // 运行指示灯
      FaultLamp : Bool;         // 故障指示灯
      FaultCode : Int;          // 故障代码
   END_VAR
   VAR
      statRun : Bool;           // 运行状态（自保持）
      statFault : Bool;         // 故障锁存
      statBlink : Bool;         // 故障灯闪烁位
      tonBlink : TON;           // 闪烁定时器
   END_VAR
   VAR_TEMP
      tmpSafetyOk : Bool;       // 安全条件汇总
   END_VAR

BEGIN
    // ---- 1) 安全条件汇总：急停常闭（1=正常）且未过载 ----
    #tmpSafetyOk := #EStop AND NOT #Overload;

    // ---- 2) 故障锁存：安全条件丢失即锁存，需人工复位 ----
    IF NOT #tmpSafetyOk THEN
        #statFault := TRUE;
        IF NOT #EStop THEN
            #FaultCode := 16#0001;      // 急停触发
        ELSE
            #FaultCode := 16#0002;      // 电机过载
        END_IF;
    END_IF;

    // ---- 3) 复位：停止按钮 + 安全条件已恢复 ----
    IF #StopBtn AND #tmpSafetyOk THEN
        #statFault := FALSE;
        #FaultCode := 0;
    END_IF;

    // ---- 4) 启停逻辑（带自保持，故障时强制停机）----
    IF #tmpSafetyOk AND NOT #statFault THEN
        IF #StopBtn THEN
            #statRun := FALSE;
        END_IF;
        IF #StartBtn AND (#LevelLow OR #AutoMode) THEN
            #statRun := TRUE;
        END_IF;
    ELSE
        #statRun := FALSE;
    END_IF;

    // ---- 5) 输出映射（停机时输出全部回到安全态）----
    #MotorRun := #statRun;
    #RunLamp := #statRun;

    // ---- 6) 故障灯 1 Hz 闪烁：TON 自复位振荡（IN 必须带 NOT Q）----
    #tonBlink(IN := #statFault AND NOT #tonBlink.Q, PT := T#500ms);
    IF #tonBlink.Q THEN
        #statBlink := NOT #statBlink;
    END_IF;

    // ---- 7) 故障灯最终映射：无故障时常灭 ----
    #FaultLamp := #statFault AND #statBlink;
END_FUNCTION_BLOCK
"""

# 故意注入 3 类典型 LLM 错误，用来演示"校验 → 修复"闭环
DIRTY_EDITS = [
    # 1) 删掉一个 END_IF;（结构不闭合）
    ("        END_IF;\n    END_IF;\n\n    // ---- 3)",
     "    END_IF;\n\n    // ---- 3)"),
    # 2) 变量名拼错（未声明写入）
    ("#tmpSafetyOk := #EStop AND NOT #Overload;",
     "#tmpSafetyOk := #EStopp AND NOT #Overload;"),
    # 3) 漏分号 + 地址缺 %
    ("#MotorRun := #statRun;", "#MotorRun := #statRun"),
]


def build_template(fb_name: str = "MotorPumpCtrl") -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "", fb_name) or "MotorPumpCtrl"
    if not re.match(r"[A-Za-z]", name):
        name = "FB_" + name
    return TEMPLATE.replace("{FB}", name)


def make_dirty(code: str) -> str:
    for old, new in DIRTY_EDITS:
        if old in code:
            code = code.replace(old, new, 1)
    return code


# ---------------------------------------------------------------- 需求解析

IO_ROW_RE = re.compile(
    r"^\|\s*([%][IQM][BWD]?\d+(?:\.\d+)?)\s*\|\s*([A-Za-z_][A-Za-z0-9_]*)\s*\|\s*"
    r"([A-Za-z][A-Za-z0-9_]*)\s*\|\s*(.*?)\s*\|")


def parse_io_table(text: str) -> List[Dict[str, str]]:
    rows = []
    for line in text.splitlines():
        m = IO_ROW_RE.match(line.strip())
        if not m:
            continue
        rows.append({"addr": m.group(1), "name": m.group(2),
                     "type": m.group(3).capitalize(), "desc": m.group(4)})
    return rows


def mock_spec(requirement: str) -> Dict[str, Any]:
    io = parse_io_table(requirement)
    title = ""
    for line in requirement.splitlines():
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            break
    seq = []
    for line in requirement.splitlines():
        m = re.match(r"^\s*\d+\.\s+(.*)$", line)
        if m:
            seq.append(m.group(1).strip())
    safety = [s for s in seq if any(k in s for k in
              ("急停", "过载", "安全", "故障", "联锁", "复位"))]
    title = re.sub(r"^(需求|Requirement)\s*[:：]\s*", "", title).strip()
    return {
        "station": title or "未命名工位",
        "io": io,
        "sequence": seq,
        "safety": safety,
        "constraints": {
            "plc": "S7-1500",
            "tia_version": "V18",
            "language": "SCL",
            "optimized_block": True,
            "logic_in_fb": True,
        },
    }


def mock_test_plan(spec: Dict[str, Any]) -> str:
    io = spec.get("io", [])
    inputs = [i for i in io if i["addr"].startswith("%I")]
    outputs = [i for i in io if i["addr"].startswith("%Q")]

    def nm(items, idx):
        return items[idx]["name"] if len(items) > idx else "—"

    return "\n".join([
        "# 测试计划：%s" % spec.get("station", ""),
        "",
        "| 编号 | 用例 | 前置条件 | 操作 | 期望结果 |",
        "|---|---|---|---|---|",
        "| TC-01 | 上电初始态 | 无故障 | 保持所有按钮释放 | 所有输出为 FALSE，FaultCode=0 |",
        "| TC-02 | 手动启动 | %s=TRUE，安全回路正常 | 点动 %s | %s 置 TRUE 并自保持 |"
        % (nm(inputs, 5), nm(inputs, 0), nm(outputs, 0)),
        "| TC-03 | 正常停止 | 运行中 | 按 %s | 输出立即 FALSE |" % nm(inputs, 1),
        "| TC-04 | 急停 | 运行中 | 断开急停回路（%s→FALSE） | 输出立即 FALSE，%s=1，故障锁存 |"
        % (nm(inputs, 2), nm(outputs, 3)),
        "| TC-05 | 过载 | 运行中 | %s→TRUE | 输出 FALSE，%s=2 |" % (nm(inputs, 3), nm(outputs, 3)),
        "| TC-06 | 故障复位 | 故障锁存 | 安全恢复后按 %s | 故障清零，输出保持停止 |" % nm(inputs, 1),
        "| TC-07 | 复位后重启 | 故障已复位 | 按 %s | 输出重新置 TRUE |" % nm(inputs, 0),
        "| TC-08 | 故障灯 | 故障锁存 | 观察 2 s | %s 以约 1 Hz 闪烁 |" % nm(outputs, 2),
        "| TC-09 | 无液位联锁 | AutoMode=FALSE，%s=FALSE | 按 %s | 输出不置 TRUE |" % (nm(inputs, 5), nm(inputs, 0)),
        "| TC-10 | 掉电重启 | 运行中 | 断电再上电 | 输出不自行启动（自保持不跨掉电） |",
    ])
