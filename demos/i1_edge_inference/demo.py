"""方向 I1 端云协同推理 demo。

跑法（仓库根目录）：
  .venv/bin/python demos/i1_edge_inference/demo.py
  .venv/bin/python demos/i1_edge_inference/demo.py --model models/edge-intent/qwen2.5-0.5b-int4
  .venv/bin/python demos/i1_edge_inference/demo.py --offline

演示内容：三层路由（T0 车控 / 端侧简答 / 上云）、置信度 + 升级原因、
VAL 真实执行（状态真的变）、延迟统计、断网兜底。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _p in (_REPO, os.path.join(_REPO, "orchestrator", "edge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from edge_classifier import RuleBasedEdgeClassifier, route  # noqa: E402
from val import VAL  # noqa: E402

CASES = [
    ("空调调到22度", "车控 T0（带槽位）"),
    ("打开空调", "车控 T0"),
    ("关掉空调", "车控 T0"),
    ("空调开低一点", "车控 T0（相对调温）"),
    ("打开车窗", "车控 T0"),
    ("把车窗关上一半", "车控 T0（规则丢失程度，见 M2 对比）"),
    ("播放音乐", "车控 T0"),
    ("把音量调大", "车控 T0"),
    ("座椅加热打开", "车控 T0"),
    ("打开座椅通风", "车控 T0"),
    ("打开后备箱", "危险动作 → 上云二次确认"),
    ("导航去合肥南站", "云域（多 Agent）"),
    ("今天天气怎么样", "云域（需外部数据）"),
    ("明天下午三点提醒我开会", "云域（reminder）"),
    ("附近有什么充电站", "低置信度 → 上云"),
    ("现在几点了", "端侧简答"),
    ("帮我写一封邮件", "低置信度 → 上云"),
    ("哈哈哈哈随便说说", "低置信度 → 上云"),
]


async def run_demo(use_model_dir: str | None, offline: bool) -> None:
    if use_model_dir:
        try:
            from onnx_classifier import ONNXEdgeClassifier
            clf = ONNXEdgeClassifier(use_model_dir)
            print(f"[model] 已加载端侧模型：{use_model_dir}")
            print("[model] 预热中（首次推理较慢）...")
            await clf.classify("打开空调")
        except Exception as e:
            print(f"[model] 加载失败（{e}）→ 回退规则实现")
            clf = RuleBasedEdgeClassifier()
    else:
        clf = RuleBasedEdgeClassifier()
        print("[rule] 规则实现 RuleBasedEdgeClassifier（M1，plan §3.3.2）")

    val = VAL()
    print("=" * 72)
    print("I1 端云协同推理 demo · 三层路由（T0 车控 / 端侧简答 / 上云）")
    print("=" * 72)

    stats = {"edge_fast": 0, "edge_answer": 0, "cloud": 0}
    total_ms = 0.0

    for text, _note in CASES:
        t0 = time.perf_counter()
        c = await clf.classify(text)
        ms = (time.perf_counter() - t0) * 1000
        total_ms += ms

        r = route(c)
        stats[r] += 1

        if r == "edge_fast":
            # T0：经 VAL 真实执行（架构约束：车控只经 VAL）
            ok, speech = val.execute(
                c.structured or c.intent,
                {} if c.structured else c.slots,
            )
            result = f"✓ {speech}" if ok else f"✗ {speech}"
        elif r == "edge_answer":
            result = c.direct_answer or "（无回答）"
        else:
            result = f"☁ 上云 [{c.escalation_reason.value}]"

        print(f"{text} | {c.intent or '—':<20} | conf={c.confidence:.2f} "
              f"| {r:<11} | {result}")

    print("=" * 72)
    print(f"路由分布: edge_fast={stats['edge_fast']}  edge_answer={stats['edge_answer']}  "
          f"cloud={stats['cloud']}（共 {len(CASES)} 条）")
    print(f"端侧平均分类耗时: {total_ms / len(CASES):.2f} ms"
          f"（规则实现毫秒级；云端 LLM 首字 800ms+）")
    print("VAL 状态快照: hvac_on=%s hvac_temp=%s window=%s media=%s volume=%s seat_heating=%s"
          % (val.state["hvac_on"], val.state["hvac_temp"], val.state["window"],
             val.state["media"], val.state["volume"], val.state["seat_heating"]))

    if offline:
        print("\n[offline] 模拟断网：cloud 请求全部失败，端侧兜底")
        for text in ("空调调到26度", "播放音乐", "导航去机场", "现在几点了"):
            c = await clf.classify(text)
            r = route(c)
            if r == "edge_fast":
                ok, speech = val.execute(
                    c.structured or c.intent,
                    {} if c.structured else c.slots,
                )
                print(f"  {text} → {r}: {speech}")
            elif r == "edge_answer":
                print(f"  {text} → {r}: {c.direct_answer}")
            else:
                print(f"  {text} → cloud 不可达 → 兜底提示「网络不可用，仅支持本地车控」")


def main() -> None:
    ap = argparse.ArgumentParser(description="I1 端云协同推理 demo")
    ap.add_argument("--model", help="端侧 ONNX 模型目录（M2，可选）")
    ap.add_argument("--offline", action="store_true", help="附加断网场景演示")
    args = ap.parse_args()
    asyncio.run(run_demo(args.model, args.offline))


if __name__ == "__main__":
    main()
