# I1 端云协同推理 Demo

方向 I1（端侧小模型 + 端云协同推理）的可运行 demo，对应 `docs/plan/plan.md` §3。

## 跑法

```bash
# 仓库根目录
cd /Users/zhang/Desktop/Gitrequest/cockpit-agent

# 规则版（M1，零模型依赖，必跑）
.venv/bin/python demos/i1_edge_inference/demo.py

# 断网场景（附加）
.venv/bin/python demos/i1_edge_inference/demo.py --offline

# 真模型版（M2，需先下载模型权重到 models/edge-intent/qwen2.5-0.5b-int4）
.venv/bin/python demos/i1_edge_inference/demo.py --model models/edge-intent/qwen2.5-0.5b-int4
```

## 文件

| 文件 | 作用 |
|---|---|
| `orchestrator/edge/edge_classifier.py` | M1：EdgeClassifier 接口 + RuleBasedEdgeClassifier + 端云路由 |
| `orchestrator/edge/onnx_classifier.py` | M2：ONNXEdgeClassifier（Qwen2.5 INT4 / ORT GenAI），超时降级规则 |
| `demos/i1_edge_inference/demo.py` | 演示主程序：18 条话术路由表 + VAL 真实执行 + 延迟统计 + 断网兜底 |

## 演示要点（3 分钟话术）

1. **痛点**：端侧现在是 1727 行正则，`空调凉一点`/`温度调低` 要各写一条规则，没有置信度、断网即瘫。
2. **架构**：EdgeClassifier 接口把"识别"和"路由"解耦——`edge_fast`（<50ms）/ `edge_answer` / `cloud`（800ms+）三层，`EscalationReason` 四种原因可观测。
3. **真数据**：18 条话术 10 条留端、7 条上云、1 条简答；VAL 状态真的变了（hvac_temp=22、volume=40）；端侧毫秒级 vs 云端 800ms。
4. **两个讲故事的点**：
   - `打开后备箱` → 危险动作上云二次确认（安全不变量，架构红线）。
   - `把车窗关上一半` → 规则只认成"关窗"丢了程度（规则局限，M2 模型的动机）。
5. **断网**：`--offline` 一跑，车控+简答照常，导航兜底提示。
6. **演进**：M2 模型换上（同一接口，零改动），`--model` 一跑，置信度和准确率对比，收尾。

## 模型权重下载

```bash
/opt/miniconda3/bin/pip install -U "huggingface_hub[cli]"
/opt/miniconda3/bin/huggingface-cli download \
  onnx-community/Qwen2.5-0.5B-Instruct-INT4 \
  --local-dir models/edge-intent/qwen2.5-0.5b-int4
```

下完目录里应有 `model.onnx`、`genai_config.json`、tokenizer 相关文件。
