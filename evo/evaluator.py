"""伐谋评估器：把演化出的程序交给 acc-thu 的真实 DFG 打分。

契约（famou EvaluateModule 强制要求）：
    evaluate(path_user_py) -> dict
返回里必须有 combined_score（float，越大越好）与 validity（0.0/1.0），
其余键会作为自定义 metrics 上报。

注意：famou 会先 os.chdir 到本文件所在目录再调用 evaluate，
所以这里所有路径都用绝对路径或 ACC_THU_ROOT 解析。
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("ACC_THU_ROOT") or os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from evo import harness  # noqa: E402


def evaluate(path_user_py, gpu_ids=None):
    """gpu_ids 是 famou 可能传的关键字参数，这里不用（纯 CPU 的编译/检查）。"""
    try:
        return harness.evaluate_program(path_user_py)
    except Exception as e:                                  # noqa: BLE001
        import traceback
        return {
            "combined_score": 0.0,
            "validity": 0.0,
            "error_info": "%s: %s" % (type(e).__name__, e),
            "traceback": traceback.format_exc()[-1500:],
        }


if __name__ == "__main__":
    # 手动自测：python evaluator.py [program.py]
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_HERE, "init.py")
    out = evaluate(target)
    for k in ("combined_score", "validity", "kernels_legal_at_ii4", "eval_time"):
        if k in out:
            print("%-22s %s" % (k, out[k]))
    for k in ("array_add", "mac", "trmm"):
        if k in out:
            print("%-22s %s" % (k, out[k]))
    if "error_info" in out:
        print(out["error_info"])
