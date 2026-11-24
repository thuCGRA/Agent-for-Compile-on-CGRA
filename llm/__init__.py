"""LLM 客户端（OpenAI 兼容）。"""
from .client import LLMClient, LLMError, extract_json      # noqa: F401

__all__ = ["LLMClient", "LLMError", "extract_json"]
