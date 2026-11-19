"""极简 RAG：BM25-lite 关键词检索本地工程规范库。

为什么不用向量库：
- 规范文档是"规则条目"，关键词命中比语义相似更可靠；
- 零依赖、零下载，离线编译环境（PLC 现场常见）也能跑。

如果后面接企业知识库，把 KnowledgeBase.search() 换成 embedding + 向量检索即可，
图结构不用动。
"""
import math
import os
import re
from typing import Any, Dict, List

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]{2,4}")


class KnowledgeBase:
    def __init__(self, root: str):
        self.root = root
        self.chunks: List[Dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not os.path.isdir(self.root):
            return
        for name in sorted(os.listdir(self.root)):
            if not name.endswith((".md", ".txt")):
                continue
            path = os.path.join(self.root, name)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            title = text.splitlines()[0].lstrip("# ").strip() if text else name
            self.chunks.append({
                "id": name,
                "title": title,
                "text": text,
                "path": path,
                "tokens": [t.lower() for t in TOKEN_RE.findall(text)],
            })

    # -------------------------------------------------- BM25
    def search(self, query: str, k: int = 3) -> List[Dict[str, Any]]:
        if not self.chunks:
            return []
        q_tokens = [t.lower() for t in TOKEN_RE.findall(query)]
        if not q_tokens:
            return []
        n = len(self.chunks)
        avg_len = sum(len(c["tokens"]) for c in self.chunks) / n
        df = {}
        for t in set(q_tokens):
            df[t] = sum(1 for c in self.chunks if t in c["tokens"])

        scored = []
        for c in self.chunks:
            tf_count = {}
            for tok in c["tokens"]:
                tf_count[tok] = tf_count.get(tok, 0) + 1
            score = 0.0
            for t in set(q_tokens):
                tf = tf_count.get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * (tf * 2.2) / (tf + 1.2 * (0.25 + 0.75 * len(c["tokens"]) / avg_len))
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**c, "score": round(s, 3)} for s, c in scored[:k]]


def build_query(spec: Dict[str, Any], requirement: str) -> str:
    """检索 query：工艺关键词 + 安全要求 + 约束，而不是整段需求（避免噪声）。"""
    parts = [requirement[:400]]
    if spec.get("station"):
        parts.append(str(spec["station"]))
    parts.extend(spec.get("safety", [])[:5])
    if spec.get("constraints", {}).get("language"):
        parts.append(str(spec["constraints"]["language"]))
    parts.append("急停 联锁 锁存 复位 定时器 状态机 IO 地址 命名")
    return " ".join(parts)
