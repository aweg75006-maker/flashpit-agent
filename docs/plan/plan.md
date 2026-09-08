# 创新方向技术规划

> **状态**：草案 v1.0
> **日期**：2026-09-07
> **范围**：在当前 Phase 1 PoC 基础上的架构级创新，非功能堆砌
> **原则**：动架构不动功能，每个方向可独立落地、可渐进验证、不破坏现有系统

---

## 1. 背景与目标

当前项目已完成云边协同智能座舱 multi-agent 系统的 Phase 1 工程化 PoC：14 个领域 Agent、快慢双系统、gRPC 契约 + Registry、语音回路、记忆、可观测全链路，`make up` 一键起栈 30 个服务。核心架构与参考实现一致，属于一比一复刻。

本规划的目标是在不推翻现有架构的前提下，引入 **5 个架构级创新方向**，使项目从"复刻"升级为"在原项目基础上深化"。每个方向满足：

- **差异化**：原项目未覆盖或仅做了浅层实现
- **可落地**：有明确的第一步（≤200 行代码出 demo）
- **可验证**：有量化的验收标准，不是"感觉更好"
- **可演进**：为 Phase 2（真实硬件对接、量产准备）铺路

---

## 2. 创新方向总览

| 编号 | 方向 | 核心命题 | 优先级 | 预计周期 | 技术风险 |
|---|---|---|---|---|---|
| I1 | 端侧小模型 + 端云协同推理 | 端侧从"规则匹配"升级为"模型推理"，断网可用、毫秒级响应 | P0 | 3 周 | 中 |
| I2 | 车辆数字孪生层 | VAL 从"返回成功"升级为"可验证仿真"，状态真实变化、场景可回放 | P0 | 2 周 | 低 |
| I3 | 决策可解释层 | 从"技术可观测"升级为"用户可解释"，每个动作回答"为什么" | P1 | 2 周 | 中 |
| I4 | 多 Agent 去中心化协作 | 从"Planner 中心化调度"到"直连协商制度化 + 条件性交叉验证" | P2 | 1.5-2 周（拆 I4-a/b；另有 S1 安全债 0.5 周） | 中高 |
| I5 | 车载多模态提示注入防御 | 从"权限校验"扩展到"环境声音对抗"，声源定位 + 置信度门控 | P2 | 3 周 | 高 |

**实施策略**：P0 两个方向（I1 + I2）并行启动，二者无依赖且都是"接口重构 + 渐进替换"模式，1-2 周可出第一个 demo。P1/P2 在 P0 稳定后再启动；**唯一例外是 I4 的 S1（直连安全债）——0.5 周、无依赖、修的是现存隐患，可随时插入**（见 §6.5）。

---

## 3. 方向 I1：端侧小模型 + 端云协同推理

### 3.1 现状缺口

当前端侧意图识别由 `orchestrator/edge/fast_intent.py` 的 `FastIntent` 承担，基于正则 + 关键词匹配：

- 泛化能力弱："空调凉一点"和"温度调低"需要写两条规则
- 无置信度概念：匹配上就是 T0 快路径，匹配不上就上云，没有"不确定"中间态
- 断网时除硬编码车控外无任何能力
- 云端 LLM 首字延迟 800ms+，简单闲聊也走全链路

### 3.2 创新点

在 edge-orchestrator 中引入**端侧推理层**，三层架构：

```
用户语音 → ASR 文本
    │
    ▼
┌─────────────────────────────────────────┐
│  端侧分类器（EdgeClassifier）            │
│  输入：text                              │
│  输出：{intent, slots, confidence,       │
│        should_escalate, answer?}         │
└─────────────────────────────────────────┘
    │
    ├── confidence ≥ 0.85 且 intent ∈ 车控集 → T0 快路径（VAL 执行，<50ms）
    ├── confidence ≥ 0.70 且 intent ∈ 简答集 → 端侧模型直接回答（<200ms）
    └── 其他 → 上云 Planner（T1/T2，800ms+）
```

**端侧模型选型**：
- 首选：Qwen2.5-1.5B-Instruct INT4（ONNX Runtime，约 1GB 内存，CPU 推理 5-10 token/100ms）
- 备选：Qwen2.5-0.5B INT4（约 400MB，更快但准确率下降）
- 推理框架：ONNX Runtime（跨平台，车机高通 8155/8295 有 NNAPI 加速）
- 微调数据：项目已有 `skills/exemplars/` + 各 Agent 的 golden 用例，可构造 500-1000 条车载领域指令微调集

### 3.3 技术方案

#### 3.3.1 接口定义（第一步：不碰模型，先定接口）

