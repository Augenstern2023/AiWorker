# Sub2api GPT-5.6 Sol 自动推理路由设计

## 背景与目标

当前运行时 `.env` 将主 LLM 指向云雾的 OpenAI-compatible 接口，并配置 OpenCode 为备用服务商，因此覆盖了代码中已有的 Sub2api 默认值。云雾接口返回 403 后，编剧大师等共用 `analysis.llm` 的工作流无法稳定响应。

本次变更需要：

- 删除云雾与 OpenCode 运行时线路，所有文本 LLM 请求只使用 Sub2api。
- 将默认模型升级为 `gpt-5.6-sol`。
- 根据任务复杂度自动选择推理强度，同时允许运维通过显式配置覆盖自动选择。
- 不复制、打印或提交 API 密钥，继续从 `SUB2API_KEY` 环境变量读取凭据。
- 不改变 ASR、Embedding 或其他非文本 LLM 服务。

## 配置设计

全局 LLM 配置与 `sub2api` provider 默认值保持一致：

| 配置 | 值 | 说明 |
| --- | --- | --- |
| `LLM_PROVIDER` | `sub2api` | 唯一文本 LLM 服务商 |
| `LLM_ENV_KEY` | `SUB2API_KEY` | 凭据只从环境读取 |
| `LLM_BASE_URL` | 必须显式配置 | 只接受 HTTPS 地址或本机加密隧道的 loopback 地址 |
| `LLM_MODEL` | `gpt-5.6-sol` | 固定目标模型 |
| `LLM_WIRE_API` | `responses` | 优先使用 Responses API |
| `LLM_REASONING_EFFORT` | `auto` | 启用任务级自动路由 |

代码和运行时配置不再暴露 fallback model 或 `LLM_SECONDARY_*` 入口。RAG 文本生成继续继承全局 LLM 配置；ASR 和 Embedding 保持现状。公网明文 HTTP 会在发送 Authorization header 前被拒绝。

## 自动推理强度路由

在 `analysis.llm` 中增加纯函数路由器。路由器接收本次请求的消息列表，只检查任务文本和文本规模，不调用外部模型，因此不会增加一次计费请求。

路由以 `medium` 为基础档，结合任务意图和输入规模选择：

| 强度 | 任务类型 |
| --- | --- |
| `low` | 抽取、分类、格式转换、短文本修复等边界明确的任务 |
| `medium` | 普通问答、摘要、常规内容分析和一般 JSON 生成 |
| `high` | 人物设计、故事梗概、分集规划、剧本生成等多约束创作任务 |
| `xhigh` | 剧本审核、剧本优化、长文本综合分析等需要交叉核验的任务 |
| `max` | 同时满足高难审核或深度优化意图与长上下文条件的质量优先任务 |

任务意图由集中维护的中英文关键词集合识别，输入规模由所有消息正文的字符总量衡量。`max` 不会只因文本很长而启用，必须同时存在高难任务信号，避免不必要的延迟与成本。

当 `LLM_REASONING_EFFORT` 是官方支持的显式值时，直接使用该值并跳过自动路由；`auto` 才按请求计算。无效配置在创建 provider 时回落到 `auto`，避免把不支持的值发送给网关。

## 请求协议与兼容回退

Sub2api 请求优先发送到同一 base URL 的 `/responses`，使用 `reasoning: {"effort": "<selected>"}`。若网关明确表示 Responses API 不可用，例如返回 404、405、415、422，或返回空的非 JSON 响应，则在同一 Sub2api、同一模型、同一推理强度下回退到 `/chat/completions`，使用 `reasoning_effort` 字段。

鉴权失败、权限不足、配额错误、超时、Responses `incomplete` 状态和模型拒绝不触发协议回退。特别是 401、403 不应被掩盖为 Chat Completions 重试；错误应直接返回调用方。现有临时网络错误重试策略保持不变。

不配置备用模型和备用 provider，因此任何兼容回退都不会离开 Sub2api，也不会切换到非 `gpt-5.6-sol` 模型。

## 数据流

1. 业务模块通过 `create_llm_provider_from_config` 创建统一 provider。
2. provider 解析 Sub2api、`gpt-5.6-sol` 和 `auto` 配置。
3. 每次 `complete_text` 根据本次消息选择推理强度。
4. provider 优先调用 Sub2api Responses API。
5. 只有协议不兼容时，才在同一 Sub2api 上调用 Chat Completions。
6. 返回文本继续走现有 JSON 修复、解析与业务校验流程。

## 错误处理与可观测性

- 缺少 `SUB2API_KEY` 时，继续在请求前抛出明确的密钥缺失错误。
- 缺少安全 `LLM_BASE_URL`，或配置公网明文 HTTP 时，在发出请求前失败。
- 401、403、429 和服务端错误保留原始 HTTP 状态，沿用现有错误展示。
- 协议回退仅处理已定义的不兼容证据，不吞掉业务错误或鉴权错误。
- provider 名称保持 `sub2api`，使生成结果中的 provider 元数据可用于确认实际线路。
- 错误信息和测试输出不得包含 Authorization header 或密钥内容。

## 测试策略

使用 `httpx.MockTransport` 和纯函数单元测试完成红绿循环，不消耗真实模型额度：

- 验证 Sub2api 默认模型、协议和自动推理配置。
- 验证显式推理强度覆盖自动路由。
- 分别覆盖 `low`、`medium`、`high`、`xhigh` 和 `max` 的代表任务。
- 验证 Responses 与 Chat Completions 请求体使用本次任务计算出的相同强度。
- 验证 Responses 协议不兼容时仅回退到同一 Sub2api Chat Completions。
- 验证 401、403 不触发协议回退。
- 验证超时、`incomplete` 和 refusal 不触发协议回退。
- 验证旧 secondary/fallback 配置不会进入生产 provider。
- 验证 `config` 的运行时解析结果为 Sub2api、`gpt-5.6-sol`、`responses`、`auto`。

完成定向测试后，运行 LLM、RAG 和共用 provider 的相关测试集，再执行文件头检查。

## 非目标

- 不迁移 ASR 或 Embedding 服务。
- 不增加可视化模型配置页面。
- 不实现基于额外 LLM 调用的任务分类器。
- 不启用 GPT-5.6 Pro mode、持久化 reasoning context 或新工具能力。
- 不修改现有业务提示词与输出结构。

## 验收标准

- 运行时配置中不再出现云雾或 OpenCode LLM 线路。
- 所有共用 provider 的文本任务默认请求 Sub2api `gpt-5.6-sol`。
- 自动路由能为五类代表任务选择预期推理强度。
- 显式强度配置可稳定覆盖自动路由。
- 协议回退不会切换服务商或模型，也不会掩盖 401、403。
- 相关自动化测试和文件头检查通过。

## 依据

OpenAI 官方模型说明确认 `gpt-5.6-sol` 支持 `none`、`low`、`medium`、`high`、`xhigh` 和 `max`，并同时支持 Responses 与 Chat Completions。官方迁移指南建议推理工作优先采用 Responses API，以 `medium` 作为平衡起点，并仅为最困难、质量优先的任务保留 `max`。

- <https://developers.openai.com/api/docs/models/gpt-5.6-sol>
- <https://developers.openai.com/api/docs/guides/upgrading-to-gpt-5p6-sol>
