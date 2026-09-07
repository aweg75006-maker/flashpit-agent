"""方向 I1 M2：ONNX 端侧小模型分类器（onnxruntime-genai + Qwen2.5-0.5B INT4）。

加载失败 / 推理超时 / 输出解析失败 → 自动降级 RuleBasedEdgeClassifier
（plan §3.3.3 超时保护：超时自动降级 + 上云）。
"""
from __future__ import annotations

import json
import re
import time

from edge_classifier import (
    CONF_ANSWER, CONF_FAST, EdgeClassification, EscalationReason,
    RuleBasedEdgeClassifier, VEHICLE_CONTROL_INTENTS,
)

_SYSTEM_PROMPT = (
    "你是车载座舱意图分类器。只输出 JSON，不要任何多余文字：\n"
    '{"intent": "<归一化意图名>", "slots": {"<槽位>": "<值>"}, '
    '"confidence": <0.0-1.0>, "should_escalate": <true|false>, '
    '"escalation_reason": "<low_confidence|out_of_scope|needs_cloud_data|safety_requires_cloud|null>", '
    '"direct_answer": "<端侧直接回答文本或null>"}\n'
    "意图名取值：hvac.set_temperature / hvac.on / hvac.off / hvac.inc / hvac.dec / "
    "window.open / window.close / media.play / media.pause / volume.inc / volume.dec / "
    "seat.heating.on / seat.ventilation.on / navi.plan / info.weather / chat.qa\n"
)

_JSON_RE = re.compile(r"\{.*\}", re.S)

# 模型意图名 → VAL legacy 名
_INTENT_MAP = {
    "hvac.set_temperature": "hvac.set", "hvac.on": "hvac.on", "hvac.off": "hvac.off",
    "hvac.inc": "hvac.inc", "hvac.dec": "hvac.dec",
    "window.open": "window.open", "window.close": "window.close",
    "media.play": "media.play", "media.pause": "media.pause",
    "volume.inc": "volume.inc", "volume.dec": "volume.dec",
    "seat.heating.on": "seat.heating.on", "seat.ventilation.on": "seat.ventilation.on",
    "navi.plan": "navi.plan", "info.weather": "info.weather", "chat.qa": "chat.qa",
}

# VAL legacy 名 → 结构化命令构造器（让 VAL 能真实执行）
_STRUCT = {
    "hvac.set": lambda s: {"object": "aircon", "operate": "set",
                           "value": s.get("temp") or s.get("value")},
    "hvac.on": lambda s: {"object": "aircon", "operate": "open"},
    "hvac.off": lambda s: {"object": "aircon", "operate": "close"},
    "hvac.inc": lambda s: {"object": "aircon", "operate": "inc"},
    "hvac.dec": lambda s: {"object": "aircon", "operate": "dec"},
    "window.open": lambda s: {"object": "window", "operate": "open"},
    "window.close": lambda s: {"object": "window", "operate": "close"},
    "media.play": lambda s: {"object": "media", "operate": "play"},
    "media.pause": lambda s: {"object": "media", "operate": "pause"},
    "volume.inc": lambda s: {"object": "volume", "operate": "inc"},
    "volume.dec": lambda s: {"object": "volume", "operate": "dec"},
    "seat.heating.on": lambda s: {"object": "seat", "operate": "open", "mode": "heating"},
    "seat.ventilation.on": lambda s: {"object": "seat", "operate": "open", "mode": "ventilation"},
}


class ONNXEdgeClassifier:
    """Qwen2.5-0.5B INT4（ONNX / ORT GenAI）端侧分类；异常/超时降级规则。"""

    def __init__(self, model_dir: str, timeout_s: float = 0.8):
        import onnxruntime_genai as og
        self._og = og
        self._model = og.Model(model_dir)
        self._tokenizer = og.Tokenizer(self._model)
        self._tokenizer_stream = self._tokenizer.create_stream()
        self._timeout = timeout_s
        self._fallback = RuleBasedEdgeClassifier()
        self._params = og.GeneratorParams(self._model)
        self._params.set_search_options(max_length=128, temperature=0.1)

    def _generate(self, text: str) -> str:
        prompt = f"{_SYSTEM_PROMPT}\n用户话语：{text}\nJSON："
        self._params.input_ids = self._tokenizer.encode(prompt)
        generator = self._og.Generator(self._model, self._params)
        out = ""
        deadline = time.monotonic() + self._timeout
        while not generator.is_done():
            generator.compute_logits()
            generator.generate_next_token()
            out += self._tokenizer_stream.get()
            if time.monotonic() > deadline:
                break
        return out.strip()

    async def classify(self, text: str, context: dict | None = None) -> EdgeClassification:
        try:
            raw = self._generate(text)
            m = _JSON_RE.search(raw)
            data = json.loads(m.group(0)) if m else {}
            model_intent = str(data.get("intent", "")).strip()
            name = _INTENT_MAP.get(model_intent, model_intent)
            slots = {str(k): str(v) for k, v in (data.get("slots") or {}).items()}
            conf = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
            da = data.get("direct_answer") or None

            if data.get("should_escalate") is True:
                reason = _parse_reason(str(data.get("escalation_reason", "")))
                return EdgeClassification(name, slots, conf, True, reason,
                                          direct_answer=da, raw_text=text,
                                          structured=_build_structured(name, slots))
            if conf >= CONF_FAST and name in VEHICLE_CONTROL_INTENTS:
                return EdgeClassification(name, slots, conf, False, raw_text=text,
                                          structured=_build_structured(name, slots))
            if conf >= CONF_ANSWER and da:
                return EdgeClassification(name, slots, conf, False,
                                          direct_answer=da, raw_text=text)
            return EdgeClassification(name, slots, conf, True,
                                      EscalationReason.LOW_CONFIDENCE, raw_text=text)
        except Exception:
            return await self._fallback.classify(text, context)


def _parse_reason(s: str) -> EscalationReason | None:
    for r in EscalationReason:
        if r.value == s:
            return r
    return EscalationReason.LOW_CONFIDENCE


def _build_structured(name: str, slots: dict) -> dict | None:
    builder = _STRUCT.get(name)
    if not builder:
        return None
    return {"domain": "vehicle", "intent": name, "data": builder(slots)}