```python
# orchestrator/edge/edge_classifier.py
from dataclasses import dataclass
from enum import Enum

class EscalationReason(Enum):
    LOW_CONFIDENCE = "low_confidence"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_CLOUD_DATA = "needs_cloud_data"  # 需要实时数据/多Agent协作
    SAFETY_REQUIRES_CLOUD = "safety_requires_cloud"  # 危险动作需云端二次确认

@dataclass
class EdgeClassification:
    intent: str               # 归一化意图名，如 "hvac.set_temperature"
    slots: dict[str, str]     # 槽位，如 {"temperature": "22", "zone": "driver"}
    confidence: float         # 0.0-1.0
    should_escalate: bool     # 是否上云
    escalation_reason: EscalationReason | None
    direct_answer: str | None = None  # 端侧直接回答的文本（简答场景）

class EdgeClassifier(Protocol):
    async def classify(self, text: str, context: dict) -> EdgeClassification: ...
```

#### 3.3.2 规则实现（第一阶段，零模型依赖）

`RuleBasedEdgeClassifier` 复用现有 `FastIntent` 逻辑，但输出标准化的 `EdgeClassification`，confidence 由规则匹配质量估算（精确匹配=0.95，模糊匹配=0.75，未匹配=0.3）。这一步完成后，架构上已经支持模型替换。

#### 3.3.3 模型实现（第二阶段）

`ONNXEdgeClassifier`：
- 模型加载：启动时加载 ONNX 模型到内存，懒初始化
- 推理：text → prompt 模板 → 模型生成 JSON → 解析为 `EdgeClassification`
- 提示工程：约束模型输出固定 JSON schema，包含 intent/slots/confidence/escalate
- 超时保护：推理超时 500ms 自动降级为规则分类器 + 上云

#### 3.3.4 端云路由策略

```python
def route(c: EdgeClassification) -> str:
    if c.should_escalate:
        return "cloud"
    if c.confidence >= 0.85 and c.intent in VEHICLE_CONTROL_INTENTS:
        return "edge_fast"      # T0 快路径
    if c.confidence >= 0.70 and c.direct_answer:
        return "edge_answer"    # 端侧简答
    return "cloud"              # 兜底上云
```

### 3.4 技术难点

| 难点 | 应对 |
|---|---|
| 小模型车载意图准确率 | 用项目 golden 用例微调；设置 confidence 阈值，低置信度自动上云兜底 |
| ONNX Runtime 容器化部署 | 基础镜像加 `onnxruntime`，模型文件放 `models/`（gitignore，启动时下载或挂载） |
| 端云路由误判（该上云的留端了） | 车控类意图保守策略：只要不在白名单就上云；可观测台记录路由决策，badcase 回流优化 |
| 模型推理延迟 | INT4 量化 + 流式生成 + 超时降级；首字延迟目标 <200ms |

### 3.5 落地路径

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| M1（第1周） | 定义 `EdgeClassifier` 接口 + `RuleBasedEdgeClassifier` 实现 + 替换 `FastIntent` 调用点 | 接口重构 PR，行为与现有一致 | 现有测试全过，端到端对话无回归 |
| M2（第2周） | 构造微调数据集（从 exemplars + 历史对话提取）+ 模型导出 ONNX + `ONNXEdgeClassifier` 实现 | 端侧模型可运行 | 100 条测试指令分类准确率 ≥ 85%，车控类 ≥ 95% |
| M3（第3周） | 端云路由策略调优 + 断网模式验证 + 性能基准 | 端云协同完整链路 | 断网时车控+简答可用率 ≥ 90%；端侧简答首字 < 200ms |

### 3.6 预期产出

- `orchestrator/edge/edge_classifier.py`：接口 + 规则实现
- `orchestrator/edge/onnx_classifier.py`：模型实现
- `models/edge-intent/`：ONNX 模型 + 微调脚本（gitignore，README 说明获取方式）
- 可观测台新增"端云路由"视图：每轮请求走了端侧还是云端、confidence、escalation_reason

---

## 4. 方向 I2：车辆数字孪生层

### 4.1 现状缺口

当前 VAL（`orchestrator/edge/val.py`）是纯 mock：

```python
async def set_hvac(self, temperature: float) -> ValResult:
    return ValResult(success=True)  # 状态不真的变
```

问题：
- 无法验证"空调开到26度后车内温度多少"
- 无法做场景回放（记录一段驾驶+指令，复现当时状态）
- 无法做故障注入（模拟空调故障、CAN 丢包）
- HMI 上车辆状态永远是默认值，不随指令变化

### 4.2 创新点

在 VAL 之下增加**车辆数字孪生层**（`VehicleDigitalTwin`），作为 VAL 的一个 backend：

