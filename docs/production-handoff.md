# AiWorker → auto_short_play 生产交接

## 实现范围

本次提供可执行的 Python/Node 接口、本地 CLI 与网页导出闭环。网页导出仅打包已锁定的创作资料，不新增独立导航菜单、不启动 Node 初始化、不启动媒体生成、不自动完成交付工作、不替代对外生产审批。

1. `creative.production_export.export_production_package(db_path, project_id, episode_numbers=None)`：单个只读 SQLite 快照导出锁定 Brief、人物、关系、大纲、Beats、Script；检验上游版本；附稳定 SHA256。返回完整原始行及解析后的 content。`preview_production_package` 供 CLI 与网页预览共用同一套检查与阻塞语义。
2. `auto_short_play/automation/framework/scripts/source/aiworker.mjs`：确定性转标准文本，保留场次映射；通过原有入口检查与项目初始化；sidecar 为 `01-source/aiworker-production-package.json`。
3. `creative.production_status.sync_production_status(status_db, project_root, project_id, fingerprint)`：回读 project.json、PROGRESS.md 和输出 manifest；独立 SQLite 幂等保存并回读。不会修改创作审批状态。
4. 网页生产包导出（`creative.production_exports` + `creative.production_exports_api`）：
   - 入口一：`/review/projects`（负责项目）列表每行“导出项目文档包”按钮。
   - 入口二：`/planning/projects/{id}/brief`（创作任务书）页头“导出项目”按钮。
   - 两处共用 `templates/common/workspace/production_export_modal.html` 与 `static/js/common/production_export.js`，分“导出检查/历史导出”两个页签；范围支持全剧、连续区间、自选集数和当前可用集。
   - 网页制品为 ZIP 项目文档包：仿写项目额外包含锁定的原剧拆解；Brief、人物设定和人物关系分别保存为 Markdown；分集大纲与 Beats 按集保存为 Markdown，场次剧本按集保存为 TXT；每集剧本开头重复附带同一份全剧 AI 视频生成规格建议；压缩包不包含 JSON，`99_数据清单` 仅保留 TXT 格式的逐文件 SHA256。
   - 导出预检要求视频生成规格建议已生成、覆盖完整全剧且与当前有效场次剧本指纹一致；缺失、分析未完成、只覆盖部分剧本、内容为空或剧本变化导致过期时均阻止导出，并引导回剧本页处理。导出指定集时仍复用已验证的全剧规格建议。
   - 四个接口：`GET /api/creative/projects/{id}/production-export/preview` 可重复传入 `episode_numbers`；`POST /api/creative/projects/{id}/production-exports` 携带同一组 `episode_numbers` 与 `expected_fingerprint` 并校验 CSRF；另有历史列表与下载接口。
   - 权限：仅系统管理员，及拥有项目管理权限（`can_access_managed_project` 语义）的负责人。页面访问权限、普通成员身份一律不放行；未登录 401、无权限 403、项目/记录缺失 404、文件缺失/篡改/越界 410、审计暂不可用 503、检查未通过或版本变化 409。
   - 不可变记录：表 `creative_production_exports` 以“项目 + 指纹 + 契约版本”为唯一键，保存制品格式、全剧/部分集标记、具体集数、项目总集数和创作模式；不含 JSON 的 ZIP 文档包使用契约 2.4，历史 ZIP/JSON 继续按原格式下载。重复请求复用原产物并保留最初创建信息。
   - 版本指纹（锁定内容）与文件 SHA256（磁盘字节）分开：历史包不可被当前内容覆盖，下载前校验路径边界、文件存在性与文件校验值，损坏即 410，不静默重建。
   - 审计至少一次：`production_export.create / .reuse / .download_started`；审计失败记录保持 `pending` 并阻塞下载，重试（再次创建/下载）自动补记后才可下载。下载审计表示服务端批准开始返回文件，不证明浏览器已完整保存。
   - 生命周期：导出记录已纳入 Writer 回滚的删除状态与外部使用指纹；显式删除项目时，记录随 `creative_production_exports` 删除，文件仅允许清理固定导出目录内已登记路径；审计记录保留。
   - 导出不修改阶段导出状态、不完成交付工作、不运行 Node 初始化；“已导出”不等于生产审批通过。

