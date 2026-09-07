# 方向 I3：决策可解释层 — 实现文档

> 状态：已落地（M1/M2/M3 全部完成）
> 规划来源：`docs/plan/plan.md` §5
> 日期：2026-09-07
> 一句话：让每个动作回答「为什么」——从开发者视角的技术 trace，升级为用户视角的自然语言解释。

---

## 1. 概述

可观测台（5174）的 trace/span/LLM 调用是**开发者视角**的技术数据。用户问「你为什么给我选这几家充电站」，
系统此前答不上来——Planner 的推理过程没有结构化记录，更没有自然语言解释。

本方向引入**决策推理轨迹（Decision Rationale）**：Agent 与 Planner 在产出结果的同时，把关键决策点
（过滤了什么、按什么排序、排除了哪些、为什么）记成一组 `DecisionStep`，汇总为 `DecisionRationale`
挂到 ui_card，HMI 每张卡片加「为什么」按钮展开决策链路；用户追问「为什么」时，系统把上一轮的
决策轨迹回灌作答。

### 核心设计原则

1. **零 proto 改动**：`ui_card` 是自由 `google.protobuf.Struct`（`proto/cockpit/agent/v1/agent.proto` 与
   `proto/cockpit/orchestrator/v1/orchestrator.proto`），决策轨迹作为普通字段透传，照抄既有
   `_prov`（数据真实性标记）的先例路径。
2. **确定性优先**：先做排序/过滤逻辑可追溯的确定性 Agent（nearby/navigation），再做编排层的确定性决策。
   **LLM 黑盒推理解释（JSON mode）明确留 Phase 2**（见 §7）。
3. **诚实标注近似**：地图没有「安静度/无障碍/排队」字段时，近似重排如实标 `confidence < 1`，不假装精确。

---

## 2. 架构总览

```
用户语音 → ASR → Planner（规划，产出 planner_rationale：为什么做这些步骤）
                        │
                        ▼
                   Executor → Agent（nearby/navigation 产出决策轨迹：为什么这么选）
                        │
                        ▼
              ui_card（自由 Struct，挂 _rationale / _planner_rationale）
                        │
        ┌───────────────┼───────────────────┐
        ▼               ▼                   ▼
   HMI 渲染        extract_focus        aggregator
  （为什么按钮）   抽 card._rationale    组装最终卡
                   → Focus.last_rationale
                        │
                 update_focus（粘性接力，Redis）
                        │
                下一轮追问「为什么选这几家」
                        │
        _apply_focus_meta 广播下发 meta.focus_last_rationale
                        │
        chitchat 读 meta → 注入 system prompt → 转述作答
```

两条数据流：

| 流 | 载体 | 回答的问题 | 消费方 |
|---|---|---|---|
| **展示流**（当轮） | `card._rationale` / `card._planner_rationale` | 用户点「为什么」看决策链路 | HMI `RationalePanel` |
| **追问流**（跨轮） | `Focus.last_rationale` → `meta.focus_last_rationale` | 用户开口问「为什么」 | Planner prompt + chitchat system |

---

## 3. 接口定义

### 3.1 数据模型（`runtime/decision_log.py`）

落在 `runtime/`（跨镜像共享，先例 `session_constraints.py`；**不是** plan 初稿写的 `shared/`，
仓库无该目录）。