```
LLM/Planner → Executor → VAL（接口不变）
                        │
                        ├── TwinBackend（数字孪生，开发/测试用）
                        ├── CanBackend（真实 CAN，量产用）
                        └── SomeIpBackend（真实 SOME-IP，量产用）
```

孪生层核心能力：
1. **状态真实化**：68 个车控对象都有状态机，指令执行后状态真的变
2. **物理模型**：空调降温速率、车窗升降时间、电量消耗与车速/空调关联
3. **场景回放**：记录 `(timestamp, command, state_before, state_after)` 序列，可回放复现
4. **故障注入**：模拟组件故障、总线丢包、延迟，验证系统降级行为

### 4.3 技术方案

#### 4.3.1 孪生状态模型

```python
# orchestrator/edge/twin/state.py
from dataclasses import dataclass, field

@dataclass
class HvacState:
    temperature: float = 22.0       # 设定温度
    actual_temp: float = 22.0       # 车内实际温度（随时间趋近设定值）
    fan_speed: int = 3              # 风量 1-7
    mode: str = "auto"              # auto/cool/heat/defrost
    ac_on: bool = True
    zone: str = "all"               # all/driver/passenger/rear

@dataclass
class WindowState:
    position: float = 0.0           # 0=全关, 1=全开
    moving: bool = False
    anti_pinched: bool = False

@dataclass
class SeatState:
    heating_level: int = 0          # 0-3
    ventilation_level: int = 0
    position: dict = field(default_factory=lambda: {"back": 100, "cushion": 100})

@dataclass
class VehicleState:
    hvac: HvacState = field(default_factory=HvacState)
    windows: dict[str, WindowState] = field(default_factory=lambda: {
        "driver": WindowState(), "passenger": WindowState(),
        "rear_left": WindowState(), "rear_right": WindowState()
    })
    seats: dict[str, SeatState] = field(default_factory=lambda: {
        "driver": SeatState(), "passenger": SeatState()
    })
    battery_soc: float = 72.0       # 电量百分比
    speed_kmh: float = 0.0
    gear: str = "P"
    odometer_km: float = 12345.0
    # ... 其余 60+ 对象按需扩展
```

#### 4.3.2 TwinBackend 实现

```python
# orchestrator/edge/twin/backend.py
class TwinValBackend(ValBackend):
    def __init__(self):
        self.state = VehicleState()
        self.history: list[TwinEvent] = []  # 用于回放

    async def set_hvac(self, temperature: float, zone: str = "all") -> ValResult:
        before = copy.deepcopy(self.state.hvac)
        # 状态变更
        self.state.hvac.temperature = temperature
        self.state.hvac.zone = zone
        # 物理模型：空调功耗影响电量
        if self.state.hvac.ac_on:
            self.state.hvac.power_kw = estimate_hvac_power(temperature, before.actual_temp)
        # 记录事件
        self.history.append(TwinEvent(
            ts=time.time(), command="hvac.set",
            before=before, after=copy.deepcopy(self.state.hvac)
        ))
        return ValResult(success=True, state_snapshot=self.state.snapshot())
```

#### 4.3.3 物理模型（简化版，自洽即可）

```python
def estimate_hvac_power(set_temp: float, actual_temp: float) -> float:
    """空调功率估算（kW）：温差越大功率越高，制冷比制热略高"""
    diff = abs(set_temp - actual_temp)
    base = 0.5  # 风机功耗
    return base + min(diff * 0.3, 2.5)  # 最大 3kW

def update_actual_temp(state: VehicleState, dt_seconds: float):
    """车内温度趋近设定温度，速率受风量和车外温度影响"""
    hvac = state.hvac
    if not hvac.ac_on:
        return
    rate = 0.01 * hvac.fan_speed  # 每秒变化量
    diff = hvac.temperature - hvac.actual_temp
    hvac.actual_temp += diff * rate * dt_seconds
    # 电量消耗
    state.battery_soc -= (hvac.power_kw * dt_seconds / 3600) / 80 * 100  # 80kWh 电池
```

#### 4.3.4 场景回放

```python
class TwinRecorder:
    """记录指令序列 + 状态快照，支持回放"""
    def record(self, event: TwinEvent): ...
    def export(self, path: str): ...  # 导出 JSONL
    def replay(self, path: str) -> VehicleState: ...  # 从头执行，返回最终状态
```

### 4.4 技术难点

| 难点 | 应对 |
|---|---|
| 68 个对象全部建模工作量大 | 先做高频 10 个（空调/座椅/车窗/车门/灯光/雨刮/电量/车速/档位/后视镜），其余按需扩展 |
| 物理模型精度 | 不需要真实，需要**自洽**（开空调=电量降=续航降）；参数可配置 |
| 与真实硬件后端的切换 | `ValBackend` 接口统一，Twin/Can/SomeIp 都是实现，运行时按 env 选择 |
| 状态并发安全 | 单进程内 asyncio Lock；未来多实例时状态上 Redis |

