# skills/ — 规划知识的声明式载体

> 定位：**Skill 是扩展智能的机制，不是运行时**——Agent 仍是部署/隔离/信任边界；skill 只
> 供给 Planner 的规划知识，与 `route_hints`（LLM 后确定性纠错）互补：一个 badcase 先问
> 「是路由错还是知识缺」，再决定投 hint 还是投 skill。**新增可执行能力仍需 Capability/Agent。**
> 第三条路是**范例**（`skills/exemplars/`，契约见该目录 README）：同一件事换个说法就落错，
> 投范例（few-shot，写错=噪声）。route_hint 在 LLM 后硬改写（写错=事故）／guide 教组合
> 判据／exemplar 只作 few-shot。**默认选范例**——它是唯一一个写错了不会伤人的选项。

## 三型对象与目录

```text
skills/
  guides/<kebab-name>.yaml       # type: guide     领域组合知识（预筛注入）
  policies/<kebab-name>.yaml     # type: policy    跨域规划软约束（常驻注入，总量严控）
```

| 型 | 职责 | 装配 | 例 |
|---|---|---|---|
| **PlanningGuide** | 告诉 Planner 何时/如何组合能力（判据+few-shot） | 检索双通道预筛 top-N（默认 3），`SKILL_BUDGET` 内注入 | 多日行程、导航顺路停靠、条件提醒、充电分流 |
| **PlannerPolicyPack** | 跨域规划指导（**软约束**） | 常驻注入，不预筛，**与 guide 共用 `SKILL_BUDGET`** | 时效性判据、禁编造/留空追问、状态查询不硬套、否定与延缓 |

> ⚠ **加一条 policy 会静默挤掉一条 guide**。`render_skills_block` 先无条件铺 policy、
> 再按检索相关度序塞 guide，两者**共用同一个 `SKILL_BUDGET`**——于是「加一条 policy」
> 这个看起来纯加法的动作，会把当轮**最相关**的 guide 记成 `!clipped`。
> **policy 是常驻的，它的字数每一轮规划都在付钱**——写之前先数字数，改之后连带看 guide 的头寸。

## 检索双通道（`SKILLS_RETRIEVAL`）

- **lexical**：keywords 命中（各 10 分）+ 中文 bigram 重合，零网络、离线确定。**盲区**：
  keywords 没写到的说法一律漏召。
- **hybrid（默认）**：词法命中**恒保留**（keywords 是作者显式设计的高精度信号），语义只
  **补位**——guide `description` 向量与用户话术余弦 ≥ `SKILL_SEM_THRESHOLD`（默认 0.40）
  的补进剩余 top-K 空位。Embedding 经 llm-gateway `Embed`（与 registry 语义路由/memory
  同源）。**fail-open**：Embed 不可用/超时（`SKILL_EMBED_TIMEOUT`，默认 1.0s）→ 该轮纯词法
  + 30s 冷却，绝不堵规划。
- 个别 guide 若有跨进程假召证据，可声明 `semantic_min_score` 抬高**自己的**语义补位门槛；
  生效值为 `max(SKILL_SEM_THRESHOLD, semantic_min_score)`。值必须是 `[0,1]` 内有限数值。
- `description` 因此身兼**语义索引**：写成「判据 + 典型表面形态」能显著提升语义召回——
  这是索引优化，不是知识双写。

## 权威链（硬边界，skill 永远在软层）

```text
VAL / payment-gateway / Runtime Policy（context_scopes 过滤等）
  > Capability Manifest（require_confirm / permissions）
  > Plan Validator（_validated_steps）
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
```

确认、权限、隐私、行驶状态的最终执行权在硬层；prompt 层 policy 不承载安全语义。

## Schema（guide；policy 同形，few_shots/keywords 可省）

```yaml
name: charging-strategy         # 唯一 ID = 文件名
type: guide                     # guide | policy
description: 充电找桩与长途补能策略的分流判据（附近找桩补电/跨城怎么充电…）
                                # 常驻语义索引：词法 bigram 底分 + hybrid 语义预筛都用它
priority: 55                    # 检索同分时的定序
semantic_min_score: 0.50        # 可选；仅抬高本 guide 的语义补位门槛，词法命中不受影响
capability_dependencies:        # 可选；knowledge 直接/间接依赖的真实 intent
  [charging.find, charging.plan] #   每轮必须各自唯一映射到当前 opaque ref，否则整条不注入
keywords: [充电, 快充, 没电]     # 词法检索触发词（高精度显式信号，命中恒保留）
knowledge: |                    # 注入 planner 的领域判据（markdown，预算裁剪）
  **充电分流**……
few_shots:                      # 可选：渲染进注入块，紧跟 knowledge
  - user: 去惠州怎么充电
    plan: {"steps":[{"id":"s1","agent_id":"charging-planner","intent":"charging.plan",...}]}
plan_repairs:                    # 可选：软提示被忽略时的窄归一；只连接已存在的唯一两步
  - kind: dependency_slot_ref    #   不新增 intent、不覆盖真实槽值，多生产/消费步骤时不猜
    trigger_any: [第一个, 最便宜]
    producer_intent: nearby.search
    consumer_intent: nearby.order
    slot: poi_id
    source_path: data.items.0.id #   运行时拼成 <producer_step_id>.data.items.0.id
owner: charging-planner         # 治理归属；跨域知识用 orchestrator
version: 1
```

