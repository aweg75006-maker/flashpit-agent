# skills/exemplars/ — 落域范例库

> **本文件是范例库契约的唯一真相源**（同 `skills/README.md` 之于 skill 层）。
> 实现 `orchestrator/cloud/exemplars.py`。

## 它解决什么

修一个落域 badcase 的标准产物是**数据**而非正则：一条 `话术 → 正确落域` 的记录，被检索后
作为 few-shot 进 planner prompt，**不做任何硬路由**。

| | route_hints | exemplars |
|---|---|---|
| 作用点 | LLM **之后** | LLM **之前**（prompt 内） |
| 作用方式 | 硬改写计划（replace/append） | few-shot 参考，模型仍可自行决定 |
| 写错的后果 | **事故**（模型判对了也被踩掉） | **噪声**（占了预算，仅此而已） |

所以它是权威链的**最软层**：

```text
VAL / payment-gateway / Runtime Policy
  > Capability Manifest（require_confirm / permissions）
  > Plan Validator
  > PlannerPolicyPack（软）
  > PlanningGuide（软）
  > Exemplar（最软——只是「别人这么说过，当时是这么落的」）
```

一个 badcase 三选一的判据：**路由错**（教科书形态、模型该会却总不会）→ route_hint；
**知识缺**（该拆没拆、该串没串）→ skill guide；**说法没见过**（同一件事换个说法就
落错）→ **exemplar**。默认选 exemplar——它是唯一一个写错了不会伤人的选项。

## 目录与 Schema

```text
skills/exemplars/<domain>.yaml      # domain = intent 的域，如 nearby / navigation
```

```yaml
domain: nearby                      # 必须等于文件名
exemplars:
  - text: 附近有什么咖啡店            # 用户原话（脱敏后）
    plan:                           # 正确落域。intent 必填且必须真实存在
      - agent: nearby               #   agent 可省；首步通常属于文件 domain（受控例外见下）
        intent: nearby.search
        slots: {keyword: 咖啡店}     #   槽位骨架，可省（manifest 导入的一律无槽）
    source: trace                   # manifest | trace | manual（封闭集）
    added: 2026-07-29
    tags: [badcase]
    note: 被视觉 hint 劫持            # 可选，≤80 字
```

**只追加不插入**：`eid = <domain>#<1-based 序号>` 是 obs 归因（`plan.exemplars`）里的
标识，插队会让归因指向别的条目。写入路径一律「读 → 去重 → 追加」。

### 可信采集首步（受控跨域例外）

文件仍按最终业务域归档；通常首步 intent 的域必须等于文件 `domain`。唯一通用例外是：
首步只负责采集后续业务动作所需的可信对象，且来源链已经写进计划，而不是靠文字说明。

```yaml
domain: luckin
exemplars:
  - text: 瑞幸迪美店点一杯拿铁
    plan:
      - id: store
        agent: nearby
        intent: nearby.search
        slots: {keyword: 瑞幸咖啡 迪美店}
      - id: order
        agent: mcp-bridge
        intent: luckin.order
        slots: {item_query: 拿铁}
        depends_on: [store]
        slot_refs:
          store_name: store.data.items.0.name
          store_longitude: store.data.items.0.lng
          store_latitude: store.data.items.0.lat
    source: manual
```

例外必须同时满足：生产者和本域消费者都有显式 `id`；消费者的 `depends_on` 包含生产者；
`slot_refs` 非空且每个引用都以同一生产者 id 开头。renderer 会保留这三个字段进入 prompt。
少任一项仍按「首步跨域、疑似放错文件」失败；纯门店发现继续放 `nearby.yaml`。

### clarify 型

有一类 badcase 的正确产物不是「落到哪个域」而是「**先别落域**」——裸地名「华润大厦」、
裸城市名「上海」该反问而不是猜。`plan` 表达不了它，于是补了 `clarify`：

```yaml
domain: clarify
exemplars:
  - text: 帮我订一下
    clarify: 只说了订，没说订什么      # 与 plan **互斥且必居其一**；≤40 字
    source: manual
```

`clarify` 的值是**澄清的理由**，刻意**不是**澄清话术。因为「怎么表达这是澄清」在 toolcall
与文本两个输出通道里形状不同；范例只示范**两个通道共有的那一半**，具体形状交回给 prompt
里恒拼的澄清段。

#### ⚠ 这个域的风险面高于其它域

普通范例写错只是噪声（占预算，模型仍自行决定）；**clarify 型示范的是「不执行」，
它被检回到一个明确请求上会诱导误澄清——那是行为改变不是噪声**。因此：

- 投 clarify 范例前必须验证：对**宾语齐全的明确请求**，clarify 不得挤进词法 top-1；
- 本仓当前**没有**生产 clarify 范例——这是实测后的决定：词法通道检回的是共享实词
  （裸专名间 IDF-Dice 实测几乎全 0），而「信息不足」在检索空间里表现为「是完整句的
  子串」——**澄清型范例天然与它的「补全版」近重复**。这不是调阈值能解决的，是检索式
  知识表达不了「缺了什么」。机制保留待将来「歧义源于**多出来的词**」场景复用。

