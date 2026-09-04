# 跨 Worktree 共用 LLM 加密主密钥设计

## 背景与目标

LLM 提供商凭据保存在 PostgreSQL，但默认 Fernet 主密钥位于各工作目录的
`.runtime/llm_config.key`。Git linked worktree 各自拥有独立工作目录，因此从不同
worktree 启动 WebGUI 会生成不同主密钥，导致同一数据库中的凭据在重启后不可解密。

目标是让同一 Git 仓库的主工作区和全部 linked worktree 自动使用同一主密钥，同时
保持 `AIWORKER_LLM_CONFIG_KEY` 的显式配置优先级，并兼容非 Git 部署。

## 方案

`LLMSecretCipher.from_environment()` 按以下顺序选择密钥：

1. `AIWORKER_LLM_CONFIG_KEY`：适用于多主机部署，优先级最高。
2. Git common dir 下的 `aiworker/llm_config.key`：同一仓库所有 worktree 共用。
3. `<runtime_dir>/llm_config.key`：非 Git 部署继续使用现有行为。

首次在 Git 仓库内升级时，如果共享密钥尚不存在，则优先迁移主工作区的 runtime
密钥，即使首次启动来自 linked worktree；主工作区没有旧密钥时才采用当前 worktree
的旧密钥或生成新密钥。候选旧密钥会在发布前验证。新文件先在目标目录完整写入并
`fsync`，再通过硬链接以“不覆盖”的方式原子发布；失败只留下可清理的临时文件，
不会留下空的最终密钥。并发启动时只有一个候选能发布成功，其他进程读取胜出的密钥。

Git common dir 通过主工作区 `.git` 目录或 linked worktree `.git` 指针及其
`commondir` 文件解析，不调用外部 Git 命令。解析失败时安全回退到 runtime 路径。

## 当前凭据恢复

现有数据库密文已经无法被任何仍存在的本地密钥解密。当前系统环境中存在
`SUB2API_KEY`，因此部署时用该环境变量把现有 Sub2API 提供商凭据重新加密一次，
不打印或记录明文。之后所有 worktree 使用共享主密钥，不再因切换启动目录而失效。

## 测试与验收

- 主工作区与 linked worktree 分别创建 cipher，能够互相解密同一密文。
- 已有 runtime 密钥会迁移并继续解密旧密文。
- 非 Git runtime 的现有持久化测试继续通过。
- 当前数据库凭据状态恢复为 `configured`，服务重启后仍可解密。
- WebGUI 继续监听 `0.0.0.0:8899` 且首页返回 HTTP 200。
