"""命令行入口。

用法：
    python -m plc_agent.cli --requirement examples/req_pump_station.md --backend mock
    python -m plc_agent.cli -r req.md --backend openai --model deepseek-chat \
        --base-url https://api.deepseek.com/v1 --out out/run1
    python -m plc_agent.cli -r req.md --dirty          # 演示"校验→修复"闭环
"""
import argparse
import json
import os
import sys

from .graph import LANGGRAPH_AVAILABLE, run_agent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="plc-agent",
        description="用 LLM + LangGraph 生成西门子 SCL 控制代码（带静态校验与自修复闭环）")
    p.add_argument("-r", "--requirement", required=True, help="需求文件（.md/.txt）")
    p.add_argument("--backend", default="mock", choices=["mock", "openai"],
                   help="模型后端：mock 离线确定性 / openai 任意 OpenAI 兼容端点")
    p.add_argument("--model", default=None, help="模型名，如 gpt-4o-mini、deepseek-chat")
    p.add_argument("--base-url", default=None, help="OpenAI 兼容 base_url")
    p.add_argument("-o", "--out", default="out", help="产物目录")
    p.add_argument("--max-repairs", type=int, default=3, help="最多修复轮次")
    p.add_argument("--dirty", action="store_true", help="mock 后端故意产出有 bug 的草稿")
    p.add_argument("--no-rag", action="store_true", help="关闭规范检索")
    p.add_argument("--kb-dir", default=None, help="自定义规范库目录")
    p.add_argument("--json", action="store_true", help="以 JSON 打印最终 state")
    p.add_argument("--quiet", action="store_true", help="不打印执行轨迹")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not os.path.exists(args.requirement):
        print("需求文件不存在：%s" % args.requirement, file=sys.stderr)
        return 2
    with open(args.requirement, "r", encoding="utf-8") as f:
        requirement = f.read()

    try:
        state = run_agent(
            requirement,
            backend=args.backend,
            out_dir=args.out,
            max_repairs=args.max_repairs,
            dirty=args.dirty,
            use_rag=not args.no_rag,
            model=args.model,
            kb_dir=args.kb_dir,
            verbose=not args.json,
        )
    except Exception as exc:
        print("执行失败：%s" % exc, file=sys.stderr)
        if args.backend == "openai":
            print("提示：请确认 PLC_AGENT_API_KEY / PLC_AGENT_BASE_URL 已设置，"
                  "或先用 --backend mock 验证流程。", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2))

    result = state.get("result", {})
    if result.get("files"):
        print("\n生成文件：")
        for key, path in result["files"].items():
            print("  - %-10s %s" % (key, os.path.abspath(path)))
    if not args.json:
        print("\n引擎：%s" % ("LangGraph" if LANGGRAPH_AVAILABLE else "内置状态机"))
    return 0 if result.get("passed", False) else 3


if __name__ == "__main__":
    raise SystemExit(main())
