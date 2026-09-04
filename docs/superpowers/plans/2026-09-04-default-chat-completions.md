# Sub2API 默认使用 Chat Completions 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 执行此计划。

**目标：** 将 Sub2API 的所有默认入口统一改为 Chat Completions，并同步当前数据库配置。

**架构：** 修改预设、运行回退配置和旧配置模块三个默认来源；保留显式 Responses 配置支持。通过现有 Repository 更新生产数据库，不接触已保存 API Key。

**技术栈：** Python 3.11、pytest、PostgreSQL、uv。

---

### 任务 1：统一代码默认值

**文件：**
- 修改：`analysis/llm_settings.py`
- 修改：`analysis/llm.py`
- 修改：`config/bilibili_config.py`
- 测试：`tests/analysis/test_llm_settings.py`
- 测试：`tests/analysis/test_llm.py`

- [ ] 先把 Sub2API 预设和默认解析断言改为 `chat_completions`。
- [ ] 运行聚焦测试，确认因现有默认值为 `responses` 而失败。
- [ ] 修改三个生产默认来源为 `chat_completions`。
- [ ] 运行 LLM 设置和请求协议测试，确认显式 Responses 行为无回归。
- [ ] 提交代码和测试。

### 任务 2：更新运行配置

- [ ] 合并到 `main`。
- [ ] 使用 `LLMSettingsRepository.update_provider()` 把当前 Sub2API 的 `wire_api` 更新为 `chat_completions`。
- [ ] 重启 WebGUI 并验证数据库有效默认、监听地址和 HTTP 状态。
- [ ] 运行完成前测试和 pre-commit 检查。