**运行时容错**：loader 对坏文件 fail-open——顶层非映射/YAML 坏 → 跳过；`priority: high` 等
非法标量 → 回默认并告警；非法 `semantic_min_score` → 回全局语义门槛并告警；**重名先到者胜**；
目录与 type 不一致 → 按 type 生效并告警；热更新把文件改坏 → **沿用上一版好文档
（last-known-good）**，删除文件才下线。运行时保知识可用性——未知顶层键告警不拒载，静默忽略
会让作者以为知识生效了。

**能力感知渲染**：`knowledge` 不拥有调用权。声明在 `capability_dependencies` 中的语义
intent 只有在本请求 catalog 中存在**唯一**映射时才会被替换为对应 opaque
`capability_ref`；任一依赖缺席或出现多 owner 歧义，整条文档本轮不注入，并在
`plan.skills` 记 `!capability-blocked`。这样能力裁剪不会出现「catalog 已删除能力，
Skill 正文却继续教模型调用它」。

`plan_repairs` 不是新的安全权威，也不是 route hint：它不能创建或改选 intent，只能在该
guide **实际注入且未被 `!clipped`**、`trigger_any` 命中、生产/消费步骤各唯一、目标槽无
真实值与引用时补 `slot_refs + depends_on`。实际作用写入 `plan.skill_effects`；因此能分开
「模型原生接对」和「skill 归一后接对」。确认、权限、能力存在性仍由硬层裁决。

**「目标槽无真实值」的两条细则**：

1. **非空 token 不等于引用。** 只有指向已声明 producer 的 `<producer_id>.data.*` 才算有效
   引用，其余是模型的格式噪声。
2. **触发词被原样抄进槽位，不算用户给的真值**。`trigger_any` 列的正是「指向一件还不知道
   名字的东西」的说法（`招牌` / `最便宜` / `第一个`），它们只有等 producer 回来才有具体值。
   > **判据：声明已经说了这个 token 意味着「值在别处」，就不能反过来拿它当值。** 只认**全等**
   > ——`招牌牛肉面` 是用户点名的具体商品，仍不许被改写成 `items.0.name`。归一时会一并清掉
   > 该占位符，免得执行期两个来源争同一个槽。

## 归因与运行行为

- **obs 归因**：`plan.skills` 名单（cloud.planning span / obs.turn）契约——guide 记
  `mode:name@通道:分数`（`@lex:23` 词法分 / `@vec:0.52` 余弦），**超预算被裁记 `!clipped`**，
  能力依赖不齐记 `!capability-blocked`（名单绝不谎称已注入）；policy 记 `mode:name`。
  badcase 先看名单：知识没进上下文（没检回/被裁/被能力面阻断）还是进了没用对。
- 热更新：文件加载 + mtime。`SKILLS_MODE`/`SKILLS_RETRIEVAL`/阈值超时每轮实时读；
  `SKILL_BUDGET`/`SKILL_TOP_K`/`SKILL_MIN_SCORE` 重启生效。env 全表见 `.env.example`。
  compose 把 `skills/` 只读挂载进 cloud-planner——投文件后 30s 内生效，不需要重建镜像。
- **T2 再规划继承**：`replan()` 按 `plan.skills` 名单重渲染同一份知识注入
  （条件依赖类知识的决策恰好发生在再规划轮，只注初规划等于知识白教）。**跨挂起同样成立**：
  `plan.skills` 随 `pending_plan` 持久化，补槽/确认恢复后的再规划不失忆。

## 目录现状

- `guides/`（9）：`multi-day-trip`、`navigation-with-stop`、`conditional-reminder`、
  `charging-strategy`、`weather-outing`、`merchant-ordering`、`shop-order-flow`、
  `nearby-detail-flow`、`manual-help-boundary`；
- `policies/`（3）：`freshness-and-depth`、`implicit-vehicle-control`、`negation-and-deferral`；
- `exemplars/`（23 域）：落域范例库（见该目录 README）。