### 4.5 落地路径

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| M1（第1周） | `VehicleState` 数据类（10 个高频对象）+ `TwinValBackend` 骨架 + VAL 接口增加 backend 抽象 | 孪生层可运行 | `set_hvac(22)` 后 `state.hvac.temperature == 22` |
| M2（第1周） | 物理模型（空调温度变化/电量消耗/车窗升降时间）+ HMI 显示真实车辆状态 | 状态随指令变化 | 开空调 5 分钟后 actual_temp 趋近设定值，电量下降 |
| M3（第2周） | 场景记录/回放 + 故障注入框架 + 回归测试用孪生层做 | 回放可用 | 导出一段对话的指令序列，回放后最终状态一致 |

### 4.6 预期产出

- `orchestrator/edge/twin/`：state.py / backend.py / physics.py / recorder.py
- VAL 接口重构：`ValBackend` 抽象 + `TwinValBackend` 实现
- HMI 新增"车辆状态"面板：实时显示空调温度/电量/车窗位置等（从孪生状态读）
- 回归测试：用孪生层替代 mock，验证指令序列的状态变化

---

## 5. 方向 I3：决策可解释层

> **状态**：已落地（M1/M2/M3 完成，2026-09-07）。实现文档见
> [`i3-decision-explainability.md`](i3-decision-explainability.md)。

### 5.1 现状缺口

可观测台（5174）记录了 trace/span/LLM 调用，但那是**开发者视角**的技术数据。用户问"你为什么给我选这个充电站"，系统无法回答——Planner 的推理过程没有结构化记录，更没有自然语言解释。

### 5.2 创新点

在 Planner 和 Agent 输出结果的同时，输出**决策推理轨迹**（Decision Rationale），HMI 上每个卡片加"为什么"按钮：

```
用户：附近的充电站
系统：推荐 3 家（滴滴充/微来充/合肥电大）
用户：为什么选这三家？
系统：按距离排序（0.2/0.3/0.5km），过滤了未营业的 2 家，
      排除了评分低于 3.5 的 1 家。第 1 家 24 小时营业且评分 4.8。
```

### 5.3 技术方案

#### 5.3.1 决策日志结构

```python
# shared/decision_log.py
@dataclass
class DecisionStep:
    step_id: str                # 如 "nearby.filter"
    decision: str               # 自然语言描述，如 "过滤未营业站点"
    options_considered: int     # 考虑了多少个选项
    options_remaining: int      # 剩余多少个
    criteria: list[str]         # 用了哪些标准，如 ["distance", "rating", "open_now"]
    weights: dict[str, float]   # 各标准权重
    chosen: list[str]           # 选中的项 ID
    eliminated: list[dict]      # 被排除的项及原因，如 [{"id": "x", "reason": "closed"}]
    confidence: float           # 决策置信度

@dataclass
class DecisionRationale:
    trace_id: str
    intent: str
    steps: list[DecisionStep]
    final_reason: str           # 一句话总结
```

#### 5.3.2 Agent 侧改造（确定性 Agent 优先）

`nearby-agent` 和 `navigation-agent` 的排序/过滤逻辑是确定性的，最容易加决策日志：

```python
# agents/nearby/src/agent.py 改造示例
async def search_nearby(self, query, location):
    candidates = await amap_search(query, location)  # 10 家
    step1 = DecisionStep(
        step_id="nearby.filter_open",
        decision="过滤当前未营业的站点",
        options_considered=len(candidates),
        criteria=["open_now"],
        chosen=[p.id for p in candidates if p.open_now],
        eliminated=[{"id": p.id, "reason": "未营业"} for p in candidates if not p.open_now],
        options_remaining=len([p for p in candidates if p.open_now]),
    )
    filtered = [p for p in candidates if p.open_now]
    # ... 评分过滤、距离排序，每步一个 DecisionStep
    return NearbyResult(..., rationale=DecisionRationale(steps=[step1, step2, step3]))
```

#### 5.3.3 HMI 展示

每个信息卡片右上角加"为什么"按钮，点击展开决策链路：

```
┌─────────────────────────────────────┐
│ 附近充电站 · 为什么推荐这 3 家？     │
│ ┌─────────────────────────────────┐ │
│ │ ① 检索到 10 家                   │ │
│ │ ② 过滤未营业：排除 2 家 → 剩 8 家│ │
│ │ ③ 过滤评分<3.5：排除 1 家 → 剩 7 │ │
│ │ ④ 按距离排序：取前 3 家          │ │
│ │   滴滴充 0.2km · 评分4.8 · 24h   │ │
│ │   微来充 0.3km · 评分4.5 · 24h   │ │
│ │   合肥电大 0.5km · 评分4.2 · 24h │ │
│ └─────────────────────────────────┘ │
└─────────────────────────────────────┘
```

