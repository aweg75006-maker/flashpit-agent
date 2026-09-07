"""决策可解释层（I3）——结构化决策推理轨迹的数据模型。

## 它解决什么

可观测台（5174）的 trace/span/LLM 调用是**开发者视角**的技术数据；用户问
「你为什么给我选这几家充电站」，系统答不上来——Planner/Agent 的推理过程没有
结构化记录，更没有用户可读的自然语言解释。

本模块给出这条轨迹的**载体**：Agent 在产出结果的同时，把关键决策点（过滤了
什么、按什么排序、排除了哪些、为什么）记成一组 `DecisionStep`，汇总为一个
`DecisionRationale` 挂到 ui_card 的 `_rationale` 字段，HMI 点「为什么」展开。

## 为什么住在 runtime/

消费方横跨两个镜像：产生方在**云侧 Agent**（nearby/navigation），展示方在
**HMI**（读卡片字段，只认 JSON），而 M3 追问还要在**云侧编排**按 trace_id 回灌
LLM。落点判据是镜像依赖闭包——同 `polarity` / `session_constraints` 那几笔，
Agent 侧 SDK 镜像与云侧编排镜像都够得着 `runtime/`。

## 设计边界（对齐 plan.md §5.4）

- **只记关键决策点，不记中间计算**：每个 Agent 最多 5 个 step，多了就是流水账。
- **置信度表达诚实的把握**：确定性过滤（营业时间）conf=1.0；近似重排（停车便利、
  氛围——地图没有对应字段）conf 调低，HMI 如实标注「近似」。
- **ui_card 是自由 Struct**：`_rationale` 作为普通字段透传，零 proto 改动
  （同 `_prov` 的真实性标记路径，见 `agents/_sdk/provenance.py`）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class DecisionStep:
    """一次关键决策动作——过滤 / 排序 / 重排的最小可解释单元。

    一个 step 回答「这一步对候选做了什么、依据什么标准、结果如何」。
    """

    step_id: str = ""
    #: 自然语言描述，如「过滤当前未营业的站点」——直接进 HMI 面板，不二次生成。
    decision: str = ""
    #: 这一步开始前面对多少个选项（检索到的候选数 / 上一步剩下来的数量）。
    options_considered: int = 0
    #: 这一步结束还剩多少个选项。过滤类 remaining < considered；重排类两者相等。
    options_remaining: int = 0
    #: 用到的标准名（如 ["open_now"] / ["distance", "rating"]）。
    criteria: list[str] = field(default_factory=list)
    #: 这一步「选中/保留」的项 ID。重排类可填排序后前几名的 name；过滤类可不填。
    chosen: list[str] = field(default_factory=list)
    #: 被排除的项及原因，形如 [{"id": "x", "name": "某某", "reason": "未营业"}]。
    eliminated: list[dict] = field(default_factory=list)
    #: 这一步的把握程度（0~1）。确定性步骤=1.0；近似重排如实调低。
    confidence: float = 1.0

    def to_dict(self) -> dict:
        """转成可 JSON 序列化的 dict（挂进 ui_card._rationale.steps）。"""
        return {
            "step_id": self.step_id,
            "decision": self.decision,
            "options_considered": self.options_considered,
            "options_remaining": self.options_remaining,
            "criteria": list(self.criteria),
            "chosen": list(self.chosen),
            "eliminated": [dict(e) for e in self.eliminated],
            "confidence": self.confidence,
        }


@dataclass
class DecisionRationale:
    """一次意图的完整决策轨迹：一串 DecisionStep + 一句话总结。"""

    #: 归属的 trace（M3 追问按它跨轮检索上一轮决策；M1 卡片展示可不填）。
    trace_id: str = ""
    #: 意图名，如 "nearby.search"。
    intent: str = ""
    #: 有序的决策步骤（最多 5 个，见模块 docstring）。
    steps: list[DecisionStep] = field(default_factory=list)
    #: 一句话总结（已有话术可直接复用，如「不合口味的已排后」）。
    final_reason: str = ""

    def add(self, step: DecisionStep) -> "DecisionRationale":
        """追加一步，链式构造用。返回 self 便于内联。"""
        self.steps.append(step)
        return self

    def to_dict(self) -> dict:
        """转成挂到 ui_card._rationale 的 dict。"""
        return {
            "trace_id": self.trace_id,
            "intent": self.intent,
            "steps": [s.to_dict() for s in self.steps],
            "final_reason": self.final_reason,
        }

    def to_json(self) -> str:
        """JSON 字符串——M3 追问时作为上下文回灌 LLM 的形态。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    def attach_to(self, card: dict | None) -> dict | None:
        """把决策轨迹挂到 ui_card 的 `_rationale` 字段（原地，返回 card 便于内联）。

        card 为 None 原样返回——同 `provenance.attach` 的容错口径。空轨迹（无任何
        step）不挂，避免 HMI 渲染出一个没有内容的「为什么」按钮。
        """
        if card is None or not self.steps:
            return card
        card["_rationale"] = self.to_dict()
        return card
