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

## .mrag 是什么

`.mrag` 不是 RAG 领域通用的标准格式，而是本项目为「Manual RAG 专用索引包」自定义的一种
私有数据格式。可以粗略理解成：

> `.mrag` ≈ 为车主手册 RAG 定制的、打包好的知识库文件。

通用 RAG 学习路径中更常见的是：

```text
PDF → 文本切分 Chunk → Embedding → Vector DB（FAISS / Milvus / Qdrant / pgvector）
```

因此通用 RAG 教程里一般不会出现 `.mrag`。

### 与普通 RAG 的区别：车主手册是强结构文档

普通 RAG 最核心的链路是：

```text
Document → Chunk → Embedding → Vector Index → Retriever
```

但汽车车主手册有一个特殊问题：它不是普通的纯文本知识库，而是「章节 + 页码 + 图片 +
图注 + 车型」的强结构文档。例如用户问「仪表盘上这个黄色的小乌龟是什么意思？」，单纯把
PDF 转成 `chunk_001 ... chunk_002 ... chunk_003 ...` 其实不够——系统最好知道：

- 车型：xiaomi-su7-2024
- 章节：驾驶
- 页码：PDF 第 156 页
- 原始图片：dashboard_warning_023.jpg
- 图片说明：黄色乌龟图标
- 正文：动力系统受限……

于是本项目自定义了一个索引容器，把这些信息封装在一起：

```text
.mrag
├── 车型信息 / 手册版本 / 文档 SHA-256（校验信息）
├── 章节结构
│   ├── 第 1 章
│   ├── 第 2 章
│   └── ...
├── 文本索引（BM25 / 短语 / semantic 预留）
├── 图片 / visual assets
│   ├── 图片 blob
│   ├── caption
│   └── PDF 页码
└── 车型绑定 / manifest / integrity
```

> 当前 `local` 实现为中文 n-gram BM25 + 章节/短语/覆盖率重排（纯词法、离线确定）；
> semantic 槽位为后续 pgvector 迁移预留——显式选择 `pgvector` 会 fail-fast（见 Provider 配置）。

所以它不是一种新的 RAG 算法。更准确地说：`.mrag` 是这个项目为了把「车主手册 RAG 所
需要的数据和索引」封装在一起而定义的一种私有数据格式。

### 用 .faiss 类比最好理解

常见向量索引目录：

```text
documents/
    xxx.txt
    index.faiss
    index.pkl
```

其中 `index.faiss` 不是 FAISS 算法本身，而是 FAISS 把构建好的索引保存下来的文件。
`.mrag` 是同一类概念——不是「业界有一种叫 MRAG 的 RAG 技术，文件扩展名就是 .mrag」，
而是「这个项目把 Manual RAG 的检索数据、文档结构、图片和元数据打包成了一个 .mrag 文件」。

### 为什么不用 Milvus / pgvector？

普通企业知识库「用户问题 → Embedding → Vector DB → Top-K chunks → LLM」完全够用；但
车主手册属于强约束知识库——用户问「我的蔚来 ET5 后备箱怎么打开」，系统不能因为「小鹏
P7 后备箱怎么打开」语义相似，就把小鹏的内容拿过来。因此本项目特别强调：

```text
车型绑定 + 章节 + 页码 + 关键词/短语 + 相关性阈值 + 视觉内容 + fail-closed
```

检索链是：

```text
用户问题
   ↓
Manual Retriever
   ↓
章节匹配 │ 文本检索 │ 图片检索
   ↓
相关性判断
   ↓
足够相关 → LLM 回答
不相关   → 拒答（零命中弃权）
```

这已经不只是教程里的「向量数据库 + Top-K + Prompt」，而是一个面向特定领域的 RAG
Retrieval Layer。

### 一句话定位：.mrag ≠ 向量数据库

| 概念 | 作用 |
|---|---|
| PDF | 原始知识 |
| Chunk | 切分后的知识片段 |
| Embedding | 文本的向量表示 |
| FAISS / Milvus / pgvector | 存储 / 检索索引 |
| Retriever | 检索逻辑 |
| `.mrag` | 本项目定义的 Manual-RAG 数据 / 索引打包格式 |

`.mrag` 内部可以使用 BM25、向量索引、章节索引、图片索引等各种底层机制，但这些底层
东西对外统一封装成一个文件（如 `xiaomi-su7-2024.v2.mrag`），运行时由
`ManualIndexRetriever` 直接加载：

```text
ManualIndexRetriever
   ↓
xxx.mrag
```

### 为什么通用 RAG 学习中没有它

