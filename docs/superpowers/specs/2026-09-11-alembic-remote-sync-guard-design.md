# Alembic 迁移远端同步保护设计

## 背景

生产 PostgreSQL 的 `alembic_version` 已记录为
`0021_llm_local_api_keys`，但仓库及 `manjuxuanpin` 远端缺少对应迁移
脚本。这说明现有流程能够在迁移文件未进入远端 Git 历史时升级生产库。

本设计增加提交、推送、远端持续集成和生产迁移前置校验，防止再次出现
“数据库已经升级，但迁移脚本只存在于本机或已在分支清理中丢失”的情况。

## 目标

- 所有 Alembic revision 文件必须进入 Git 历史并同步到当前主上游
  `manjuxuanpin`。
- 在提交阶段尽早发现模型与迁移脚本未配对、迁移链断裂和历史迁移被修改。
- 在推送阶段确认待推送提交完整包含数据库结构变更及其迁移脚本。
- 远端独立复验迁移图和空库升级，避免只依赖可跳过的本地 hook。
- 生产数据库升级前确认执行代码正是 `manjuxuanpin/main` 已接收的提交。
- 所有校验只读访问 Git 和数据库元数据，不修改生产数据，也不影响现有网页服务。

## 非目标

- 本任务不重建内容尚未最终确认的 `0021_llm_local_api_keys.py`；该文件由现有
  数据库迁移恢复任务负责。
- 不自动生成 Alembic migration。
- 不自动推送 Git，也不把其他远端纳入强制同步范围。
- 不允许通过修改既有历史迁移来补偿新结构变化。

## 方案概览

采用四道互补防线：

1. `pre-commit` 检查暂存区中的结构变化和 Alembic 迁移图。
2. `pre-push` 检查发往 `manjuxuanpin` 的实际提交范围。
3. `manjuxuanpin` 远端 CI 在干净 checkout 中复验迁移图及空库升级。
4. 生产迁移入口要求当前提交已存在于 `manjuxuanpin/main`，再允许执行升级。

本地 hook 用于快速反馈，远端 CI 用于不可依赖开发机状态的最终验证，生产前置
校验直接阻止未同步代码修改数据库。单独使用其中任意一层都不足以覆盖本次事故。

## 统一校验器

新增 `tools/check_alembic_integrity.py`，以明确模式运行：

- `repository`：检查当前提交或工作树中的完整迁移图。
- `staged`：检查 Git 暂存区的结构变更与迁移文件配对关系。
- `pre-push`：读取 pre-commit 传入的推送远端以及标准输入中的 ref 更新记录，
  检查实际待推送提交范围。
- `production`：在执行生产迁移前检查工作树、当前提交、远端 main 和数据库
  revision。

校验器使用 Alembic `ScriptDirectory` 解析迁移图，不以文件名排序代替 revision
依赖关系。错误信息必须给出失败规则、涉及文件或 revision，以及可执行的修复建议；
不得输出数据库 URL、口令或其他密钥。

## 仓库迁移图规则

`repository` 模式必须验证：

- 每个迁移模块均可由 Alembic 正常加载。
- revision ID 非空、全局唯一，并与文件名中的 revision 前缀一致。
- 每个 `down_revision` 都指向仓库内存在的 revision；merge revision 的所有父节点
  都必须存在。
- 整张迁移图不存在环路，且只有一个最终 head。
- `system_database/alembic/versions/` 下不存在未被 Git 跟踪的 Python 迁移文件。
- 当前数据库 revision 仅在显式要求数据库校验时读取；常规提交不得依赖生产库
  可用性。

## 提交阶段规则

在 `.pre-commit-config.yaml` 增加两个本地 `pre-commit` hook：一个始终运行
`repository` 模式，另一个始终针对整个暂存区运行 `staged` 模式。两者都不依赖
pre-commit 传入的单个文件列表。首期结构声明范围明确限定为
`system_database/schema.py`；新增其他 PostgreSQL 元数据入口时再显式加入配置，
避免把业务 DTO schema 误判为数据库结构。

规则如下：

- 暂存的 `system_database/schema.py` 或其他纳入配置的结构声明文件发生变更时，
  同一提交必须包含至少一个新增的 Alembic revision 文件。
- 新增 revision 必须已经暂存，不能仅存在于工作树或未跟踪文件中。
- 相对 `HEAD` 已存在的迁移文件禁止删除、重命名或修改。纠错必须新增后续迁移。
- 新增迁移后，无论结构声明文件是否变化，都必须通过完整迁移图检查。
- 纯文档、测试或非数据库代码提交不强制新增 migration，但仍会在迁移目录变化时
  验证迁移图。

该规则能捕获“结构代码已经提交但 migration 未提交”和“migration 留在未跟踪文件
中”两类常见遗漏。

## 推送阶段规则

在同一份 pre-commit 配置中注册 `stages: [pre-push]` 的本地 hook，并提供仓库受控
安装命令，同时安装 `pre-commit` 与 `pre-push` hook。`.git/hooks` 不进入 Git，
因此不能依赖手工复制 hook 文件。