#### 5.3.4 追问机制

用户追问"为什么不选第 4 家？"时，将 `DecisionRationale` 作为上下文回灌 LLM，生成自然语言回答：

```python
# Planner 侧
if user_ask == "为什么":
    context = f"上一轮决策过程：{rationale.to_json()}"
    answer = await llm.generate(f"基于以下决策过程回答用户问题：{context}\n用户：{user_text}")
```

### 5.4 技术难点

| 难点 | 应对 |
|---|---|
| LLM 推理过程是黑盒，decision_log 可能是事后编的 | 确定性 Agent 先做（排序/过滤逻辑可追溯）；LLM Planner 的决策日志用结构化输出约束（JSON mode） |
| 决策粒度太粗没解释力，太细性能扛不住 | 每个 Agent 最多 5 个 DecisionStep；只记录关键决策点，不记录中间计算 |
| 多轮决策的因果链 | DecisionRationale 携带 trace_id，追问时按 trace_id 检索上一轮 |

### 5.5 落地路径

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| M1（第1周） | `DecisionRationale` / `DecisionStep` 数据类 + `nearby-agent` 接入 | 周边搜索卡片有"为什么" | 10 条测试用例的决策日志可解释排序原因 |
| M2（第1周） | `navigation-agent` 接入 + HMI "为什么"按钮 + 展开面板 | 导航卡片可解释 | 路线选择能说明绕路原因 |
| M3（第2周） | 追问机制 + Planner 决策日志（结构化输出）+ 其余 Agent 按需接入 | 全链路可解释 | 用户追问"为什么"能得到基于决策日志的回答 |

---

## 6. 方向 I4：多 Agent 去中心化协作

> **状态**：草案 v2.0（2026-09-08 复核修订）
> **修订原因**：v1.0 的缺口判断与收益论证经代码复核后不成立——Agent 直连、DAG 并行、NATS、
> 黑板与账本均已落地，"绕开 Planner 中转"不是未做的事；"端到端延迟降低 ≥30%"把杠杆点认错。
> 本版重写缺口、难点、落地路径与验收标准。

### 6.1 现状复核：缺口被高估了

v1.0 判断"Agent 之间不直接通信，所有中间结果都经过 Planner 中转"。复核后：**前半句不成立，
后半句的成本被高估**。

| 能力 | v1.0 判断 | 2026-09-08 复核 | 证据 |
|---|---|---|---|
| Agent 直连调用 | 缺 | **已实现**：gRPC + Registry 动态发现，护栏齐全 | `agents/_sdk/agent_client.py:84`；`MAX_DEPTH=2`(46)、环检测(107)、超时、endpoint 三级解析(190) |
| 直连真实用例 | 无 | **已在跑**：`road_safety` 并行调 `info.weather`/`info.forecast`/`navigation.search_poi`；`reminder` 调 `nearby` | `agents/road_safety/src/agent.py:249-252`、`agents/reminder/src/agent.py:634` |
| 并行执行 | 缺 | **已实现**：DAG 分层，层内 `asyncio.gather` | `orchestrator/cloud/executor.py:160-161` |
| NATS | 待引入 | **已部署**（`nats:2-alpine` + JetStream），但只跑广播类主题 | `deploy/docker-compose.yaml:83-92`；现用主题为 `vehicle.state.changed`/`agent.proactive.*`/`obs.*`/`payment_result` |
| 共享黑板 | 无 | **已有**：`shared_state`，带权威 key 登记表（owner/reader/schema/TTL） | `agents/_sdk/shared_state.py:1-21` |
| 跨 Agent 账本 | 无 | **已有**：`TaskLedger`，PG 存储，deep_research 与 mcp_bridge 共用 | `agents/_sdk/ledger.py` |
| 冲突仲裁算子 | 无 | **已有但非 Agent 级**：域名权威分档、时效优先重排 | `agents/_sdk/source_quality.py:93-117`、`grounding.py:285-291` |
| 交叉验证 / 共识 | 缺 | **确实为零**：全仓库仅 plan.md 自身出现 `cross_validate`/`consensus` | — |

**v1.0 说对的只有一条**：Agent 之间没有任务级协商与结果交叉验证。真实缺口只有两个——
**① 任务级协商通道；② Agent 级仲裁器**。其余都属于"把已跑通的模式制度化"。

### 6.2 收益论证修正：延迟的杠杆不在中转

