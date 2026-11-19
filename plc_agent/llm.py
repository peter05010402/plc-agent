"""模型接入层。

一个统一的 chat() 接口，后面挂不同 transport：
- MockLLM：离线确定性后端，用于 CI / 演示；
- OpenAILLM：任意 OpenAI 兼容网关（OpenAI / DeepSeek / 通义 / vLLM / Ollama 的 /v1 端点）。

换模型只改环境变量，不动图。
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import mock as mock_backend


class LLMError(RuntimeError):
    pass


class BaseLLM:
    name = "base"

    def chat(self, system: str, user: str, role: str = "CODE_GEN",
             payload: Optional[Dict[str, Any]] = None) -> str:
        raise NotImplementedError


# ---------------------------------------------------------------- Mock

class MockLLM(BaseLLM):
    name = "mock"

    def chat(self, system: str, user: str, role: str = "CODE_GEN",
             payload: Optional[Dict[str, Any]] = None) -> str:
        payload = payload or {}
        if role == "SPEC_EXTRACT":
            return json.dumps(mock_backend.mock_spec(user), ensure_ascii=False, indent=2)
        if role == "CODE_GEN":
            code = mock_backend.build_template(payload.get("fb_name", "MotorPumpCtrl"))
            return code if not payload.get("dirty") else mock_backend.make_dirty(code)
        if role == "CRITIQUE":
            return ("OK: 安全联锁、故障锁存、输出安全态映射三部分齐全；"
                    "建议现场调试时补充急停回路的双通道诊断。")
        if role == "CODE_REPAIR":
            return mock_backend.build_template(payload.get("fb_name", "MotorPumpCtrl"))
        if role == "TEST_PLAN":
            return mock_backend.mock_test_plan(payload.get("spec") or mock_backend.mock_spec(user))
        return "OK"


# ---------------------------------------------------------------- OpenAI 兼容

class OpenAILLM(BaseLLM):
    def __init__(self, model: Optional[str] = None, base_url: Optional[str] = None,
                 api_key: Optional[str] = None, temperature: float = 0.2,
                 timeout: int = 120):
        self.model = model or os.getenv("PLC_AGENT_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.getenv("PLC_AGENT_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("PLC_AGENT_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        self.temperature = temperature
        self.timeout = timeout
        self.name = "openai:%s" % self.model

    def chat(self, system: str, user: str, role: str = "CODE_GEN",
             payload: Optional[Dict[str, Any]] = None) -> str:
        if not self.api_key:
            raise LLMError(
                "未配置 API Key：请设置 PLC_AGENT_API_KEY（或 OPENAI_API_KEY），"
                "或改用 --backend mock 离线运行。")
        import httpx

        body = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": "ROLE: %s\n%s" % (role, system)},
                {"role": "user", "content": user},
            ],
        }
        last_err = None
        for attempt in range(3):
            try:
                with httpx.Client(timeout=self.timeout) as cli:
                    resp = cli.post(self.base_url + "/chat/completions",
                                    headers={"Authorization": "Bearer " + self.api_key,
                                             "Content-Type": "application/json"},
                                    json=body)
                if resp.status_code >= 400:
                    raise LLMError("HTTP %s: %s" % (resp.status_code, resp.text[:300]))
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:  # 网络抖动重试，鉴权/参数错误直接抛
                last_err = exc
                if isinstance(exc, LLMError) and "HTTP 4" in str(exc):
                    break
                time.sleep(1.5 * (attempt + 1))
        raise LLMError("模型调用失败：%s" % last_err)


def get_llm(backend: str = "mock", model: Optional[str] = None,
            base_url: Optional[str] = None, api_key: Optional[str] = None) -> BaseLLM:
    if backend in ("mock", "offline", "stub"):
        return MockLLM()
    return OpenAILLM(model=model, base_url=base_url, api_key=api_key)


# ---------------------------------------------------------------- 工具函数

FENCE_RE = re.compile(r"```(?:scl|iec|pascal|)\s*\n(.*?)```", re.S | re.I)
BLOCK_START_RE = re.compile(r"^\s*(FUNCTION_BLOCK|FUNCTION|ORGANIZATION_BLOCK|DATA_BLOCK)\b",
                            re.M | re.I)


def extract_code(text: str) -> str:
    """从模型回复里剥出代码：优先取 ```scl 围栏，其次从块声明开始截。"""
    if not text:
        return ""
    m = FENCE_RE.search(text)
    if m:
        return m.group(1).strip() + "\n"
    m = BLOCK_START_RE.search(text)
    if m:
        return text[m.start():].strip() + "\n"
    return text.strip() + "\n"


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """容错解析 JSON：容忍前后废话、```json 围栏。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return None