当目标远端名称为 `manjuxuanpin` 时，`pre-push` 模式必须：

- 解析 Git 提供的每条 ref 更新，覆盖新分支、普通更新和删除分支。
- 对每个非删除更新计算远端 SHA 到本地 SHA 的待推送提交范围。
- 若该范围修改结构声明，则范围内必须新增 migration。
- 拒绝待推送范围对远端已有历史 migration 的修改、删除或重命名。
- 从待推送提交树而非仅从当前工作树加载迁移文件并验证完整迁移图。
- 若迁移目录存在未跟踪 Python 文件，明确失败并要求先提交。

推送到其他远端时不执行“必须同步”策略，只执行基础仓库完整性检查；本设计唯一
受保护的主上游是 `manjuxuanpin`。

## 远端 CI

新增 `.gitea/workflows/alembic-integrity.yml`，在 `manjuxuanpin` 的分支推送和合并
请求上执行：

1. 检出完整 Git 历史。
2. 使用项目约定的 Python 3.11 和 `uv` 默认缓存安装依赖。
3. 运行迁移仓库完整性检查。
4. 启动隔离 PostgreSQL，执行空库 `alembic upgrade head`。
5. 运行迁移保护相关测试。

CI 不连接生产数据库。主分支保护应把该工作流设为必需检查；如果
`manjuxuanpin` 未启用 Actions，则需在服务器端 CI 中执行同一组仓库命令后才能
把它视为强制保障。

## 生产迁移前置校验

提供仓库内受控的生产迁移入口。它在调用 `alembic upgrade head` 前必须满足：

- 当前工作树无已跟踪修改，也不存在未跟踪的迁移脚本。
- 当前分支为 `main`。
- 通过只读远端查询取得 `manjuxuanpin/main` 的 SHA，且当前 `HEAD` 与该 SHA 完全
  一致；网络不可用或远端不可确认时失败关闭，不执行 migration。
- 本地 Alembic 图通过完整性检查。
- 数据库当前 revision 能在本地迁移图中解析；未知 revision（如代码仍缺失时的
  `0021_llm_local_api_keys`）直接拒绝升级。
- 数据库目标 revision 等于当前代码的唯一 head。

校验全部通过后才调用 Alembic。旧的直接命令仍由运行环境权限控制，但生产操作
手册和部署入口必须统一切换到该受控命令；CI 测试确保入口不会在任一前置条件失败
时调用升级操作。

## 安装与使用

提供一个跨平台 Python 安装入口，通过
`uv run pre-commit install --hook-type pre-commit --hook-type pre-push` 安装两类
hook，并在 README 或开发文档中标为仓库初始化必做步骤。

常用检查命令保持显式可运行，以便 CI 和故障排查复用：

```text
uv run python tools/check_alembic_integrity.py repository
uv run pre-commit run --all-files
```

## 错误处理

- Git 命令失败、迁移解析失败、远端 SHA 无法确认、数据库 revision 未知时均返回
  非零退出码。
- 本地常规提交不因 PostgreSQL 不可用而失败；数据库访问只发生在生产模式和专门的
  数据库测试中。
- `--no-verify` 能绕过本地 hooks，因此远端 CI 和分支保护不可省略。
- pre-push 只能验证本次推送内容，不能迫使用户执行推送；“生产迁移前 HEAD 必须
  等于远端 main”负责封闭最后一个缺口。

## 测试策略

新增纯 Git 临时仓库测试，覆盖：

- 完整线性迁移链通过。
- 重复 revision、缺失父节点、多 head、文件名与 revision 不一致分别失败。
- 结构声明已暂存但未暂存 migration 时失败。
- migration 仅为未跟踪文件时失败。
- 修改或删除历史 migration 时失败。
- 结构声明与新增 migration 同一提交时通过。
- 推送新分支和更新已有分支时正确计算提交范围。
- 推送到 `manjuxuanpin` 缺少 migration 时失败，完整提交时通过。
- 生产 HEAD 未同步、工作树不干净、远端不可达、数据库 revision 未知时均禁止
  调用 Alembic。

新增 PostgreSQL 集成测试，在隔离空库运行 `upgrade head`。没有测试数据库时允许
相关用例明确跳过，但远端 CI 必须配置 PostgreSQL，不能以跳过作为成功标准。

## 验收标准

- 任何包含数据库结构声明变化、但不含新增迁移脚本的提交都会被 pre-commit 拒绝。
- 任何修改或删除历史迁移的提交都会在提交或推送阶段失败。
- 发往 `manjuxuanpin` 的不完整迁移提交会被 pre-push 拒绝。
- `manjuxuanpin` 上的干净 checkout 能解析唯一 head，并从空 PostgreSQL 升级到
  head。
- 未同步到 `manjuxuanpin/main` 的本地提交不能用于生产数据库升级。
- 数据库记录了代码中不存在的 revision 时，生产迁移入口明确报错且不写数据库。
- 现有网页服务启动和健康检查行为不被本任务修改。
