# 跨 Worktree 共用 LLM 加密主密钥实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让同一 Git 仓库的所有 worktree 共用 Fernet 主密钥，并一次性恢复当前不可解密的 Sub2API 凭据。

**架构：** 密钥选择保持环境变量优先；本地默认密钥从 worktree 私有 runtime 目录迁移到 Git common dir，非 Git 部署保持原路径。数据库密文不进入代码，使用现有环境凭据在部署阶段重新加密。

**技术栈：** Python 3.11、cryptography/Fernet、pathlib、pytest、uv、FastAPI/uvicorn。

---

### 任务 1：共享密钥解析与迁移

**文件：**
- 修改：`analysis/llm_settings.py`
- 测试：`tests/analysis/test_llm_settings.py`

- [ ] **步骤 1：编写失败测试**

构造真实的主工作区 `.git` 目录和 linked worktree `.git`/`commondir` 文件。分别从
两个 runtime 目录调用 `LLMSecretCipher.from_environment()`，断言第二个 cipher
可解密第一个 cipher 生成的密文。再预置主工作区 runtime 旧密钥，断言迁移后旧密文
仍可解密。

- [ ] **步骤 2：运行测试并确认红灯**

运行：

```powershell
uv run --project D:\Crawler\AiWorker pytest tests/analysis/test_llm_settings.py -k "worktree or migrates_runtime_key" -v
```

预期：linked worktree 使用不同 runtime 密钥，解密抛出 `LLMSecretError`，或共享密钥文件不存在。

- [ ] **步骤 3：实现最少代码**

在 `LLMSecretCipher` 内增加只负责路径解析的私有类方法：读取 `.git` 目录或 gitdir
指针、解析 `commondir`，返回 `<common-dir>/aiworker/llm_config.key`。共享文件不存在
时优先迁移主工作区 runtime 密钥，验证候选 Fernet key 后通过同目录临时文件、
`fsync` 和原子硬链接发布；并发输家读取已发布密钥。

- [ ] **步骤 4：验证绿灯和回归**

运行：

```powershell
uv run --project D:\Crawler\AiWorker pytest tests/analysis/test_llm_settings.py tests/analysis/test_llm_settings_api.py -q
```

- [ ] **步骤 5：提交实现**

```powershell
git add analysis/llm_settings.py tests/analysis/test_llm_settings.py docs/superpowers
git commit -m "fix(llm): share encryption key across worktrees"
```

### 任务 2：迁移当前运行配置并验证重启

**文件：**
- 不修改已跟踪源码；仅迁移本机忽略的密钥文件和数据库中的加密凭据。

- [ ] **步骤 1：将当前 runtime 密钥迁移到 Git common dir**

使用新实现创建 cipher，确认共享密钥文件存在；所有检查只输出布尔状态，不输出密钥。

- [ ] **步骤 2：恢复当前 Sub2API 凭据**

从进程环境读取 `SUB2API_KEY`，通过 `LLMSettingsRepository.update_provider()` 重新加密
当前 Sub2API 提供商；不把明文写入命令参数、日志或审计内容。

- [ ] **步骤 3：重启并验证**

重启 `uvicorn WebGUI:app --host 0.0.0.0 --port 8899`，检查：

```text
共享密钥存在 = True
提供商 credential_status = configured
监听地址 = 0.0.0.0:8899
首页状态 = 200
```

- [ ] **步骤 4：完成前验证**

运行相关测试、`pre-commit run --files` 和 `git diff --check`，确认无失败后再报告完成。