```python
@dataclass
class DecisionStep:
    step_id: str = ""                 # 如 "nearby.filter_open"
    decision: str = ""                # 自然语言描述，直接进 HMI 面板，不二次生成
    options_considered: int = 0       # 这一步开始前面对多少个选项
    options_remaining: int = 0        # 这一步结束还剩多少个（过滤类 remaining < considered）
    criteria: list[str] = field(...)  # 用到的标准名，如 ["open_now"] / ["distance", "rating"]
    chosen: list[str] = field(...)    # 选中/保留的项 ID（可选）
    eliminated: list[dict] = field(...)  # 被排除项及原因 [{"id","name","reason"}]
    confidence: float = 1.0           # 0~1；确定性步骤=1.0，近似重排如实调低

@dataclass
class DecisionRationale:
    trace_id: str = ""                # M3 追问按它跨轮检索（卡片展示可空）
    intent: str = ""                  # 意图名，如 "nearby.search"
    steps: list[DecisionStep] = field(...)  # 有序决策步骤（每 Agent ≤5，见 plan §5.4）
    final_reason: str = ""            # 一句话总结

    def add(self, step) -> "DecisionRationale"   # 链式追加
    def to_dict(self) -> dict                     # 挂 ui_card 的形态
    def to_json(self) -> str                      # M3 追问回灌 LLM 的形态
    def attach_to(self, card) -> dict | None      # 挂 card["_rationale"]；空轨迹不挂
```

### 3.2 ui_card 契约字段

| 字段 | 层 | 语义 | 产出方 |
|---|---|---|---|
| `card._rationale` | Agent 层 | 「为什么这么选」（过滤/排序/重排） | nearby / navigation |
| `card._planner_rationale` | Planner 层 | 「为什么做这些步骤」（选中意图 + 路由规则 + 兜底） | cloud engine |

两者结构相同（都是 `DecisionRationale.to_dict()`），HMI 的 `RationalePanel` 复用同一套渲染。

### 3.3 跨轮下发字段（step.meta）

| 字段 | 类型 | 消费方 |
|---|---|---|
| `meta.focus_last_rationale` | JSON 字符串（DecisionRationale） | chitchat（追问转述） |

---

## 4. 分层设计

### 4.1 Agent 层（为什么这么选）

**nearby**（`agents/nearby/src/agent.py` `_search`）记录 5 个决策点：

| step_id | 决策 | confidence |
|---|---|---|
| `nearby.search` | 按「{keyword}」检索周边候选（含 rating/price/open_now/sort 口径） | 1.0 |
| `nearby.filter_open` | 按入座时刻筛除明确已闭店的（记 eliminated） | 1.0 |
| `nearby.rerank_ambience` | 按环境标签+评分优先（无安静度字段，近似） | 0.6 |
| `nearby.rerank_parking` | 按周边停车便利度排序（无无障碍字段，近似） | 0.6 |
| `nearby.rerank_taste` | 不合口味的排后（软降权，不删除） | 0.7 |

**navigation**（`agents/navigation/src/agent.py`）：
- `_search_poi`：`navigation.search_poi`（检索）+ `navigation.sort_rating`（评分排序）
- `_route_plan_to`：`navigation.route_strategy`（路线策略，含记忆偏好回退）+ `navigation.route_plan`（路线计算）

### 4.2 Planner 层（为什么做这些步骤）

`engine.py` `_build_planner_rationale` 记录**确定性**的规划决策：

| step_id | 决策 | 触发条件 |
|---|---|---|
| `planner.plan` | 系统规划调用：{agent:intent → ...}（按序） | 有 steps |
| `planner.route_hint` | 命中确定性路由规则，直接确定目标 Agent | `hint_effect` 命中 |
| `planner.fallback` | 无匹配领域能力，兜底到闲聊应答 | 全步 chitchat |

### 4.3 追问机制（跨轮回灌）

1. **抽取**：`extract_focus` 从本轮成功步的 `card._rationale` 抽到 `Focus.last_rationale`。
2. **接力**：`update_focus` 粘性接力（普通轮不抹掉，同 `last_places`/`active_route` 那族）。
3. **渲染**：`_render_focus` 只抽 `decision` 短句（≤5 步）进 Planner prompt。
4. **下发**：`_apply_focus_meta` 广播 `focus_last_rationale` 到 step.meta（同 `safety_alert` 口径，
   不按 scope 门控——追问最常落到闲聊兜底）。
5. **消费**：chitchat `_rationale_context(meta)` 解析后注入 system prompt，用户问「为什么」时据此转述、不编造。

---

## 5. 改动清单

