"""Content-addressed cache for LLM placement responses.

Key = sha256(kernel, dfg sha256, II, prompt sha256).  Re-running the table with
the same DFG, II and prompt replays the stored completion instead of paying for
it again; this is what makes `scripts/eval_llm_placement.py` reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, Optional


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def prompt_sha(messages: Any) -> str:
    blob = json.dumps(messages, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return sha256_bytes(blob)


def cache_key(kernel: str, dfg_sha: str, ii: int, prompt: str) -> str:
    blob = json.dumps({"kernel": kernel, "dfg_sha": dfg_sha, "ii": int(ii),
                       "prompt_sha": prompt}, sort_keys=True).encode("utf-8")
    return sha256_bytes(blob)


class LLMCache:
    def __init__(self, root: str, enabled: bool = True):
        self.root = root
        self.enabled = enabled
        os.makedirs(root, exist_ok=True)

    def path(self, key: str) -> str:
        return os.path.join(self.root, key[:2], key + ".json")

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        path = self.path(key)
        if not os.path.isfile(path):
            return None
        try:
            return json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, key: str, response: str, usage: Optional[Dict[str, Any]] = None,
            extra: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            return
        path = self.path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        record = {"key": key, "response": response, "usage": usage or {}}
        if extra:
            record.update(extra)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
