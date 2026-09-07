# AGENTS.md — 接手者入口

> 先读本文件，再动代码。工程规则见 [`CLAUDE.md`](CLAUDE.md)；系统能力与界面预览见根 `README.md`。

## 1. 项目是什么

云边协同的智能座舱 multi-agent 系统。端侧快系统处理高频、安全敏感和离线能力；云侧
Planner 处理复杂、多域、多轮任务。Agent 统一使用 gRPC 契约 + Manifest，经 Registry 发现；
车控只经 VAL，LLM 只产意图/计划。

当前阶段是 **Phase 1 工程化 PoC**，仓库为成果形态：工程主干、云端中枢、真实 Provider、
语音回路、记忆、可观测与 14 个领域 Agent 均已落地并可用 `make up` 一键起栈。真实
CAN/SOME-IP、量产账号体系与完整隐私治理仍是明确边界。

## 2. 目录地图

| 想了解 | 入口 |
|---|---|
| 工程规则、目录、安全红线 | `CLAUDE.md` |
| 系统能力与界面预览 | `README.md` |
| 环境变量速查 | `.env.example` |
| 各服务说明 | 各服务子目录 README（改前先读） |

## 3. 不可违反的规则

### 3.1 运行环境

- 根目录 `.env` 是唯一运行时环境与密钥来源；不得复制、维护或依赖 `deploy/.env`。
- 本地真栈只用 `make up` 或根 `compose.yaml`；不得以 `deploy/docker-compose.yaml` 为首文件。
- 不停止其他 agent 正在使用的 Docker 或真栈进程。

### 3.2 架构安全

1. 车控只经 VAL；任何组件不得直接碰 CAN/SOME-IP。
2. LLM 不直连车控：Planner 产计划，确定性 Executor 经权限与 VAL 执行。
3. 危险动作必须二次确认；`require_confirm` 权威来自 capability manifest/受控配置，不信 LLM。
4. 新 Agent 经 Registry 发现；不得为加 Agent 修改 orchestrator 核心路由分支。
5. secret/token/password 不进代码、commit、日志或文档。
6. 改 proto 先改 `proto/`，再 `buf generate proto`；绝不手改 `gen/`。
7. `Capability.response_only` 是只响应能力的权威；D0/T2/Executor 都必须 fail closed。
8. 安全问句的权威文本是服务端 `safety_origin_text`；LLM goal/reason 和补槽短句无授权权威。

## 4. 改完怎么验证

- 改 proto 后：`make proto`；
- 端侧/单服务改动：先跑该服务目录 README 说明的验证方式；
- 全栈改动：`make up` 起栈后按 `README.md` 的入口做真栈冒烟。

## 5. 常见工程任务

### 5.1 新增 Agent

1. 新建 `agents/<name>/manifest.yaml`、源码、README；
2. 遵守 `proto/cockpit/agent/v1/agent.proto`；
3. 注册服务，不改 orchestrator 核心分支；
4. 加 capability 契约、权限、确认与 provenance 声明。

详细流程见 `CLAUDE.md`。

### 5.2 新增端侧车控能力

1. 改 `orchestrator/edge/knowledge/commands.yaml`；
2. 明确对象、operate、权限、`require_confirm`、drive/voice 限制；
3. 让生成器派生意图，不手写第二份集合；
4. 确认规则产出的命令能通过 VAL，不只验证名字存在。

### 5.3 改 proto / manifest

- proto 先改真相源，再 codegen；generated 文件 gitignore，不手改、不 force-add；
- manifest 新字段要检查 YAML loader、Registry 持久化 round-trip、Step 装配、挂起恢复和执行出口；
- 可选 JSON null 在 map<string,string> 边界视为“未提供”，不得转成 `"None"`。

## 6. 协作与文档

- 默认中文；结论先行，代码/命令/变量用英文。
- 变更前读规则；大改先给方案，用户确认后实施。
- 修改后主动验证，不能用注释、skip 或宽松断言掩盖失败。
- 工作树可能有别人改动；只碰本任务文件，禁止 `git reset --hard`、rebase、force-push。
- 删除文件/目录、改 `.env`/密钥、数据库迁移、push、生产部署都要人工授权。
- 文档中的“今天/最近”只用于引用原始用户话术；状态一律写绝对日期。
