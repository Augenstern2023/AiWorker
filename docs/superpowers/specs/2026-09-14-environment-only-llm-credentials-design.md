# LLM 仅环境变量凭据设计

## 背景

当前 LLM 提供商把 API Key 以 Fernet 密文保存在 PostgreSQL，并由
`AIWORKER_LLM_CONFIG_KEY` 或工作区密钥文件解密。多个工作树、临时服务或独立检出
使用不同 Fernet 密钥时，同一条提供商记录会变成不可读，后台 Worker 随后以
`Stored LLM API key cannot be decrypted` 失败。

本次目标是让数据库不再保存 API Key（无论明文或密文），只保存环境变量名称；Web
进程和它启动的 Worker 从进程环境读取真实凭据。此变更不修改数据库结构。

## 方案比较

### 方案 A：继续使用 Fernet，统一配置一个固定密钥

改动最小，但仍需要分发、轮换和保护第二把密钥。错误配置仍会令全部数据库凭据不可
读，不能消除本次故障类型。

### 方案 B：数据库保存明文 API Key

实现简单，但数据库备份、查询权限和诊断输出都会扩大凭据暴露面，不采用。

### 方案 C：数据库仅保存环境变量名称（采用）

真实凭据只存在于部署环境，数据库泄露不会暴露 API Key，也不需要 Fernet 密钥。
代价是每个运行节点必须配置相应环境变量，并在变量变更后重启 Web 服务；这与当前
单节点部署和 Worker 继承父进程环境的运行方式一致。

## 数据模型与凭据解析

沿用现有 `llm_providers.api_key_env_var` 字段，不新增或修改表结构。
`api_key_encrypted` 保留为兼容列，但应用不再读取或写入它；上线时通过一次受控配置
清理将所有现存值设为 `NULL`。

提供商预设给出默认变量名：

- OpenAI：`OPENAI_API_KEY`
- DeepSeek：`DEEPSEEK_API_KEY`
- MiniMax：`MINIMAX_API_KEY`
- Sub2API：`SUB2API_KEY`
- 自定义提供商：必须显式填写变量名

变量名只允许 `[A-Za-z_][A-Za-z0-9_]*`。运行时只通过 `os.getenv()` 读取非空值，
不读取 Windows 注册表，也不在日志、API 响应或审计记录中返回变量值。

`effective_default()`、`effective_model()`、`provider_connection()` 和可用模型列表统一
调用同一个环境凭据解析函数，避免 Web 与 Worker 对“凭据可用”的判断不一致。

## 管理 API 与界面

提供商创建和更新请求用 `api_key_env_var` 取代 `api_key`、`clear_api_key`。旧字段若被
旧客户端提交，返回明确的 400 错误，不做静默保存。

管理页面移除 API Key 密码输入框，改为“API Key 环境变量”输入框。页面只显示：

- `configured`：当前 Web 进程可读取到非空环境变量；
- `missing`：变量名为空或当前进程读不到变量；
- `credential_source: environment`：凭据来自环境。

连接测试、模型同步、设置默认模型以及生成任务都使用同一环境凭据。缺失时返回包含
变量名但不包含变量值的中文错误，例如：`环境变量 SUB2API_KEY 未配置或为空`。

## 现有配置切换

上线顺序固定，避免出现凭据窗口期：

1. 从仍可解密的现有提供商记录读取凭据，仅写入对应的 Windows 用户环境变量；不在
   命令输出中打印值。已有且已验证的 `SUB2API_KEY` 直接复用。
2. 为每个提供商写入 `api_key_env_var`，重启服务并验证连接、模型解析和后台 Worker。
3. 将所有 `api_key_encrypted` 清空。
4. 删除用户级 `AIWORKER_LLM_CONFIG_KEY`，并删除仓库公共目录与 `.runtime` 下的
   `llm_config.key`。

这些步骤是凭据配置切换，不是数据库 schema 迁移。任一步验证失败都在清空密文前
停止。

## 错误处理与安全

- 环境变量缺失时，提供商保留但不可用于默认模型、模型列表或任务启动。
- 不能把缺少凭据的模型设为默认；现有默认模型缺少凭据时返回可操作错误。
- Worker 继续复制父进程环境，因此 Web 服务启动时必须拥有全部所需变量。
- API、日志、审计、测试夹具和 Git 文件都不得包含真实 API Key。
- 不提供环境变量到数据库密文的回退，防止系统悄悄恢复旧的加密依赖。

## 测试与验收

测试按 TDD 覆盖：

- 提供商创建、更新及公开响应只处理变量名，不保存密文；
- 默认模型、显式模型、连接测试和模型同步从环境读取凭据；
- 缺失、空值和非法变量名产生明确错误；
- 可用模型和管理页面状态与 Worker 的实际解析一致；
- API 与浏览器界面不再接受或展示 API Key 输入框；
- Worker 环境继承变量且不依赖 `AIWORKER_LLM_CONFIG_KEY`；
- 数据库所有 `api_key_encrypted` 均为空，密钥文件及用户级加密变量已移除；
- 使用环境凭据完成一次真实的最小 LLM 请求，并重试一次剧本审核任务。

