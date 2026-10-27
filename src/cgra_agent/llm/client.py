"""OpenAI-compatible chat client used by the placement agent.

Only ``requests`` is required (already in the cgra env).  The endpoint is
configured by environment variables so the same code talks to the offline mock
(``mock_llm_server.py`` on 127.0.0.1:18431) and to a real API:

    CGRA_LLM_BASE_URL   default http://127.0.0.1:18431/v1
    CGRA_LLM_API_KEY    default "sk-mock"
    CGRA_LLM_MODEL      default "mock"
    CGRA_LLM_TIMEOUT_S  default 120
"""

from __future__ import annotations

import json
import os
import time
from urllib.parse import urlsplit
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover - the cgra env always has it
    requests = None

DEFAULT_BASE_URL = "http://127.0.0.1:18431/v1"
DEFAULT_MODEL = "mock"


class LLMError(RuntimeError):
    """The endpoint could not be reached or returned no usable answer."""


class LLMClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        call_log_path: Optional[str] = None,
    ):
        if requests is None:
            raise LLMError("requests is not installed in this interpreter")
        self.base_url = (base_url or os.environ.get("CGRA_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key or os.environ.get("CGRA_LLM_API_KEY") or "sk-mock"
        self.model = model or os.environ.get("CGRA_LLM_MODEL") or DEFAULT_MODEL
        self.timeout = float(os.environ.get("CGRA_LLM_TIMEOUT_S") or timeout)
        self.max_retries = max(1, int(max_retries))
        self.call_log_path = call_log_path
        self.calls = 0
        self.records: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ http
    @property
    def chat_url(self) -> str:
        base = self.base_url
        if base.endswith("/chat/completions"):
            return base
        # OpenAI-compatible servers normally mount the API under /v1.  When the
        # configured base carries no path at all (e.g. https://api.example.com),
        # assume that default; an explicit path is always left untouched.
        if not urlsplit(base).path.strip("/"):
            base = base + "/v1"
        return base + "/chat/completions"

    def _record(self, rec: Dict[str, Any]) -> None:
        self.calls += 1
        self.records.append(rec)
        if self.call_log_path:
            try:
                with open(self.call_log_path, "a") as f:
                    f.write(json.dumps(rec) + "\n")
            except OSError:
                pass

    def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        seed: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if os.environ.get("CGRA_LLM_NO_THINKING"):
            # The reasoning channel can swallow the whole token budget and return
            # an empty content string (finish_reason=length).  For structured
            # placement output we disable it and record that we did.
            payload["thinking"] = {"type": "disabled"}
        if seed is not None:
            payload["seed"] = int(seed)
        if extra:
            payload.update(extra)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key

        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            started = time.monotonic()
            try:
                resp = requests.post(
                    self.chat_url, headers=headers, json=payload, timeout=self.timeout
                )
                secs = round(time.monotonic() - started, 3)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_error = "HTTP %s: %s" % (resp.status_code, resp.text[:300])
                    if attempt < self.max_retries:
                        time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    break
                if resp.status_code >= 400:
                    last_error = "HTTP %s: %s" % (resp.status_code, resp.text[:300])
                    break
                data = resp.json()
                text = self._content_of(data)
                self._record(
                    {
                        "n": len(self.records) + 1,
                        "model": self.model,
                        "url": self.chat_url,
                        "secs": secs,
                        "attempts": attempt,
                        "ok": True,
                        "usage": data.get("usage") or {},
                        "response": text,
                        "messages": messages,
                    }
                )
                return text
            except Exception as exc:  # requests exceptions, bad json, ...
                secs = round(time.monotonic() - started, 3)
                last_error = "%s: %s" % (type(exc).__name__, exc)
                if attempt < self.max_retries:
                    time.sleep(min(2 ** (attempt - 1), 4))
                    continue
                self._record(
                    {
                        "n": len(self.records) + 1,
                        "model": self.model,
                        "url": self.chat_url,
                        "secs": secs,
                        "attempts": attempt,
                        "ok": False,
                        "error": last_error,
                        "messages": messages,
                    }
                )
        raise LLMError("chat failed after %d attempt(s): %s" % (self.max_retries, last_error))

    @staticmethod
    def _content_of(data: Dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("response has no choices[0].message.content: %s" % str(data)[:300])
        if isinstance(content, list):
            return "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return content or ""
