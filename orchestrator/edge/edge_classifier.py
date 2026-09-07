"""方向 I1：端侧推理层 EdgeClassifier——接口 + 规则实现 + 端云路由。

对应 docs/plan/plan.md §3（M1：不碰模型，先定接口）。
RuleBasedEdgeClassifier 复用现有 FastIntent 规则，输出标准化的
EdgeClassification，让「路由决策」与「实现方式」解耦——M2 换 ONNX 模型
只需实现同一 Protocol，调用方零改动。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

import fast_intent

# ── 路由阈值（plan §3.3.4）────────────────────────────
CONF_FAST = 0.85        # ≥0.85 且 ∈ 车控集 → T0 快路径
CONF_ANSWER = 0.70      # ≥0.70 且有直接回答 → 端侧简答

# 车控意图集 = 端侧可执行集（LOCAL_INTENTS）去掉查询类
VEHICLE_CONTROL_INTENTS = {
    n for n in fast_intent.LOCAL_INTENTS if ".query" not in n
}

# 危险动作（require_confirm）：保守策略 → 上云二次确认（plan §3.4）
DANGER_INTENTS = {
    "trunk.open", "door_lock.open",
    "fuel_tank_cover.open", "charging_port.open",
}


class EscalationReason(Enum):
    LOW_CONFIDENCE = "low_confidence"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_CLOUD_DATA = "needs_cloud_data"      # 需要实时数据/多 Agent 协作
    SAFETY_REQUIRES_CLOUD = "safety_requires_cloud"  # 危险动作需云端二次确认


@dataclass
class EdgeClassification:
    intent: str
    slots: dict[str, str]
    confidence: float
    should_escalate: bool
    escalation_reason: EscalationReason | None = None
    direct_answer: str | None = None
    raw_text: str = ""
    structured: dict | None = None   # 公版结构化命令（demo 直接喂 VAL 用）


class EdgeClassifier(Protocol):
    async def classify(self, text: str, context: dict | None = None) -> EdgeClassification: ...


def _builtin_answer(text: str) -> str | None:
    """端侧简答小表（demo 内置；M2 模型阶段改由模型直接生成）。"""
    if "现在几点了" in text or ("现在" in text and "时间" in text):
        return datetime.now().strftime("现在是 %H:%M")
    return None


class RuleBasedEdgeClassifier:
    """第一阶段实现：复用 FastIntent 规则，输出标准化 EdgeClassification。"""

    async def classify(self, text: str, context: dict | None = None) -> EdgeClassification:
        t = (text or "").strip()
        if not t:
            return EdgeClassification("", {}, 0.0, True,
                                      EscalationReason.OUT_OF_SCOPE, raw_text=t)

        structured = fast_intent.classify_structured(t)
        legacy = fast_intent.structured_to_legacy(structured) if structured else None
        name = legacy["name"] if legacy else None
        slots = legacy["slots"] if legacy else {}
        conf = float(legacy.get("confidence", 0.9)) if legacy else 0.0

        # ① 端侧可执行：车控 / 危险动作 / 状态查询
        if name and fast_intent.is_local(name):
            if name in DANGER_INTENTS:
                return EdgeClassification(name, slots, conf, True,
                                          EscalationReason.SAFETY_REQUIRES_CLOUD,
                                          raw_text=t, structured=structured)
            if name.endswith(".query"):
                return EdgeClassification(name, slots, conf, False,
                                          direct_answer="当前电量 72%，续航约 380 公里",
                                          raw_text=t, structured=structured)
            return EdgeClassification(name, slots, conf, False,
                                      raw_text=t, structured=structured)  # T0 车控

        # ② 有结构化意图但不在端侧集（navi.plan / info.weather …）→ 上云
        if structured is not None and name is not None:
            return EdgeClassification(name, {}, conf, True,
                                      EscalationReason.NEEDS_CLOUD_DATA,
                                      raw_text=t, structured=structured)

        # ③ 命中云域（reminder 等）→ 上云
        domain = fast_intent.cloud_domain_of(t)
        if domain:
            return EdgeClassification(f"cloud:{domain}", {}, 0.6, True,
                                      EscalationReason.NEEDS_CLOUD_DATA, raw_text=t)

        # ④ 端侧简答（内置小表）
        answer = _builtin_answer(t)
        if answer:
            return EdgeClassification("chat.qa", {}, CONF_ANSWER, False,
                                      direct_answer=answer, raw_text=t)

        # ⑤ 未匹配 → 低置信度上云
        return EdgeClassification("", {}, 0.3, True,
                                  EscalationReason.LOW_CONFIDENCE, raw_text=t)


def route(c: EdgeClassification) -> str:
    """端云路由（plan §3.3.4）：edge_fast / edge_answer / cloud。"""
    if c.should_escalate:
        return "cloud"
    if c.confidence >= CONF_FAST and c.intent in VEHICLE_CONTROL_INTENTS:
        return "edge_fast"
    if c.confidence >= CONF_ANSWER and c.direct_answer:
        return "edge_answer"
    return "cloud"