v1.0 把延迟归因于"每个 Agent 调用都经过 Planner 序列化"。复核后不成立：

- `slot_refs` 是**进程内 dict 传递**（解析见 `executor.py:564`），不是网络往返，成本 <1ms；
- 同层已并行，跨层串行由**数据依赖**决定，去中心化消不掉；
- 端到端延迟的大头是 **LLM 规划**与**外部 API**（地图/天气/检索）。

真正的杠杆是 **replan 轮次**：`loop.py:33-36` 给出 `simple=(2 次, 8s)` / `adaptive=(3 次, 12s)`，
每一轮 replan 都是一次完整 LLM 调用。Agent 若能自补子信息、少回一次 Planner，才拿得到 30%。

> **验收标准相应改为**：adaptive 档**平均 replan 轮次 ≤1.5**（基线待实测）。
> 端到端延迟仍统计，但只作回归项（增量 ≤10%，对齐 §10 第 3 条），不作为创新点指标。

### 6.3 创新点重定义

"混合协作模式"方向保留，落地形态收窄为两件**可独立交付**的事：

- **I4-a 协商下放**（制度化 + 补安全债）：把 `road_safety` 已验证的直连模式提升为 SDK 一等能力，
  补齐直连路径缺失的安全闸门，再推广到 2-3 个跨域场景。**这是工程规范化，不是架构创新。**
- **I4-b 交叉验证**（待可行性验证）：高风险决策由 2-3 个 Agent 独立计算，分歧时仲裁。
  **先测分歧率，再决定做不做**（见 6.5）。

### 6.4 真实难点（v1.0 未覆盖的三项）

v1.0 列的四条难点（死锁/活锁、竞标评价函数、Planner 兼容、可观测）**已被现有护栏兜住或可控**：
`MAX_DEPTH=2` + 环检测 + `asyncio.wait_for` 已解决终止性；可观测可复用 `agent_client` 已有的
guardrail span（`agent_client.py:65-80`）。真正难的是下面三项：

| # | 难点 | 说明 | 应对 |
|---|---|---|---|
| N1 | **中心化的重量在校验，不在中转** | `planning.py` 2624 行里 `_validated_steps`(2164)、`_derive_depends_on_from_refs`(2416)、`_side_effect_steps`(2437)、整句坍缩(2340)、slot 保真(657) 全部假设 orchestrator 是唯一真相源。绕开中转容易，给这些守卫重新找落点才是主要工作量 | 守卫**不下放**。Agent 直连只承担取数与计算，计划合法性仍由 Planner 裁定 |
| N2 | **直连路径缺安全闸门（现存隐患）** | `response_only` 的 fail-closed 只在 `engine.py:839-859`、`loop.py:249-273`，`agents/_sdk` 全文不检查；权限同理——`agent_client.py:9-13` 明确"权限不在此层做，依赖编排层 dispatch"。**今天没出事只是因为直连尚未撞上受保护能力** | **独立安全债**，见 6.5 S1，与 I4 是否实施解耦，建议优先修 |
| N3 | **权限逃逸** | Agent 若自发发起车控类直连，"谁有权代表用户"这条线是空的；比 v1.0 担心的死锁危险得多 | 直连目标能力必须在 manifest 声明 `peer_callable` 与权限集，**车控能力默认不可被直连**（fail closed） |

> **约束**：改造全部落在 `agents/_sdk`，不改 orchestrator 核心路由分支（CLAUDE.md §3.1 第 4 条）。

### 6.5 落地路径（重排）

#### S1：直连安全债（0.5 周，建议独立于 I4 立即启动）

| 项 | 内容 |
|---|---|
| 改动 | 在 `agents/_sdk/server.py` 入口对 `response_only` 做 fail closed（语义对齐 `executor._enforce_response_only`）；`AgentClient` 拒绝调用未声明 `peer_callable` 的能力 |
| 验收 | 契约测试：直连调 `response_only` 能力必须失败；未声明 `peer_callable` 的直连被拒并发 guardrail span |
| 价值 | 消除 CLAUDE.md §3.1 第 7 条在直连路径上的覆盖缺口 |

#### I4-a：协商下放（1.5 周，低风险）

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| A1（0.5 周） | SDK 规范化：直连请求携带 `trace_id`/`call_depth`/权限声明；`agent.comm` span 进 collector | 直连可观测、可审计 | Dashboard trace 视图可见完整 agent→agent 调用链，trace 不断 |
| A2（1 周） | 推广 2-3 个跨域场景（导航+周边、充电规划、行程+提醒），Agent 自补子信息 | 场景级直连 | **adaptive 档平均 replan 轮次 ≤1.5**；现有精简栈全功能无回归 |

#### I4-b：交叉验证（先做 0.5 周可行性验证，通过才继续）