## 操作入口

从 AiWorker 根目录执行：

```bash
uv run python -m creative.production_export_cli --project-id PROJECT_ID > package.json
```

这是可信本地操作员接口，不是经审批审计的对外交付功能。实际商用/对外交付仍应走原有审批与授权流程。导出必须成功后才可消费 stdout；不要把失败产生的空文件送给下游。

网页端：登录 WebGUI（http://localhost:8080）后，进入“负责项目”或项目 Brief 页，点击导出按钮；检查全部通过后“生成项目文档包”，随后在结果区或历史页签下载 ZIP。仅导出锁定创作资料，尚未开始生产。ZIP 只含 Markdown、TXT 和校验清单，不包含 JSON；本地 CLI 的机器衔接接口保持不变。

从 auto_short_play/automation 根目录执行：

```bash
node framework/scripts/source/aiworker.mjs --package ABS_PACKAGE_PATH --out NEW_CONVERSION_DIRECTORY
node framework/scripts/source/aiworker.mjs --package ABS_PACKAGE_PATH --init NEW_PROJECT_DIRECTORY --approval ABS_APPROVAL_PATH
```

approval 必须来自真实来源对照审核和用户元数据确认，格式见本仓库 `docs/aiworker-adapter.md`，不能复用测试 fixture。初始化固定停在 structure，后续由原有生产编排继续。

从 AiWorker 根目录回读：

```bash
uv run python -m creative.production_status_cli --project-root ABS_PROJECT_DIRECTORY --project-id PROJECT_ID --fingerprint PACKAGE_FINGERPRINT
```

默认落库 `.runtime/creative/production-status.db`。调用方可按任务节点主动同步；本次没有新增周期任务或自动 HTTP callback。

## 验证

```bash
uv run pytest tests/test_production_export.py tests/test_production_preview.py \
    tests/test_production_archive.py \
    tests/test_production_status.py tests/test_production_integration.py \
    tests/test_production_exports_web.py tests/test_production_exports_browser.py -q
node ../auto_short_play/automation/framework/tests/aiworker.test.mjs
npm --prefix ../auto_short_play/automation/framework test
```

跨仓测试使用明确标记的合成 SQLite 创作数据，实际调用导出、Node 初始化、原有结构验证、Python 状态回读。没有生成真实视频，也没有假称生产审批通过。

## 故障与重试

- 创建/复用返回 409：当前锁定版本与确认指纹不一致，回到“导出检查”页签重新确认后再生成；历史包不受影响。
- 下载 410：登记文件缺失、被改动或路径越界，禁止下载，也不从当前项目重建历史包；联系管理员恢复导出目录。
- 创建 503/审计待补记：产物与记录已保留，稍后对同一指纹再次创建或下载即自动补记审计；补记成功前不提供下载。
- 回退方式：仅撤下页面入口与导出路由，`creative_production_exports` 记录、文件与审计保留供排查。

## 边界

- 当前 Node 入口只接受完整连续全剧，Python 的部分集导出仅供其他调用方；不要把局部包送到该入口。
- 网页导出支持全剧或选集的锁定版本，格式固定为 ZIP；不支持自定义输出目录或上传外部包。“当前可用集”只选择大纲、Beats、剧本均已锁定的集，并在生成前再次预检。本地 CLI 仍输出原始 JSON，供现有 Node 入口直接消费。
- 下游保留 Brief/人物/关系/大纲/Beats，但没有在本次自动生成视觉资产或替代原有分镜编排。
- Node 校验指纹非空并原样保存，不重新计算上游指纹；只接收可信本地导出包。状态同步校验来源绑定，不证明文件未经篡改，也不自动检测原剧库后续变化。
- 完成集数仅检查索引和非空文件，不代表媒体可解码、口型准确或内容质量通过。
- 幂等键为来源项目 ID + 指纹 + 契约版本；同一版本的多网页请求保留唯一产物与最初创建信息，历史 JSON 与 ZIP 文档包不会互相复用；`production_status` 表仍以项目 + 指纹为幂等键，多次下游执行保留最新同步摘要，不是多运行历史表。
- 没有修改原有未提交的 writer_agent.js 或其浏览器测试；未提交 Git。
