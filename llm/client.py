"""OpenAI 兼容的 chat 客户端。只用 requests，凭据走环境变量。

    CGRA_LLM_BASE_URL   例如 https://v2.cloudmist.cloud（会自动补 /v1/chat/completions）
    CGRA_LLM_API_KEY
    CGRA_LLM_MODEL      例如 deepseek-v4.1-flash
    CGRA_LLM_TIMEOUT_S  默认 180

两条从旧工程换来的经验，写死在实现里：
1. **推理模型会把预算烧在 reasoning_content 上**，正文为空、finish_reason=length。
   空正文是装置故障，不是模型判决——必须重试/报错，绝不能静默返回 ""。
2. 需要时可显式关思考（`thinking: {"type": "disabled"}`），否则单次能写两万字符。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class LLMError(RuntimeError):
    pass


def _load_credentials() -> None:
    """把 ~/.config/cgra_agent/credentials.env 读进 os.environ（不覆盖已有的）。"""
    path = os.path.expanduser("~/.config/cgra_agent/credentials.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


class LLMClient:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: float = 180.0,
                 max_retries: int = 3, no_thinking: bool = False):
        if requests is None:
            raise LLMError("requests 不可用")
        _load_credentials()
        self.base_url = (base_url or os.environ.get("CGRA_LLM_BASE_URL")
                         or "http://127.0.0.1:18431/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("CGRA_LLM_API_KEY") or "sk-mock"
        self.model = model or os.environ.get("CGRA_LLM_MODEL") or "mock"
        self.timeout = float(os.environ.get("CGRA_LLM_TIMEOUT_S") or timeout)
        self.max_retries = max(1, int(max_retries))
        self.no_thinking = no_thinking
        self.records: List[Dict[str, Any]] = []

    @property
    def chat_url(self) -> str:
        b = self.base_url
        if b.endswith("/chat/completions"):
            return b
        if not b.endswith("/v1"):
            b += "/v1"
        return b + "/chat/completions"

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.0,
             max_tokens: int = 2048, tag: str = "") -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.no_thinking:
            payload["thinking"] = {"type": "disabled"}
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer %s" % self.api_key}
        last = ""
        for attempt in range(1, self.max_retries + 1):
            t0 = time.monotonic()
            try:
                resp = requests.post(self.chat_url, json=payload, headers=headers,
                                     timeout=self.timeout)
                secs = round(time.monotonic() - t0, 2)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = "HTTP %s: %s" % (resp.status_code, resp.text[:200])
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break
                if resp.status_code >= 400:
                    raise LLMError("HTTP %s: %s" % (resp.status_code, resp.text[:300]))
                data = resp.json()
                msg = ((data.get("choices") or [{}])[0].get("message") or {})
                text = msg.get("content") or ""
                if isinstance(text, list):
                    text = "".join(p.get("text", "") if isinstance(p, dict) else str(p)
                                   for p in text)
                if not text.strip():
                    fr = (data.get("choices") or [{}])[0].get("finish_reason")
                    last = "空正文，finish_reason=%s，reasoning 长度=%d" % (
                        fr, len(msg.get("reasoning_content") or ""))
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break
                self.records.append({"n": len(self.records) + 1, "tag": tag,
                                     "secs": secs, "attempt": attempt, "ok": True,
                                     "usage": data.get("usage") or {},
                                     "reasoning_len": len(msg.get("reasoning_content") or ""),
                                     "response": text})
                return text
            except LLMError:
                raise
            except Exception as exc:
                last = "%s: %s" % (type(exc).__name__, exc)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt - 1), 4))
                    continue
                break
        self.records.append({"n": len(self.records) + 1, "tag": tag, "ok": False,
                             "error": last})
        raise LLMError("chat 失败（%d 次）：%s" % (self.max_retries, last))


def extract_json(text: str) -> Optional[dict]:
    """从模型输出里抠第一个 JSON 对象。模型常带 ```json 围栏或前后解释。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None