通用 RAG 关注「怎么把 PDF 做成向量数据库」；本项目做的是 Domain-specific RAG /
Document-grounded Agent——关注的已经不是「怎么把 PDF 做成向量数据库」，而是「如何让
一个车载 Agent 只根据指定车型的官方手册回答、还能引用原始图文、遇到错车型/低相关问题
直接拒绝回答」。这是两个层次：

```text
通用 RAG： PDF → Chunk → Embedding → Vector DB
本项目：   PDF → 章节/文本/图片/页码/车型/校验 → Manual Index → .mrag → Manual RAG Retriever
```

`.mrag` 本质上是「知识库运行时资产」，不是一种必须学习的新 RAG 技术。这种「确定性
兜底 + 强约束检索 + 拒绝通道」的设计，与 Agent Harness 的工具路由和 fail-closed 一脉
相承——为什么没有简单使用 Vector DB、而是自己做 `.mrag` + `ManualIndexRetriever`，
值得沿着 Harness 的思路继续研究。

## Manual RAG 完整流程

> 本文是「运行链路」的详细展开：从 PDF 到 `.mrag` 的离线建库，到用户问题触发检索与
> 防编造的完整链路。示例均来自本仓真实配置（`resources/retrieval.yaml`、
> `resources/visual_assets.yaml`）。

### 一、核心定位

这个 RAG 不是「搜点什么喂给 LLM 让答案更聪明」，而是「知识供给 + 防编造」：

- 只允许引用真实手册内容；
- 数值必须能在引用片段核对；
- 零命中不调 LLM；
- 未知产品名（CarPlay）零命中。

### 二、离线建库流程

```text
输入：PDF 用户手册 + visual_assets.yaml（人工审定的图标目录）
   ↓
1. PDF 提取（pypdf）
   ├─ 逐页提取正文 → ExtractedPage(page_number, section_path, content)
   └─ 逐页提取图片 → ExtractedVisualAsset(asset_id, page, media_type, width, height, bbox, data)
   ↓
2. 视觉资产绑定（visual_assets.yaml）
   ├─ table_pages: 按页面位置从上到下绑定图标与 caption
   │   如 191 页: [动力电池电量低指示灯, 乌龟灯(功率受限灯), 电子稳定系统故障指示灯, …]
   ├─ asset_overrides: 人工审定的别名映射（如 page 95 前风挡雨刮拨杆操作示意）
   └─ aliases: 用户俗称 → 正式 caption（如「小人背着宝剑」→ 安全带未系提醒指示灯）
   ↓
3. 构建索引 bundle（index_format.py）
   ├─ build_index_bundle() → 文本索引
   │   ├─ 每页一个 chunk: {chunk_id, page_start, section_path, content, chunk_sha256}
   │   └─ document 元数据: {document_id, title, publisher, vehicle_model,
   │                        revision, source_sha256, content_sha256}
   └─ build_visual_manifest() → 视觉索引
       ├─ 每张图: {asset_id, page_start, media_type, width, height, bbox,
       │           caption, aliases, description, role, blob_sha256, blob_path}
       └─ skipped_assets: 无法解码/超限的图片记录
   ↓
4. 写入 .mrag 包（ZIP 格式）
   ├─ index.json           → 文本索引（gzip 压缩）
   ├─ visual-assets.json   → 视觉 manifest
   └─ assets/{sha256}.jpg|png → 图片 blob（去重）

   输出：models/manual_rag/xiaomi-su7-2024.v2.mrag
```

> 视觉绑定按页面位置逐一校验：图片数与 labels 数不一致即失败，防止图标错行。

### 三、运行时触发流程