## 检索与注入

- **词法通道**：IDF 加权 Dice（bigram）——范例只有 5-15 字，裸 Dice 会被功能词 bigram
  支配；IDF 是语料自己长出来的权重，投一个文件即自动重算。
- **语义通道**（`EXEMPLARS_RETRIEVAL=hybrid`，默认）：query 与范例文本余弦，经
  llm-gateway `Embed`（与 skills/registry/memory 同源）。词法命中**恒保留**，语义只补位
  词法漏召的 paraphrase。**fail-open**：Embed 不可用 → 该轮纯词法 + 30s 冷却。
- **向量后台预热**：首次检索起后台任务分批填；预热未完成不影响可用性——已填的参与语义、
  没填的走词法。⚠️ 容器刚起的头几秒语义通道只是部分可用；短命进程须先
  `await store.warm_blocking()`，否则 A/B 只测到词法档。
- **同域去重在选取时生效**：同一 domain 最多进 1 条。对照离对面太近就不是对照而是干扰
  （写对照范例时先问：它和对面差的是**判据**，还是只差一个词）。
- ⚠ **范例说法不要抄评测语料的原句**——把对抗语料的原句写成范例，`unseen_transfer`
  用例就变成了 `seen`，之后读「落域通过率涨了」就读不出泛化。同一件事换个说法写。
- **预算硬帽** `EXEMPLAR_BUDGET`（默认 700 字符）+ `EXEMPLAR_TOP_K`（默认 3）。
- 注入位置：**规划知识块之后、上下文之前**。块内抬头写明「仅供参考不是规则；与上方
  规划知识冲突时以规划知识为准」——位置与文案一起表达优先级，不靠模型揣摩。
- **T2 再规划 / 挂起恢复继承**：按 `plan.exemplars` 实际注入名单重渲染，不重新检索。

## 金标裁定：地盘冲突（台账 `boundaries.yaml`）

批量导入 manifest examples 时，**manifest examples 是「我这个能力能答这句」写出来的，
天然不判别化**——过期/重叠的地盘声明会被一起激活成「判定尺自相矛盾」。三条可复用的判断：

- 盘活死资产的同时也会把死资产里的错误一起激活；地盘搬家必须全局收口。
- 两个 capability 的描述重叠到分不开时，规则必然被拉来当裁判——修描述才是修根因。
  判别化的判据用**产出形态**，不用类目枚举。
- 为某条规则写的语料，它的 gold 就是那条规则的输出——「带着 hint 也答错」那一档
  **必须由人裁定**。

裁定台账见 `boundaries.yaml`：**只登记「判为两回事」；判为冲突的必须改金标。**
**改判（移域/删除）的记账**：范例只追加不插入，从中间删一条会让后续 eid 全部前移——
改判时在 commit 里写清移动了哪些 eid。

## ⚠ 启动期合成能力的域

`mcp-bridge` 的 `manifest.yaml` 写 `capabilities: []`——**这是有意的**，它的能力由
`servers.yaml` 准入清单在 bootstrap 时合成。`_known_intents` 同时读 `agents/*/servers.yaml`
与 manifests。**「能力从哪里声明」和「能力写在哪个文件」是两件事**——清单只认一种声明
形态时，另一种形态的域会安静地失去整层机制。再出现新的「能力不写在 manifest 里」的
Agent 形态时，记得同时喂这两处。

## env

| 变量 | 默认 | 说明 |
|---|---|---|
| `EXEMPLARS_MODE` | `full` | full=注入｜shadow=只检索记录不注入（A/B）｜off=关。每轮实时读 |
| `EXEMPLARS_RETRIEVAL` | `hybrid` | hybrid=词法∪语义补位｜lexical=纯词法（零网络） |
| `EXEMPLAR_LEX_THRESHOLD` | `0.34` | IDF-Dice 下限，钳 (0,1] |
| `EXEMPLAR_SEM_THRESHOLD` | `0.65` | 余弦下限，钳 [0,1]。比 skills 的 0.40 高是应该的——范例比「话术 vs 话术」同文体，余弦基线整体抬高 |
| `EXEMPLAR_TOP_K` | `3` | 单轮注入条数上限（重启生效） |
| `EXEMPLAR_BUDGET` | `700` | 注入块字符预算（重启生效） |
| `EXEMPLAR_EMBED_TIMEOUT` | `1.0` | 语义通道超时（秒） |
| `EXEMPLARS_DIR` | — | 覆盖范例根目录；缺省跟随 `SKILLS_DIR`/`<repo>/skills` 下的 `exemplars/` |

容器内 `skills/` 是只读挂载 → **投范例文件 30s 内生效**，不需要重建镜像。

## obs 归因

`cloud.planning` span 的 `exemplars` 属性，契约与 `skills` 对齐：
`<mode>:<eid>@lex:0.55` / `@vec:0.71`，超预算被裁加 `!clipped`。
badcase 先看这一行——**没检回 / 检回了没用对 / 检回了却被裁**是三种不同的失败。
