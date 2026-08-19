# 明文 HTTP LLM 显式授权设计

## 目标

允许运维在明确知晓风险时，通过公网明文 HTTP 调用 Sub2api，同时保持默认拒绝明文传输 API 密钥的安全行为。

## 配置

- `LLM_BASE_URL` 继续提供完整的 OpenAI-compatible `/v1` 基础地址。
- 新增 `LLM_ALLOW_INSECURE_HTTP`，只有可识别的真值才启用明文 HTTP；缺失、空值或其他值均视为关闭。
- 当前本机 `.env` 使用 `http://54.92.99.171:8080/v1`，并显式设置 `LLM_ALLOW_INSECURE_HTTP=true`。
- `SUB2API_KEY` 仍只从环境变量读取，不写入仓库或测试数据。

## 数据流

1. `config` 从 `.env` 读取基础地址与显式授权开关。
2. `resolve_llm_settings` 解析开关并传给 `OpenAICompatibleLLMProvider`。
3. provider 在请求前检查传输地址：HTTPS 和 loopback HTTP 始终允许；公网 HTTP 仅在显式开关开启时允许。
4. 通过检查后，现有 Responses API、协议回退、推理强度与重试逻辑保持不变。

## 错误处理

- 未配置 `LLM_BASE_URL` 时继续返回 `LLM_BASE_URL is required.`。
- 公网 HTTP 且未显式授权时继续返回安全拦截错误。
- 显式授权只跳过传输协议拦截，不跳过 API 密钥、鉴权、配额或模型错误。

## 测试

- 保留“默认拒绝公网 HTTP，且不会发出请求”的测试。
- 新增“显式授权公网 HTTP 后可以发出请求”的测试。
- 验证配置解析与 provider 构造会传递显式授权值。
- 运行 LLM、编剧大师及共用 provider 的相关回归测试，并执行 Python 文件头与 diff 检查。

## 风险

启用该开关会使 Authorization 请求头在客户端与远端服务之间以明文 HTTP 传输，可能被网络路径上的第三方截获。该开关只用于用户已经明确接受该风险的运行环境；生产环境仍推荐 HTTPS 或加密隧道。
