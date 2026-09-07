# manual-rag Agent (ecosystem / first_party)

车书助手：从**指定车型的真实用户手册**检索可核验文本与图片，再由 LLM 仅依据片段生成
简短回答。v2 同时处理无标点操作方法问句、受控图标俗称和同页原图；mock 只保留给 CI
和无私有手册资产的离线开发。

| intent | 说明 |
|---|---|
| `manual.query` | 胎压、保养、充电、功能操作、应急处置等车型手册问答 |

## 运行链路

```text
PDF + resources/visual_assets.yaml
    -> 离线建库工具 -> models/manual_rag/*.mrag
    -> ManualIndexRetriever -> 中文 n-gram BM25 + 章节/短语/覆盖率重排
       + 受控视觉 caption/aliases 精确匹配
    -> Chunk(source_type=manual, section_path, PDF page, vehicle_model, images)
    -> grounded prompt / 视觉目录确定性回答 -> speech + 图文 manual card
```

确定性护栏：

- 索引绑定源 PDF SHA-256、车型、手册版本和内容 SHA-256，并须与 tracked
  `resources/manual_catalog.yaml` 的人工批准指纹一致；任一不一致即启动失败；
- 显式 real 配置缺文件/损坏/错车型时 fail-fast，绝不回 mock；
- 未出现的显著 Latin 产品名或多词协议名（如 `CarPlay`、`Android Auto`）零命中；
- 低相关查询零命中且不调 LLM；
- 真实手册答案里的带单位/小数数值必须能在本轮引用片段核对，否则整段弃权；
- 安全告警继续由 `runtime/safety_signal.py` 的确定性分级建议前置；
- 卡片带章节、PDF 页码、车型、源/内容 hash 和 `_prov.mode=real`。
- `.mrag` 内每个图片 blob 与视觉 manifest 均有 SHA-256；启动期全量校验，运行期读图复验；
- 卡片最多 2 张、单图 640 KiB、总计 768 KiB，只允许 PNG/JPEG；图片不进入 LLM prompt；
- “背宝剑小人”等俗称只接受 `visual_assets.yaml` 的人工审定映射，未知描述不模糊猜测；
- `runtime.question_shape` 保证“雨刮器怎么打开”等无标点方法问句不执行；PlanningGuide/
  exemplar 负责泛化，manifest route hint 只兜生产已复现的高风险窄句形。

## 私有索引资产

源 PDF、抽取正文与图片不进入 Git。生成包放 `models/manual_rag/`，该目录只跟踪说明和
`.gitkeep`，包体全部 ignored。在线镜像不安装 PDF 解析器；`pypdf`/`PyYAML` 只属于离线建库
（见 `requirements-ingest.txt`）。`.mrag` 包由**离线构建脚本**从源 PDF 生成（`--pdf <PDF> --output <pkg> --expected-sha256 <hash>`）；
默认拒绝覆盖已有包，确认输入后才使用 `--force`。相同 PDF、视觉目录与参数的输出应逐字节
相同。

当前本机使用的输入基线：

| 字段 | 值 |
|---|---|
| document | `SU7用户手册` |
| vehicle_model | `xiaomi-su7-2024` |
| revision | `2024-04-15` |
| PDF pages | 278 |
| output | `models/manual_rag/xiaomi-su7-2024.v2.mrag` |
| visual coverage | 350 个图片放置 / 299 个去重 blob；17 个明确跳过 |

> 17 个 skipped 中 7 个为 pypdf 无法解码的 LZW，10 个为超过受控像素上限的
> FlateDecode 大图；它们不会被伪装成已支持图片。

## Provider 配置

```text
KNOWLEDGE_VENDOR=local
MANUAL_INDEX_PATH=/app/models/manual_rag/xiaomi-su7-2024.v2.mrag
KNOWLEDGE_VEHICLE_MODEL=xiaomi-su7-2024
```

- `KNOWLEDGE_VENDOR=mock`：仅 CI/离线演示；
- `local|manual|file`：真实只读包，缺失或任一文本/视觉 hash 校验失败即启动失败；旧
  `.json.gz` 兼容读取但 `images=[]`；
- `pgvector`：仍未实现，显式选择会 fail-fast；当多车型规模或真实 badcase 证明词法召回
  达到上限时，再以现有 retrieval corpus 为 A/B 尺子迁移。

根 `.env` 是唯一运行时配置源；不要在本目录复制 `.env`。

## 验证

```powershell
python -X utf8 -m pytest -q agents/manual_rag  --import-mode=importlib
```

真实评测必须同时核对 top 页和关键正文；“页号碰对”不算通过。retrieval 与整本覆盖评估为
离线流程，语料见 `resources/retrieval.yaml`。
