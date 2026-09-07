# 启动与运维手册（Runbook）

> 用途：下次开机直接照此启动。**唯一入口：根 `compose.yaml`；唯一运行时环境：根 `.env`。**
> 快照日期：2026-09-07。compose 定义 32 个服务（prometheus/grafana 由 `--profile observability`
> 门控，默认不参与 `up`），当前实际运行 19 个（§2 标注「运行中」）。

## 0. 速查

| 想做什么 | 命令 |
|---|---|
| 新 clone 首次准备 | `cp .env.example .env`（不配任何密钥也能跑：LLM 落 MockProvider、外部数据源走 mock） |
| 首次 / 改 proto 后 | `make proto` |
| 全量起栈（默认 30 服务，含构建） | `make up`（= `docker compose -f compose.yaml up --build -d`；prometheus/grafana 需另加 `--profile observability`，见 §3.4） |
| 只起当前常用 19 服务 | 见 §3.2（推荐日常） |
| 只重建并启动某个服务 | `docker compose -f compose.yaml up -d --build hmi` |
| 看当前状态 | `docker compose -f compose.yaml ps` |
| 跟踪日志 | `make logs` / `docker compose -f compose.yaml logs -f <service>` |
| 停全栈 | `make down` |

## 1. 前置与入口约定

- 依赖：Docker Desktop、Go 1.24+、Node 20+（buf 仅改 proto 时需要）。
- 根 `compose.yaml` 是唯一 Docker Compose 入口：它 `include` 了 `deploy/docker-compose.yaml`，
  并**显式加载根 `.env`**。
- **禁止**直接用 `deploy/docker-compose.yaml` 或维护 `deploy/.env`——会丢失根 `.env`，
  真实 Provider 会静默回退 mock。
- 根 `.env` 是唯一运行时环境与密钥来源；`make up` 已封装上述约定。

## 2. 服务全景与当前状态

| 分类 | 服务 | 端口 | 当前状态 |
|---|---|---|---|
| 基础 | redis | 6379 | 运行中 |
| 基础 | nats | 4222 | 运行中（healthy） |
| 基础 | postgres（pgvector） | 5432 | 运行中（healthy） |
| 基础 | http-proxy（出站白名单代理） | 8082→8080 | 运行中 |
| 中枢 | registry（Agent 注册/语义路由） | 50051 | 运行中（healthy） |
| 中枢 | llm-gateway（LLM/Embedding/ASR/TTS 出口） | 50052 / 50059 | 运行中 |
| 中枢 | memory（pgvector 语义记忆） | 50053 | 运行中 |
| 中枢 | cloud-planner（T1 DAG / T2 循环） | 50054 | 运行中 |
| 中枢 | cloud-gateway | 8080 | 运行中 |
| 网关/编排 | edge-gateway | 8090 | 运行中 |
| 网关/编排 | edge-orchestrator（FastIntent + VAL） | 50070 | 运行中 |
| Agent | navigation / nearby / info / chitchat / trip-planner | — | 运行中 |
| 可观测 | observability-collector | 8092 | 运行中 |
| 可观测 | dashboard | 5174 | 运行中 |
| 前端 | hmi | 5173 | 运行中 |
| Agent | parking-payment / manual-rag / deep-research / reminder / charging-planner / scene-orchestrator / road-safety / vision / mcp-bridge | — | 未启动 |
| 业务附加 | payment-gateway | 50071 | 未启动 |
| 业务附加 | proactive（统一主动引擎） | — | 未启动 |
| 可观测 | prometheus / grafana（`profiles: [observability]`） | 9090 / 3000 | 未启动（需 `--profile observability`） |

> obs-data 是 collector 的持久化命名卷，不是容器。README「30 个服务一键起栈」即默认
> `up` 的 30 个服务；prometheus/grafana 由 profiles 门控，故 compose 共定义 32 个服务。

## 3. 常用启动命令

### 3.1 全量起栈（默认 30 服务，写作用参考，日常不必全起）

```bash
make up
# 等价于：
docker compose -f compose.yaml up --build -d
# 注意：prometheus/grafana 由 profiles 门控，默认不会随 up 启动，见 §3.4
```

### 3.2 只启动当前常用服务（19 个，推荐日常）

```bash
docker compose -f compose.yaml up -d \
  redis nats postgres http-proxy \
  registry llm-gateway memory cloud-planner cloud-gateway \
  edge-gateway edge-orchestrator \
  navigation-agent nearby-agent info-agent chitchat-agent trip-planner-agent \
  observability-collector dashboard hmi
```

### 3.3 单服务（改代码后重建 / 按需拉起）

```bash
# HMI 前端改过代码后单独重建
docker compose -f compose.yaml up -d --build hmi

# 体验手册 RAG（默认 mock 语料可直接起；要真实手册索引先配 .env，见 §5）
docker compose -f compose.yaml up -d manual-rag-agent

# 深度调研 Agent
docker compose -f compose.yaml up -d deep-research-agent
```

### 3.4 按需补齐

```bash
# 指标观测栈（Prometheus + Grafana）——profiles 门控，必须带 --profile observability
docker compose -f compose.yaml --profile observability up -d prometheus grafana

# 支付网关（默认 mock 渠道，防 mock 金额走真渠道，fail-closed）
docker compose -f compose.yaml up -d payment-gateway

# 统一主动引擎
docker compose -f compose.yaml up -d proactive
```

## 4. 状态 / 日志 / 停止

```bash
# 状态
docker compose -f compose.yaml ps

# 单服务日志
docker compose -f compose.yaml logs -f <service>

# 全栈日志
make logs

# 重启 / 停止 / 启动单个服务
docker compose -f compose.yaml restart <service>
docker compose -f compose.yaml stop <service>
docker compose -f compose.yaml start <service>

# 停全栈（保留数据卷）
make down
```

## 5. 注意事项

- **根 `.env` 是唯一运行时环境与密钥来源**；不得复制、维护或依赖 `deploy/.env`。
- 不停止其他 agent 正在使用的 Docker 或真栈进程。
- 改 proto：先改 `proto/`，再 `make proto`，然后重建受影响服务。
- **manual-rag 默认 mock**（5 条演示语料）。要真实手册索引：配置根 `.env` 的
  `KNOWLEDGE_VENDOR=local`、`MANUAL_INDEX_PATH`、`KNOWLEDGE_VEHICLE_MODEL`，把 `.mrag`
  包放入 `models/manual_rag/` 后 **重建镜像**（Dockerfile 是 `COPY` 不是运行时挂载）：
  `docker compose -f compose.yaml up -d --build manual-rag-agent`。
- **payment-gateway 默认 mock**：`PAYMENT_VENDOR=mock`、`PAYMENT_REAL_SCENES` 默认空
  （fail-closed：防 mock 数据金额走真渠道收真钱）。
- 起栈后访问：HMI <http://localhost:5173>、可观测台 <http://localhost:5174>。