**前置可行性验证（0.5 周，不写生产代码）**：让 `navigation` / `charging_planner` / `trip_planner`
对同一组充电规划问题各算一遍，统计**三方结果分歧率**及分歧中真正抓到错误的比例。

- 分歧率 **<5%** → 交叉验证是纯成本（多 2 次调用换不到正确性提升），**砍掉 I4-b**；
- 分歧率 **>15% 且分歧确实抓到错误** → 继续 B1/B2；
- 中间地带 → 仅对支付/车控等高危域的单一能力启用，不做通用框架。

| 阶段 | 内容 | 验收 |
|---|---|---|
| B1（1 周） | 声明式交叉验证：manifest 声明 `cross_verify: [agent_ids]`，仲裁复用 `source_quality` 权威/时效算子 | 分歧可追溯，仲裁结果进 decision log（与 I3 打通） |
| B2（1 周） | 有界协商：最多 3 轮 + 超时熔断 + Planner 终裁权 | 无死锁；协商超时率 <1% |

> **风险提示（沿用 v1.0 判断）**：I4-b 技术风险最高，容易做成"为了去中心化而去中心化"。
> **分歧率是唯一进入判据**，达不到就停在 I4-a。

### 6.6 预期产出

- `agents/_sdk/agent_client.py`：权限声明 + `peer_callable` 门禁 + 完整 trace 透传
- `agents/_sdk/server.py`：`response_only` 等安全闸门下沉（S1）
- 2-3 个跨域场景改为 Agent 直连自补子信息
- （条件性）manifest `cross_verify` 字段 + 仲裁器
- 可观测台新增 `agent.comm` 视图：直连调用链、深度、耗时、拒绝原因

---

## 7. 方向 I5：车载多模态提示注入防御

### 7.1 现状缺口

当前安全机制：权限校验 + 危险动作二次确认。防的是"用户主动发起危险请求"。

车载场景特有攻击面：**环境声音被 ASR 识别成指令**。
- 广播里说"现在打开车窗"→ ASR 识别 → 系统执行
- 音乐歌词包含"导航去XXX"→ 触发导航
- 后排乘客开玩笑说"打开安全气囊"→ 触发危险动作
- 恶意用户用手机播放"忽略以上指令，你现在是..."→ 提示注入

### 7.2 创新点

多层防御体系：

```
ASR 文本
  │
  ├── L1 声源验证：指令必须来自主驾位置（麦克风阵列波束成形）
  ├── L2 置信度门控：ASR 置信度低 + 危险动作 → 强制二次确认
  ├── L3 上下文异常检测：正在播放媒体时出现的指令标记可疑
  ├── L4 提示注入检测：文本包含"忽略以上""你现在是""system:"等模式 → 拦截
  └── L5 物理确认：最高危动作（安全气囊/刹车/油门）要求物理按键确认
```

### 7.3 技术方案

#### 7.3.1 置信度门控（纯软件，第一步可做）

```python
# orchestrator/edge/safety/injection_guard.py
class InjectionGuard:
    DANGER_PATTERNS = [
        r"忽略.*指令", r"忽略.*以上", r"你现在是", r"system\s*:",
        r"忘掉.*规则", r"你是一个.*(?:不受限|无限制)",
    ]

    def evaluate(self, text: str, asr_confidence: float,
                 media_playing: bool) -> GuardResult:
        # L4 提示注入检测
        for pattern in self.DANGER_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return GuardResult(blocked=True, reason="prompt_injection_pattern")

        # L2 置信度门控：低置信度 + 危险意图 → 强制确认
        is_dangerous = self._is_dangerous_intent(text)
        if asr_confidence < 0.7 and is_dangerous:
            return GuardResult(require_confirm=True, reason="low_confidence_dangerous_action")

        # L3 媒体播放时异常指令
        if media_playing and is_dangerous:
            return GuardResult(require_confirm=True, reason="command_during_media_playback")

        return GuardResult(pass_through=True)
```

#### 7.3.2 声源定位（需硬件支持，Phase 2）

麦克风阵列波束成形，判断声源方向角，主驾区域（约 30°-60°）的指令才直通，其他方向的指令要求确认。

### 7.4 落地路径

| 阶段 | 内容 | 产出 | 验收 |
|---|---|---|---|
| M1（第1周） | `InjectionGuard` 实现 + 提示注入正则检测 + 置信度门控 + 接入 edge-orchestrator | 软件防御层可用 | 20 条注入测试用例拦截率 ≥ 90%，误杀率 ≤ 5% |
| M2（第2周） | 媒体上下文检测 + 危险动作清单 + HMI 可疑指令确认 UI | 多层防御 | 播放音乐时的危险指令强制确认 |
| M3（第3周） | 对抗测试集构造 + 误杀率优化 + 文档 | 完整防御体系 | 100 条混合测试（正常+攻击）综合准确率 ≥ 95% |