| 文件 | 改动 |
|---|---|
| `runtime/decision_log.py` | 新增：`DecisionStep` + `DecisionRationale` 数据模型 |
| `agents/nearby/src/agent.py` | `_search` 记录 5 步决策，`attach_to(card)` |
| `agents/navigation/src/agent.py` | `_search_poi` / `_route_plan_to` 记录决策 |
| `agents/chitchat/src/agent.py` | `_rationale_context(meta)` 消费追问上下文 |
| `orchestrator/cloud/context.py` | `Focus.last_rationale` + 抽取 + 接力 + 渲染 |
| `orchestrator/cloud/engine.py` | `_apply_focus_meta` 下发 + `_build_planner_rationale` + `_attach_planner_rationale` |
| `orchestrator/cloud/models.py` | `Plan.planner_rationale` 字段 |
| `hmi/src/types.ts` | `DecisionStep` / `DecisionRationale` 类型 + 卡片字段 |
| `hmi/src/components/Cards.tsx` | `RationalePanel`（两层）+ 「为什么」按钮 |

**零改动**：proto / 网关 / orchestrator 核心路由分支（决策轨迹作为自由 Struct 字段透传）。

---

## 6. 测试方法

### 6.1 静态验证（本地，无需起栈）

```bash
# Python 语法
python3 -m py_compile runtime/decision_log.py \
  agents/nearby/src/agent.py agents/navigation/src/agent.py agents/chitchat/src/agent.py \
  orchestrator/cloud/context.py orchestrator/cloud/engine.py orchestrator/cloud/models.py

# 数据模型冒烟
python3 -c "from runtime.decision_log import DecisionStep, DecisionRationale; ..."

# 前端类型（hmi/ 目录内）
./node_modules/.bin/tsc --noEmit
```

> 注：`tsc` 会有项目既有的历史报错（`cardMath.mjs` 的 `airQualityBadge`、若干 `.mjs` 无声明文件），
> 与 I3 无关；只需确认没有 `rationale` / `DecisionStep` / `hasRationale` 相关的新报错。

### 6.2 真栈冒烟（`make up` 后）

**场景 A — 卡片「为什么」按钮**：
1. 说「附近好吃的」→ nearby 出 place_list 卡，右上角「为什么」。
2. 点「为什么」→ 展开决策链路（检索 → 过滤 → 排序，含排除项与近似标注）。

**场景 B — 追问回灌**：
1. 说「附近好吃的」→ 得到推荐。
2. 紧接说「为什么选这几家」→ 系统基于上一轮决策轨迹转述（不编造）。

**场景 C — 导航策略解释**：
1. 说「不走高速去机场」→ route_plan 卡「为什么」→ 展开「已按您的偏好避开高速」。

### 6.3 验收对照（plan §10）

| 验收项 | 达标情况 |
|---|---|
| 功能验收：核心能力可演示 | 场景 A/B/C 可演示 |
| 回归验收：现有精简栈无回归 | 纯增量，零 proto/路由改动 |
| 性能验收：不增 >10% 延迟 | 决策日志是内存内 dict 组装，无网络开销 |
| 可观测验收：dashboard 有对应视图 | 决策轨迹随卡片透传，可经 trace 观测 |
| 文档验收：独立 README | 本文档 |

---

## 7. 边界与后续

| 项 | 状态 | 说明 |
|---|---|---|
| LLM Planner 黑盒推理解释（JSON mode） | **留 Phase 2** | plan §5.4 明确「decision_log 可能是事后编的」；需 llm-gateway JSON mode 配合 |
| 其余 Agent 接入 | 按需 | 确定性排序/过滤逻辑的 Agent 可照 nearby 模式接入 |
| 多步任务的 Planner 层展示 | 部分 | `card_group` 场景暂不挂 `_planner_rationale`（多卡并列，编排轨迹意义弱） |
| 决策日志持久化（审计） | 未做 | 当前只跨轮驻留 focus，未落库做长期审计 |
