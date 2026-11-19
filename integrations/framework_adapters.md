# 换框架：CrewAI / AutoGen / 原生 LangGraph 的同一套角色

图的本质只有 5 个零件，换框架就是换「谁来调度」：

| 零件 | 性质 | 说明 |
|---|---|---|
| Planner | LLM | 需求 → 结构化规格（JSON schema 强约束） |
| Retriever | 函数 | BM25 / 向量库，注入工程规范 |
| Coder | LLM | 生成 SCL，只输出一个 FUNCTION_BLOCK |
| Reviewer | LLM | 只评审不改写，输出 `OK:` / `ISSUES:` |
| Repairer | LLM | 按校验报告做最小修复 |
| **Verifier** | **普通函数** | `validate_scl()`，绝不交给 agent 自由发挥 |

> 三条不能破的原则：校验器是确定性函数；状态显式传递（spec/draft/issues）；修复预算在代码里，不在 prompt 里。

---

## 1. LangGraph（本仓库默认实现）

```python
from plc_agent.graph import build_graph

app = build_graph()
state = {"requirement": req, "backend": "openai", "max_repairs": 3, "out_dir": "out"}
final = app.invoke(state, {"recursion_limit": 30})
```

优点：显式 State、条件边、可加 checkpointer（断点续跑 / 人工审核中断）：

```python
from langgraph.checkpoint.memory import MemorySaver

# 把 build_graph() 结尾改成 g.compile(checkpointer=MemorySaver())，
# 调用时传 config={"configurable": {"thread_id": "req-001"}} 即可断点续跑；
# 再在 validate 之前插入 interrupt()，就能实现"人工审核通过后才落盘"。
```

---

## 2. CrewAI

```python
from crewai import Agent, Task, Crew, Process

planner = Agent(role="PLC 工艺工程师", goal="把需求转成 IO 表与动作时序 JSON",
                backstory="20 年 S7-1500 现场调试经验", llm=LLM(model="gpt-4o-mini"))
coder   = Agent(role="SCL 程序员", goal="按规格书生成单个 FUNCTION_BLOCK", llm=LLM(model="gpt-4o-mini"))
reviewer= Agent(role="代码评审员", goal="只挑安全问题，不重写代码", llm=LLM(model="gpt-4o-mini"))

crew = Crew(
    agents=[planner, coder, reviewer],
    tasks=[
        Task(description="解析需求并输出 JSON 规格", agent=planner, expected_output="JSON"),
        Task(description="按规格与规范生成 SCL", agent=coder, expected_output="```scl 代码```"),
        Task(description="评审代码并输出 OK: 或 ISSUES:", agent=reviewer, expected_output="评审结论"),
    ],
    process=Process.sequential,
)

# —— 关键：校验与修复不要塞进 Crew，用普通 Python 循环 ——
from plc_agent.validator import validate_scl, format_issues
from plc_agent.llm import extract_code

code = extract_code(crew.kickoff().raw)
for i in range(3):
    result = validate_scl(code)
    if result["passed"]:
        break
    # 用一个单任务 Crew（或直接 llm.chat）做定点修复
    code = extract_code(repair_task_execute(code, format_issues(result)))
```

---

## 3. AutoGen（GroupChat）

```python
from autogen import AssistantAgent, UserProxyAgent, GroupChat, GroupChatManager

verifier = UserProxyAgent(
    name="Verifier",
    human_input_mode="NEVER",
    code_execution_config=False,
    # 让"校验"成为可执行工具，而不是让模型自己看着办
    function_map={"validate_scl": lambda code: validate_scl(code)},
)

group = GroupChat(agents=[planner, coder, reviewer, verifier], messages=[],
                  max_round=10, speaker_selection_method="auto")
manager = GroupChatManager(groupchat=group)

# 在 agent 间传 state 时带上 spec 原文，避免长对话里丢需求
```

注意：AutoGen 的对话历史会膨胀，第 3 轮修复时模型常常忘记原始需求。
解法是把 `spec` 与 `issues` 以结构化字段塞进每条消息，而不是依赖历史。

---

## 4. 其他框架（Dify / Coze / 自研编排）

把 4 个 LLM 角色做成 4 个节点，`validate_scl` 做成"代码执行节点"，
条件分支写 `errors == 0 ? 结束 : 修复`，回边指向修复节点，并设最大循环次数。
本仓库的 `validator.py`、`prompts.py`、`knowledge/` 可以直接搬走。