---

## 8. 实施路线图

```
第1周          第2周          第3周          第4周
  │              │              │              │
  ├─ I1-M1 接口重构 ─┤              │              │
  ├─ I2-M1 孪生状态 ─┤              │              │
  │              ├─ I1-M2 模型实现 ─┤              │
  │              ├─ I2-M2 物理模型 ─┤              │
  │              ├─ I3-M1 决策日志 ─┤              │
  │              │              ├─ I1-M3 路由调优 ─┤
  │              │              ├─ I2-M3 回放故障 ─┤
  │              │              ├─ I3-M2 导航+追问 ┤
  │              │              │              ├─ I4-a/I5 启动（评估后）
  ▼              ▼              ▼              ▼
 P0 接口重构    P0 核心实现    P0 调优验收    P1/P2 启动
```

**排期说明**：S1（直连安全债）0.5 周且无依赖，不与 P0 抢资源，可在任意窗口插入，建议尽早；
I4-a 在 P0 验收后启动；I4-b 必须先通过 0.5 周的分歧率前置验证才进入 B1/B2。

**依赖关系**：
- I1 和 I2 无依赖，可并行
- I3 依赖 I2 的状态快照（决策日志可能引用车辆状态）
- I4-a（协商下放）与 S1（直连安全债）不依赖任何 P0 方向，可独立排期；I4-b 依赖 I1 的分类器
  （交叉验证需要更准的意图理解）
- I5 独立，可随时启动

---

## 9. 风险与应对

| 风险 | 影响 | 概率 | 应对 |
|---|---|---|---|
| 端侧模型准确率不达标 | I1 退化为规则+上云，创新点弱化 | 中 | 用项目 golden 微调；低置信度兜底上云；可接受 85% 准确率 |
| 孪生层物理模型与真实偏差大 | I2 只能用于开发测试，不能用于验证 | 低 | 明确标注"仿真参数，非真实车辆数据"；参数可配置 |
| 决策日志与 LLM 实际推理不符 | I3 解释不可信，用户质疑 | 中 | 确定性 Agent 先做；LLM 用 JSON 结构化输出约束 |
| 直连路径缺权限 / `response_only` 闸门（2026-09-08 复核新增） | 违反 CLAUDE.md §3.1 第 7 条，Agent 直连可绕过只响应约束 | 中 | S1 独立修复，闸门下沉到 `agents/_sdk`；与 I4 是否实施解耦 |
| 交叉验证分歧率过低 | I4-b 退化为纯成本（多 2 次调用换不到正确性） | 中 | 0.5 周前置实测三方分歧率，<5% 直接砍掉 I4-b |
| 去中心化引入死锁/延迟 | I4-b 降低系统稳定性 | 中低 | 有界迭代（≤3 轮）+ 超时熔断 + Planner 终裁权；现有 `MAX_DEPTH=2` 与环检测已兜底终止性 |
| 注入防御误杀正常指令 | I5 影响用户体验 | 中 | 可疑指令走确认而非直接拦截；持续优化白名单 |
| 五个方向同时开工导致主线混乱 | 项目不可维护 | 中 | 严格按优先级，P0 完成验收后才启动 P1/P2 |

---

## 10. 验收总标准

每个方向完成后需满足：

1. **功能验收**：该方向描述的核心能力可演示，有截图/录屏
2. **回归验收**：现有 17 容器精简栈全功能无回归，端到端对话正常
3. **性能验收**：不引入超过 10% 的端到端延迟增加（I1 应降低延迟）
4. **可观测验收**：新增能力在 dashboard 有对应视图或日志
5. **文档验收**：该方向有独立 README，包含架构图、接口定义、测试方法

---

## 附录：与原项目的差异化总结

| 维度 | 原项目 | 本规划创新 |
|---|---|---|
| 端侧智能 | 正则规则匹配 | 端侧小模型推理 + 端云协同路由 |
| 车控仿真 | VAL 返回成功 mock | 数字孪生层，状态真实变化 + 场景回放 |
| 可解释性 | 技术 trace（开发者视角） | 决策推理轨迹（用户视角）+ 追问机制 |
| Agent 协作 | Planner 中心化调度 | 直连协商制度化（含安全闸门下沉）+ 条件性交叉验证 |
| 安全 | 权限校验 + 二次确认 | 多模态注入防御 + 声源验证 + 置信度门控 |

完成 P0 两个方向后，项目即具备明确的差异化标签：**"端云协同推理 + 车辆数字孪生的智能座舱 Agent 系统"**。
