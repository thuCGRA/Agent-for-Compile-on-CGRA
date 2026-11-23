"""给 vendored famou-v2 打两个小补丁（都可复现，改完要记进文档）。

补丁 1（必须）：evolver.py 在 rollout 失败时无条件调用
    self.current_strategy.on_rollout_failed(result)
但 built-in 的 StaticStrategy（greedy / initial_baseline 等静态策略）没有这个方法，
于是一次普通的 LLM 生成失败会把整个实验打挂（AttributeError）。

补丁 2（必须）：openai_client.py 的推理模型在 thinking 通道里可能写满整个
max_tokens（实测 32768 全花在 reasoning_content 上），正文为空、finish_reason=length。
上层看到空 code 就报 "No code extracted from LLM response"，白等三分钟。
这里加一个一次性的兜底重试：正文为空且 finish_reason=length 时，带
extra_body={"thinking": {"type": "disabled"}} 再请求一次。
"""
import sys

EVOLVER = "/home/sutong/famou-v2/famou/controller/evolver.py"
CLIENT = "/home/sutong/famou-v2/famou/infrastructure/llm/openai_client.py"

EV_OLD = """                    if self.current_strategy is not None:
                        self.current_strategy.on_rollout_failed(result)"""
EV_NEW = """                    if self.current_strategy is not None:
                        # StaticStrategy (used by the built-in static rollouts) does not
                        # implement this optional transaction hook; a transient LLM failure
                        # must not abort the whole run.
                        hook = getattr(self.current_strategy, "on_rollout_failed", None)
                        if callable(hook):
                            hook(result)"""

ST_OLD = """        text = "".join(content_parts)
        thinking = "".join(thinking_parts)
        if not text and not thinking:"""
ST_NEW = """        text = "".join(content_parts)
        thinking = "".join(thinking_parts)
        if not text and thinking and finish_reason == "length" and "extra_body" not in params:
            # The reasoning channel ate the whole token budget: the answer is in
            # `thinking`, the visible content is empty. Ask again with reasoning
            # switched off instead of failing the rollout.
            retry_params = dict(params)
            retry_params["extra_body"] = {"thinking": {"type": "disabled"}}
            return self._create_streaming(**retry_params)
        if not text and not thinking:"""


def patch(path, old, new, label):
    src = open(path, encoding="utf-8").read()
    if new in src:
        print("%-10s already patched" % label)
        return True
    if old not in src:
        print("%-10s ANCHOR NOT FOUND" % label)
        return False
    open(path, "w", encoding="utf-8").write(src.replace(old, new, 1))
    print("%-10s patched" % label)
    return True


ok = patch(EVOLVER, EV_OLD, EV_NEW, "evolver")
ok = patch(CLIENT, ST_OLD, ST_NEW, "client") and ok
sys.exit(0 if ok else 1)
