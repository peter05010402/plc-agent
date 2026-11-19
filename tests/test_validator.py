"""静态校验器与整图链路的测试（pytest）。

跑法：cd plc_agent && python -m pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plc_agent.graph import run_agent  # noqa: E402
from plc_agent.mock import build_template, make_dirty  # noqa: E402
from plc_agent.validator import validate_scl  # noqa: E402

REQ = """# 需求：泵站电机控制

| 地址 | 符号名 | 数据类型 | 说明 |
|---|---|---|---|
| %I0.0 | StartBtn | Bool | 启动 |
| %I0.1 | StopBtn | Bool | 停止 |
| %I0.2 | EStop | Bool | 急停常闭 |
| %I0.3 | Overload | Bool | 过载 |
| %Q0.0 | MotorRun | Bool | 运行输出 |

1. 急停或过载时立即停机并锁存故障。
2. 故障复位需要人工操作。
"""


def _codes(result):
    return {i["code"] for i in result["issues"]}


def test_reference_template_passes():
    result = validate_scl(build_template())
    assert result["passed"], result["errors"]
    assert result["warnings"] == [], result["warnings"]


def test_missing_end_if_detected():
    code = build_template().replace("        END_IF;\n", "", 1)
    result = validate_scl(code)
    assert not result["passed"]
    assert "E_STRUCT_UNBALANCED" in _codes(result)


def test_undeclared_assignment_detected():
    code = build_template().replace("#tmpSafetyOk := #EStop", "#tmpSafetyOkX := #EStop")
    result = validate_scl(code)
    assert "E_UNDECLARED_WRITE" in _codes(result)


def test_missing_semicolon_detected():
    code = build_template().replace("#MotorRun := #statRun;", "#MotorRun := #statRun")
    result = validate_scl(code)
    assert "E_SEMICOLON" in _codes(result)


def test_io_address_without_percent_detected():
    # 注释里的地址不算错误（会随代码一起被剥离），只检查真实语句
    code = build_template().replace("#MotorRun := #statRun;", "#MotorRun := I0.7;")
    result = validate_scl(code)
    assert "E_IO_PREFIX" in _codes(result)


def test_write_to_input_rejected():
    code = build_template().replace("#statRun := FALSE;", "#StopBtn := FALSE;", 1)
    result = validate_scl(code)
    assert "E_WRITE_INPUT" in _codes(result)


def test_blink_timer_without_self_reset_warns():
    code = build_template().replace(
        "IN := #statFault AND NOT #tonBlink.Q", "IN := #statFault")
    result = validate_scl(code)
    assert "W_TIMER_PULSE" in {i["code"] for i in result["warnings"]}


def test_empty_code_fails():
    assert validate_scl("")["passed"] is False


def test_mock_graph_end_to_end(tmp_path):
    state = run_agent(REQ, backend="mock", out_dir=str(tmp_path / "out"), verbose=False)
    assert state["result"]["passed"] is True
    assert state["result"]["repairs"] == 0
    assert os.path.exists(state["result"]["files"]["scl"])
    assert os.path.exists(state["result"]["files"]["test_plan"])


def test_dirty_draft_is_repaired(tmp_path):
    state = run_agent(REQ, backend="mock", out_dir=str(tmp_path / "out2"),
                      dirty=True, verbose=False)
    assert state["iterations"] >= 1, "应触发至少一轮修复"
    assert state["result"]["passed"] is True
    assert state["result"]["errors"] == 0


def test_max_repairs_budget_is_respected(tmp_path):
    """修复预算为 0 时，脏草稿应如实输出"未通过"，而不是死循环。"""
    state = run_agent(REQ, backend="mock", out_dir=str(tmp_path / "out3"),
                      dirty=True, max_repairs=0, verbose=False)
    assert state["iterations"] == 0
    assert state["result"]["passed"] is False