```text
用户问：「胎压多少正常？」
   ↓
1. 路由匹配（manifest.yaml route_hints）
   ├─ 正则匹配: 胎压/轮胎气压…多少/合适
   ├─ guard 排除: 帮我/现在/马上 + 设置/调到（执行类让回主链）
   └─ 命中 → intent: manual.query
   ↓
2. ManualRagAgent.handle()
   ├─ 安全信号检测: alert_level(question)
   └─ 检索: self.kb.retrieve(question, vehicle_model)
   ↓
3. ManualIndexRetriever.retrieve() — 检索核心
   ├─ 3.1 未知 ASCII 词检查
   │   CarPlay、Android Auto 等专名若手册里没出现过 → 直接返回 []（零命中）
   ├─ 3.2 查询变体生成
   │   ├─ 去噪: 「请问一下胎压多少正常」→「胎压 多少 正常」
   │   ├─ 同义词: 「胎压」→ [轮胎压力, 充气压力]
   │   └─ 意图扩展: 含「多少/标准/推荐/合适/参数」且非报警/故障问法 → 补「参数」
   ├─ 3.3 BM25 召回 + 重排
   │   ├─ 中文双字 n-gram 分词
   │   ├─ BM25 评分: body_score + 1.8 × section_score
   │   ├─ 短语匹配加分: 连续 3 字以上命中 +1~6 分
   │   └─ 覆盖率: matched_weight / total_weight < 0.42 → 丢弃
   ├─ 3.4 视觉资产匹配
   │   ├─ 别名匹配: 「小人背着宝剑」→ 安全带未系提醒指示灯
   │   ├─ caption 匹配: 需视觉上下文词（图标/指示灯/仪表/灯亮/亮了）
   │   └─ 匹配成功 → 该页 quality 提升到 24+
   └─ 3.5 返回 top_k=4 个 Chunk
       每个 Chunk 包含: content, source, score, source_type, document_id,
       vehicle_model, page_start, section_path, images
   ↓
4. 防编造决策树
   ├─ 零命中 → 「手册里没有查到，建议联系客服」（不调 LLM，安全信号仍给处置建议）
   ├─ 有命中 + 安全信号 + 非权威来源 → 「具体数值请以车辆铭牌或随车手册为准」
   │   （不进 LLM，避免把演示数值说成权威值）
   ├─ 有命中 + 视觉目录匹配 → 确定性转述
   │   「根据《SU7用户手册》的图标目录，这是「安全带未系提醒指示灯」…」
   │   （不经过 LLM，直接用人工审定的 description）
   └─ 有命中 + 需要 LLM 生成
       ├─ 构建 messages: system=只依据【参考资料】回答；user=【参考资料】+【问题】
       ├─ 调用 LLM（temperature=0.2, max_tokens=200）
       └─ 数值核对: 提取 LLM 答案中的带单位数值 → 在引用片段中核对 → 找不到整段弃权
```

### 四、技术栈

| 组件 | 技术 |
|---|---|
| 离线 PDF 解析 | pypdf 6.9.2（仅离线建库，见 `requirements-ingest.txt`） |
| 索引格式 | `.mrag` = ZIP(index.json + visual-assets.json + assets/) |
| 文本索引 | gzip JSON，每页一个 chunk |
| 检索算法 | 中文 n-gram BM25 + 章节/短语/覆盖率重排 |
| 视觉匹配 | 人工审定的 alias/caption 精确匹配 |
| 运行时依赖 | Python stdlib + PyYAML（无 PDF 解析器） |
| 防编造 | 零命中不调 LLM + 数值核对 + 来源类型标记 |

### 五、关键数据结构

```text
.mrag（ZIP 包）
├── index.json            # 文本索引
│   ├── schema_version: 1
│   ├── document: {document_id, title, publisher, vehicle_model,
│   │              revision, source_sha256, content_sha256}
│   └── chunks: [{chunk_id, page_start, page_end, section_path,
│                 content, chunk_sha256}, ...]
├── visual-assets.json    # 视觉 manifest
│   ├── assets: [{asset_id, page_start, media_type, width, height,
│   │             bbox, caption, aliases, description, role,
│   │             blob_sha256, blob_path, byte_length}, ...]
│   └── skipped_assets: [{page_start, xobject_name, reason}, ...]
└── assets/
    ├── {sha256}.jpg      # 图片 blob（去重）
    └── {sha256}.png
```

### 六、防编造机制总结

| 层级 | 机制 | 代码位置 |
|---|---|---|
| 1. 零命中不调 LLM | 检索无结果 → 直接弃权 | agent.py:247-253 |
| 2. 未知专名零命中 | CarPlay 等手册没出现的词 → 零命中 | local_index.py:380-406 |
| 3. 低相关零命中 | coverage < 0.42 → 丢弃 | local_index.py:481 |
| 4. 来源类型标记 | manual/web/mock 随资料传递 | base.py:31-35 |
| 5. 安全信号 + 非权威 | 有告警但无真实手册 → 不进 LLM | agent.py:265-270 |
| 6. 视觉目录确定性回答 | 人工审定的 description 直接转述 | agent.py:275-297 |
| 7. 数值核对 | LLM 答案中的数值必须能在引用片段找到 | agent.py:99-124 |
| 8. catalog 指纹校验 | 索引 hash 必须与人工批准的 catalog 一致 | local_index.py:122-151 |

核心设计理念：**这个 RAG 的 LLM 只负责「摘要」，不负责「知识」**。知识来自真实手册，
LLM 只是把检索到的内容组织成口语化回答——离线建库（PDF → 逐页提取 → 人工审定视觉目录
→ .mrag 包）保证知识真实，运行时（BM25 + 同义词/意图扩展 + 视觉 alias 精确匹配）保证
召回可控，8 层确定性护栏保证不编造。

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
