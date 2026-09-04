# Sub2API 默认使用 Chat Completions 设计

## 目标

Sub2API 提供商的新建预设、无数据库配置时的运行回退配置，以及当前数据库中的提供商
统一默认使用 `chat_completions`。OpenAI 等其他提供商保持原协议；用户仍可手动选择
`responses`。

## 修改范围

- `PROVIDER_PRESETS["sub2api"]` 的 `wire_api` 改为 `chat_completions`。
- Sub2API 的运行默认值和 `config/bilibili_config.py` 旧配置回退值同步修改。
- 当前数据库提供商通过 Repository 更新为 `chat_completions`，不修改 API Key。
- 保留 Responses API 实现及其兼容回退测试，以支持显式选择该协议的配置。

## 验收

- Sub2API 预设和无数据库默认解析结果均为 `chat_completions`。
- 当前数据库默认模型的有效配置为 `chat_completions`。
- 服务重启后仍为该协议，监听 `0.0.0.0:8899` 且 HTTP 200。

