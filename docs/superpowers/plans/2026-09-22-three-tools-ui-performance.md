# AiWorker “三剑客”工具链、工作台 UI 与站点性能实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在不改变业务架构和接口契约的前提下，将 Ruff、uv、ty 建成低噪声 Python 质量链路，同时把 Web GUI 优化为任务驱动、内容优先的深色工作台，并降低无关脚本、重复静态目录扫描和后台轮询带来的首屏与运行时成本。

**架构：** 计划分为两个可独立审查、独立提交的实施轨道。轨道 A 以 `pyproject.toml` 和 `uv.lock` 为唯一依赖事实源，以 Ruff 高置信度规则阻断真实缺陷，以 ty 对四个 schema 文件做观察性试点；轨道 B 保留 FastAPI + Jinja2 + Bootstrap 5 + Alpine.js，通过统一设计令牌、任务页和编辑页信息架构、`window.aiworkerModules` 模块拆分、轮询生命周期管理及静态版本缓存来改善 UI 与性能。两个轨道不混入爬虫、数据库、LLM、媒体处理或权限模型重构。

**技术栈：** Python 3.11、uv、Ruff、ty、pytest、pre-commit、Gitea Actions、FastAPI、Jinja2、Bootstrap 5、Bootstrap Icons、Alpine.js、原生 JavaScript/CSS。

---

## 实施边界与视觉方向

- 保留现有 FastAPI、Jinja2、Bootstrap 5、Alpine.js 和 `window.aiworkerModules` 扩展协议；不引入 React、Vue、Tailwind、Motion 或新的前端构建链。
- 主页面采用 **Operate + Monitor**：用户先看到下一步动作、进行中项目和异常；编辑详情采用 **Command / Inspect**：固定操作区、内容编辑区、上下文检查区。
- 视觉变化度 3/10、动效 2/10、信息密度 6/10。保持深色工作台；蓝色是唯一交互强调色，绿/黄/红只表达成功、警告和失败。
- 不使用渐变、大面积装饰背景、无意义悬浮卡片或纯装饰动画；使用现有 Bootstrap Icons，不增加图标依赖。
- 动效只用于状态过渡，时长 120–180ms；`prefers-reduced-motion: reduce` 下关闭非必要过渡。
- 工具链轨道禁止全仓自动修复和格式化；UI 轨道禁止顺手改业务 API、数据库 schema 或权限语义。
- 开始每个任务前执行 `git status --short`。只暂存任务列出的文件，避开当前工作区已有的删除项、`.codex_tmp/`、示例文档和其他未跟踪计划。
- 所有 Python 命令统一使用 `uv run`；使用 uv 默认全局缓存，不设置仓库内 `UV_CACHE_DIR`，不创建 `.uv-cache*`。

## 已测基线与性能预算

以下是 2026-09-22 工作区中的未压缩源文件大小，用于比较改造前后，不等同于浏览器启用压缩后的传输大小：

| 资源/行为 | 当前基线 | 本计划预算 |
| --- | ---: | ---: |
| `static/js/echarts.min.js` | 1,030,900 bytes | 仍只在分析页加载 |
| `static/js/common/creative.js` | 277,237 bytes | 拆分后核心文件 ≤ 160,000 bytes |
| `static/js/common/tasks.js` | 60,075 bytes | `/tasks` 不加载 |
| `static/css/bootstrap.min.css` | 232,921 bytes | 保留现有本地 Bootstrap，不重复加载 |
| `static/css/workspaces.css` | 53,800 bytes | 允许因令牌和布局增长，但不复制 Bootstrap 组件体系 |
| `static_asset_version()` | 每次模板上下文递归扫描 `static/` | 每进程最多扫描 1 次 |
| jobs/logs 轮询 | 5 秒/1 秒固定 interval | 隐藏页 0 次、同类请求最大并发 1 |

不设置缺乏真实生产网络和硬件数据支撑的毫秒级首屏指标。首轮以可重复的资源归属、源文件体积、目录扫描次数和轮询并发预算作为阻断条件；上线后再从真实监控建立 LCP、INP 和接口延迟基线。

## 文件结构

### 轨道 A：Python 工具链

- 修改 `pyproject.toml`：拆分开发依赖，固定 Ruff/ty 兼容范围，配置 Ruff 最小规则和 ty 试点范围。
- 修改 `uv.lock`：锁定新依赖及开发依赖分组。
- 修改 `.gitignore`：忽略根目录 Ruff 缓存。
- 修改 `workflow/repository.py`：补齐 `Any`、`Path` 的真实导入。
- 修改 `workflow/transition_service.py`：补齐 `Path` 的真实导入。
- 修改 `GUI.py`：修复异常变量被延迟 lambda 捕获的问题。
- 修改 `.pre-commit-config.yaml`：增加只检查传入 Python 文件的 Ruff hook。
- 创建 `.gitea/workflows/python-quality.yml`：在无数据库服务的独立 job 中运行锁文件检查、Ruff 和 ty 观察步骤。
- 删除 `requirements.txt`：消除第二份人工维护的依赖源。
- 修改 `docs/index.md`、`README_en.md`、`README_es.md`、`docs/excel_export_guide.md`：统一安装方式为 uv。

### 轨道 B：UI 与性能

- 修改 `static/css/workspaces.css`：整理设计令牌、状态样式、任务卡片、编辑器布局、响应式和低动效规则。
- 修改 `templates/layouts/base.html`：保持基础壳轻量，补充语义化页面结构；不在全局加入页面专属资源。
- 修改 `templates/common/workspace/my_tasks.html`：从项目表格改为任务驱动的操作与监控布局。
- 修改 `static/js/common/my_tasks.js`：派生下一步动作、筛选和搜索，安全渲染任务卡片。
- 修改 `templates/common/workspace/project_beats.html`：把 11 列横向编辑表改为场景导航 + 聚焦编辑器 + 连续性检查区。
- 修改 `static/js/writing/beats.js`：提供当前场景选择和键盘可访问行为。
- 修改 `static/js/common/creative.js`：只保留项目/资源共用能力，继续使用 `window.aiworkerModules`。
- 创建 `static/js/selection/drama_reviews.js`：承接选题评论采集能力。
- 创建 `static/js/common/prompt_templates.js`：承接提示词模板、治理、市场和导入能力。
- 创建 `static/js/writing/creative_episodes.js`：承接大纲、Beats、场次剧本、下载和视频参数能力。
- 创建 `static/js/common/creative_generation.js`：承接生成、任务进度、重试、保存和锁定能力。
- 修改 `templates/selection/topics.html`、`templates/selection/hits.html`、`templates/selection/dramas.html`：只加载选题域所需模块。
- 修改 `templates/planning/projects.html`、`templates/planning/brief.html`、`templates/planning/characters.html`：加载项目、提示词和生成模块。
- 修改 `templates/writing/projects.html`、`templates/writing/outlines.html`、`templates/writing/beats.html`、`templates/writing/scripts.html`：加载项目、写作、提示词和生成模块。
- 修改 `templates/common/workspace/prompt_template_center.html`、`templates/common/workspace/prompt_template_effects.html`：只加载项目和提示词模块。
- 修改 `static/js/common/tasks.js`：使用可暂停、无重叠的轮询调度器，并在隐藏/离开页面时停止计时器。
- 修改 `web/page_router.py`：静态资源版本只在进程生命周期内扫描一次；让 `/tasks` 不加载 Alpine 工作区运行时。
- 创建 `tests/test_frontend_performance_contract.py`：固化路由资源边界、脚本体积和全局依赖预算。
- 修改 `tests/test_page_authorization.py`：验证静态版本缓存和 `/tasks` 资源边界。
- 修改 `tests/test_webgui_ui_changes.py`：验证任务页、编辑页、模块注册和轮询生命周期。
- 修改 `tests/test_role_page_content.py`：验证拆分后所有工作区 JavaScript 语法与模板资源顺序。
- 修改现有浏览器测试 `tests/test_episode_all_view_browser.py`：验证 Beats 聚焦编辑和全集查看不回归。

## 执行顺序

1. 轨道 A 的任务 1–5 顺序执行，形成五个独立提交。
2. 轨道 B 的任务 6–11 顺序执行，形成六个独立提交。
3. 两条轨道可在不同 worktree 中并行；若在同一工作区执行，先完成轨道 A，再开始轨道 B。
4. 最终验收只在所有任务合并后运行一次。任何失败先修复根因，再重跑失败命令及最终验收；不重复运行已经通过且未受影响的套件。

---

## 轨道 A：Python 工具链

### 任务 1：收口 uv 开发依赖与锁文件

**文件：**
- 修改：`pyproject.toml`
- 修改：`uv.lock`
- 修改：`.gitignore`

- [ ] **步骤 1：记录工作区与工具基线**

运行：

```powershell
git status --short
uv lock --check
uv run python --version
```

预期：锁文件在修改前有效，Python 为 3.11；记录现有未提交文件，后续暂存时不包含它们。

- [ ] **步骤 2：为依赖分组编写失败检查**

运行：

```powershell
uv run python -c "import tomllib, pathlib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); dev=p['dependency-groups']['dev']; assert any(x.startswith('ruff') for x in dev); assert any(x.startswith('ty') for x in dev)"
```

预期：FAIL，当前没有 `dependency-groups.dev`。

- [ ] **步骤 3：移动开发依赖并加入 Ruff、ty**

从 `[project].dependencies` 移除 `pytest`、`pytest-asyncio`、`pre-commit`，在 `pyproject.toml` 增加：

```toml
[dependency-groups]
dev = [
    "pre-commit>=3.5.0",
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "ruff>=0.16.8,<0.17",
    "ty>=0.0.81,<0.1",
]
```

在 `.gitignore` 的类型检查缓存附近增加：

```gitignore
# Ruff
/.ruff_cache/
```

- [ ] **步骤 4：重新锁定并验证可复现安装**

运行：

```powershell
uv lock
uv lock --check
uv sync --locked --group dev
uv run ruff --version
uv run ty --version
uv run pytest --version
uv run pre-commit --version
```

预期：全部退出码为 0；Ruff 为 `0.16.x`，ty 小于 `0.1`；仓库根目录没有 `.uv-cache*`。

- [ ] **步骤 5：提交依赖收口**

```powershell
git add pyproject.toml uv.lock .gitignore
git commit -m "build: 收口 uv 开发依赖"
```

### 任务 2：清零 Ruff 高置信度诊断

**文件：**
- 修改：`pyproject.toml`
- 修改：`workflow/repository.py`
- 修改：`workflow/transition_service.py`
- 修改：`GUI.py`

- [ ] **步骤 1：配置 Ruff 最小门禁**

在 `pyproject.toml` 增加：

```toml
[tool.ruff]
target-version = "py311"
line-length = 100
extend-exclude = [
    ".codex_tmp",
    ".runtime",
    "backups",
    "browser_data",
    "data",
    "logs",
]

[tool.ruff.lint]
select = ["E9", "F63", "F7", "F82"]
```

- [ ] **步骤 2：运行检查确认 121 项存量**

运行：`uv run ruff check .`

预期：FAIL；诊断集中在 `workflow/repository.py`、`workflow/transition_service.py` 和 `GUI.py`，主要为未定义的 `Any`、`Path` 和异常变量延迟捕获。

- [ ] **步骤 3：修复真实导入和异常消息捕获**

在 `workflow/repository.py` 增加 `from pathlib import Path` 和 `from typing import Any`；在 `workflow/transition_service.py` 增加 `from pathlib import Path` 并保留已有 `Any` 导入。不添加 `noqa`：

```python
from pathlib import Path
from typing import Any
```

将 `GUI.py` 两处异常回调改成在 `except` 作用域内先求值，再由默认参数捕获，例如：

```python
except Exception as exc:
    error_message = f"启动爬虫失败: {exc}"
    self.comment_crawler_window.after(
        0,
        lambda message=error_message: messagebox.showerror("错误", message),
    )
```

同样处理复查失败回调；不以改名、字符串注解或忽略注释掩盖诊断。

- [ ] **步骤 4：验证诊断与语法**

运行：

```powershell
uv run ruff check .
uv run python -m py_compile GUI.py workflow/repository.py workflow/transition_service.py
```

预期：两条命令均退出码为 0，且没有生成被 Git 跟踪的文件。

- [ ] **步骤 5：提交高置信度修复**

```powershell
git add pyproject.toml GUI.py workflow/repository.py workflow/transition_service.py
git commit -m "fix: 清理 Ruff 高置信度诊断"
```

### 任务 3：建立本地与 CI Ruff 门禁

**文件：**
- 修改：`.pre-commit-config.yaml`
- 创建：`.gitea/workflows/python-quality.yml`

- [ ] **步骤 1：先验证 hook 尚不存在**

运行：

```powershell
uv run python -c "from pathlib import Path; s=Path('.pre-commit-config.yaml').read_text(encoding='utf-8'); assert 'id: ruff-check' in s"
```

预期：FAIL。

- [ ] **步骤 2：增加只检查传入文件的本地 hook**

在 `.pre-commit-config.yaml` 的 local hooks 中加入：

```yaml
      - id: ruff-check
        name: Check Python with Ruff
        entry: uv run ruff check --force-exclude
        language: system
        types: [python]
        pass_filenames: true
        stages: [pre-commit]
```

不得添加 `--fix`，也不得把 hook 改成每次扫描全仓。

- [ ] **步骤 3：创建独立 Python 质量工作流**

`.gitea/workflows/python-quality.yml` 写入：

```yaml
name: Python quality

on:
  push:
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.11"
          enable-cache: true
      - run: uv lock --check
      - run: uv sync --locked --group dev
      - run: uv run ruff check .
```

该 job 不声明 PostgreSQL/Redis 服务，不复用 Alembic 工作流。

- [ ] **步骤 4：验证 hook 和 YAML**

运行：

```powershell
uv run pre-commit run ruff-check --all-files
uv run pre-commit run check-yaml --files .gitea/workflows/python-quality.yml .pre-commit-config.yaml
```

预期：两个 hook 均 PASS。

- [ ] **步骤 5：提交质量门禁**

```powershell
git add .pre-commit-config.yaml .gitea/workflows/python-quality.yml
git commit -m "ci: 增加 Ruff 质量门禁"
```

### 任务 4：删除双依赖源并统一安装文档

**文件：**
- 删除：`requirements.txt`
- 修改：`docs/index.md`
- 修改：`README_en.md`
- 修改：`README_es.md`
- 修改：`docs/excel_export_guide.md`

- [ ] **步骤 1：固定文档失败检查**

运行：

```powershell
uv run python -c "from pathlib import Path; assert not Path('requirements.txt').exists(); bad=[]; files=['docs/index.md','README_en.md','README_es.md','docs/excel_export_guide.md']; [bad.append(p) for p in files if ('pip install -r requirements.txt' in Path(p).read_text(encoding='utf-8') or 'pip install openpyxl' in Path(p).read_text(encoding='utf-8'))]; assert not bad, bad"
```

预期：FAIL，因为 `requirements.txt` 和旧安装入口仍存在。

- [ ] **步骤 2：删除 requirements 并改写文档命令**

删除 `requirements.txt`。把安装示例统一为：

```bash
uv sync --locked --group dev
uv run playwright install
uv run main.py --help
```

Excel 指南说明 `openpyxl` 已由 `pyproject.toml` 声明，只需执行 `uv sync --locked`；不保留可执行的 `pip install` 备选路径。若外部系统未来必须接收 requirements 文件，文档只给出即时导出命令：

```bash
uv export --locked --no-dev --format requirements-txt > requirements.generated.txt
```

- [ ] **步骤 3：扫描残留入口**

运行：

```powershell
rg -n "pip install|requirements\.txt" docs README*.md .gitea *.bat pyproject.toml
```

预期：没有可执行的 pip 安装命令，也没有把 `requirements.txt` 作为安装入口的说明；允许出现“不要使用 pip”的规范性文字和即时导出说明。

- [ ] **步骤 4：验证锁文件仍有效**

运行：

```powershell
uv lock --check
uv sync --locked --group dev
```

预期：均退出码为 0。

- [ ] **步骤 5：提交文档与依赖源清理**

```powershell
git add requirements.txt docs/index.md README_en.md README_es.md docs/excel_export_guide.md
git commit -m "docs: 移除 requirements 和 pip 安装入口"
```

### 任务 5：建立 ty 四文件观察性试点

**文件：**
- 修改：`pyproject.toml`
- 修改：`.gitea/workflows/python-quality.yml`

- [ ] **步骤 1：验证未配置时检查范围不受控**

运行：

```powershell
uv run python -c "import tomllib, pathlib; p=tomllib.loads(pathlib.Path('pyproject.toml').read_text(encoding='utf-8')); assert p['tool']['ty']['src']['include'] == ['identity/schema.py','analysis/schema.py','workflow/schema.py','writer_agent/schema.py']"
```

预期：FAIL，因为 ty 配置尚不存在。

- [ ] **步骤 2：配置唯一的试点范围**

在 `pyproject.toml` 增加：

```toml
[tool.ty.environment]
python-version = "3.11"

[tool.ty.src]
include = [
    "identity/schema.py",
    "analysis/schema.py",
    "workflow/schema.py",
    "writer_agent/schema.py",
]
```

保留 `mypy.ini`，不增加全仓 exclude 或批量忽略规则。

- [ ] **步骤 3：在 CI 增加观察性检查**

在 Ruff 步骤后加入：

```yaml
      - name: Observe ty schema pilot
        continue-on-error: true
        run: uv run ty check
```

- [ ] **步骤 4：验证试点为零诊断且 Ruff 不回归**

运行：

```powershell
uv run ty check
uv run ruff check .
uv lock --check
```

预期：均退出码为 0；ty 输出只覆盖四个 schema 文件。CI 连续 5 次通过前保留 `continue-on-error: true`，之后移除观察标志必须另开提交。

- [ ] **步骤 5：提交 ty 试点**

```powershell
git add pyproject.toml .gitea/workflows/python-quality.yml
git commit -m "build: 增加 ty schema 试点"
```

---

## 轨道 B：UI 与站点性能

### 任务 6：建立设计令牌与前端性能契约

**文件：**
- 修改：`static/css/workspaces.css`
- 修改：`templates/layouts/base.html`
- 创建：`tests/test_frontend_performance_contract.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：写入失败的设计与资源边界测试**

在 `tests/test_frontend_performance_contract.py` 使用项目标准版权头，并加入：

```python
# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_workspace_tokens_define_one_accent_and_semantic_states():
    css = source("static/css/workspaces.css")
    for token in (
        "--surface-canvas", "--surface-panel", "--surface-raised",
        "--color-accent", "--color-success", "--color-warning",
        "--color-danger", "--radius-control", "--radius-panel",
        "--space-1", "--space-6",
    ):
        assert token in css
    assert "prefers-reduced-motion: reduce" in css


def test_heavy_libraries_are_route_scoped():
    base = source("templates/layouts/base.html")
    analytics = source("templates/admin/analytics.html")
    assert "echarts.min.js" not in base
    assert "echarts.min.js" in analytics
    assert "creative.js" not in base
```

在 `tests/test_webgui_ui_changes.py` 增加：

```python
def test_workspace_shell_exposes_skip_link_and_main_landmark():
    base = _source("templates/layouts/base.html")
    assert 'class="ui-skip-link"' in base
    assert 'id="workspace-main"' in base
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py::test_workspace_shell_exposes_skip_link_and_main_landmark -q
```

预期：FAIL，缺少新令牌、低动效规则和跳转链接。

- [ ] **步骤 3：统一令牌和基础壳**

将现有颜色映射到语义明确的令牌，并保留旧变量作为短期别名以避免大范围重写：

```css
:root {
    --surface-canvas: #11131a;
    --surface-panel: #191d27;
    --surface-raised: #222835;
    --surface-input: #141821;
    --color-text: #f3f6fa;
    --color-muted: #98a2b3;
    --color-border: #303747;
    --color-accent: #3b82f6;
    --color-success: #22c55e;
    --color-warning: #f59e0b;
    --color-danger: #ef4444;
    --radius-control: 6px;
    --radius-panel: 10px;
    --space-1: 4px;
    --space-2: 8px;
    --space-3: 12px;
    --space-4: 16px;
    --space-5: 20px;
    --space-6: 24px;
    --bg-page: var(--surface-canvas);
    --bg-panel: var(--surface-panel);
    --bg-panel-2: var(--surface-raised);
    --border: var(--color-border);
    --text: var(--color-text);
    --muted: var(--color-muted);
    --accent: var(--color-accent);
}

@media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
        scroll-behavior: auto !important;
        transition-duration: .01ms !important;
        animation-duration: .01ms !important;
        animation-iteration-count: 1 !important;
    }
}
```

在 `base.html` 的主体内容前增加：

```html
<a class="ui-skip-link" href="#workspace-main">跳到主要内容</a>
<main id="workspace-main"{% if workspace_page %} class="main-content"{% else %} class="container py-4"{% endif %}>
```

为 `.ui-skip-link` 提供仅在聚焦时可见的样式；保持 ECharts 只在分析页、`creative.js` 只在需要的页面加载。

- [ ] **步骤 4：验证设计基础**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py::test_workspace_shell_exposes_skip_link_and_main_landmark tests/test_webgui_ui_changes.py::test_workspace_shell_has_accessible_mobile_drawer_controls -q
```

预期：全部 PASS。

- [ ] **步骤 5：提交设计基础**

```powershell
git add static/css/workspaces.css templates/layouts/base.html tests/test_frontend_performance_contract.py tests/test_webgui_ui_changes.py
git commit -m "style: 统一工作台设计令牌"
```

### 任务 7：把 `/tasks` 改为任务驱动的操作与监控页

**文件：**
- 修改：`templates/common/workspace/my_tasks.html`
- 修改：`static/js/common/my_tasks.js`
- 修改：`static/css/workspaces.css`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：写入失败的任务页结构测试**

在 `tests/test_webgui_ui_changes.py` 增加：

```python
def test_my_tasks_is_an_operate_and_monitor_workspace():
    html = _source("templates/common/workspace/my_tasks.html")
    script = _source("static/js/common/my_tasks.js")
    for marker in (
        'id="task-summary"', 'id="task-search"', 'id="task-status-filter"',
        'id="my-project-list"', 'aria-live="polite"',
    ):
        assert marker in html
    assert "projectNextAction" in script
    assert "applyProjectFilters" in script
    assert "data-task-card" in script
    assert "innerHTML = project" not in script
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_my_tasks_is_an_operate_and_monitor_workspace tests/test_webgui_ui_changes.py::test_my_tasks_uses_set_button_loading -q
```

预期：新结构测试 FAIL，原有按钮状态测试 PASS。

- [ ] **步骤 3：重排信息层级并保留现有 API**

页面标题改为“我的工作台”，顶部展示“需处理 / 进行中 / 已完成”三个摘要值；随后放置搜索和状态筛选，主体使用响应式项目任务卡。卡片只显示：项目名、来源、当前阶段、更新时间、明确的下一步按钮；编辑和删除收进次要操作区。

任务卡容器保留 `id="my-project-list"`，并使用：

```html
<section class="task-workspace" aria-labelledby="task-workspace-title">
    <div id="task-summary" class="task-summary-grid" aria-live="polite"></div>
    <div class="task-toolbar" role="search">
        <label class="visually-hidden" for="task-search">搜索项目</label>
        <input id="task-search" class="form-control" type="search" placeholder="搜索项目或来源选题">
        <label class="visually-hidden" for="task-status-filter">筛选状态</label>
        <select id="task-status-filter" class="form-select">
            <option value="all">全部状态</option>
            <option value="action">需处理</option>
            <option value="running">进行中</option>
            <option value="done">已完成</option>
        </select>
    </div>
    <div id="my-project-list" class="task-card-grid" aria-busy="true"></div>
</section>
```

`my_tasks.js` 继续请求 `/api/creative/projects`，在客户端派生动作，不改变后端响应：

```javascript
function projectNextAction(project) {
    const actions = {
        created: {label: '完善创作方向', stage: 'action'},
        direction_review: {label: '查看生成进度', stage: 'running'},
        brief_locked: {label: '推进人物关系', stage: 'action'},
        characters_draft: {label: '继续编辑人物', stage: 'action'},
        characters_locked: {label: '推进分集大纲', stage: 'action'},
        outline_draft: {label: '继续编辑大纲', stage: 'action'},
        outline_locked: {label: '推进单集 Beats', stage: 'action'},
        beats_in_progress: {label: '继续编辑 Beats', stage: 'running'},
        archived: {label: '查看归档项目', stage: 'done'},
    };
    return actions[project.status] || {label: '打开项目', stage: 'action'};
}
```

卡片内容全部通过 `textContent`、`setAttribute` 和 `append` 创建；不得把项目名、来源或状态直接插入 `innerHTML`。筛选结果为空时展示可操作空状态和“清除筛选”按钮。

- [ ] **步骤 4：验证任务页和移动端样式**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_my_tasks_is_an_operate_and_monitor_workspace tests/test_webgui_ui_changes.py::test_my_task_tables_append_rows_instead_of_stringifying_them tests/test_webgui_ui_changes.py::test_my_tasks_uses_set_button_loading tests/test_webgui_ui_changes.py::test_my_tasks_template_has_skeleton_and_aria_busy -q
```

预期：全部 PASS；若旧测试名称仍包含 `tables`，更新断言为卡片 `replaceChildren(...cards)`，不删除其安全渲染意图。

- [ ] **步骤 5：提交任务工作台**

```powershell
git add templates/common/workspace/my_tasks.html static/js/common/my_tasks.js static/css/workspaces.css tests/test_webgui_ui_changes.py
git commit -m "feat: 重构任务驱动工作台"
```

### 任务 8：把 Beats 页面改为内容优先的 Command / Inspect 编辑器

**文件：**
- 修改：`templates/common/workspace/project_beats.html`
- 修改：`static/js/writing/beats.js`
- 修改：`static/css/workspaces.css`
- 修改：`tests/test_webgui_ui_changes.py`
- 修改：`tests/test_role_page_content.py`
- 修改：`tests/test_episode_all_view_browser.py`

- [ ] **步骤 1：写入失败的聚焦编辑器测试**

在 `tests/test_webgui_ui_changes.py` 增加：

```python
def test_beats_workspace_uses_command_and_inspect_layout():
    html = _source("templates/common/workspace/project_beats.html")
    script = _source("static/js/writing/beats.js")
    assert 'class="editor-command-bar' in html
    assert 'class="beats-scene-rail' in html
    assert 'class="beats-focus-editor' in html
    assert 'class="beats-inspector' in html
    assert "selectedBeatOrder" in script
    assert "selectProjectBeat" in script
    assert "selectedProjectBeat" in script
```

在 `tests/test_episode_all_view_browser.py` 添加一个浏览器断言：选择第二个场景后，焦点编辑器显示第二个场景且修改 `goal` 会写回原始 Alpine 数据；切回第一个场景后第一条数据不变。

- [ ] **步骤 2：运行聚焦测试确认失败**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_beats_workspace_uses_command_and_inspect_layout tests/test_episode_all_view_browser.py -q
```

预期：新结构测试 FAIL；现有全集/单集行为测试保持 PASS。

- [ ] **步骤 3：实现场景导航、聚焦编辑和检查区**

保留页面顶部集数、生成、锁定、剧本和下载动作，但放入带清晰层级的 `.editor-command-bar`；单集编辑区改为三段：

1. 左侧 `.beats-scene-rail`：场景序号、场景名、是否完整；按钮使用 `aria-current`。
2. 中间 `.beats-focus-editor`：当前场景的场景、人物、目标、阻力、情绪、局面、代价、对抗策略、转折和钩子，按“场景基础 / 冲突推进 / 结果与钩子”分组。
3. 右侧 `.beats-inspector`：当前场景连续性变更和全剧连续性账本；窄屏时落到编辑器下方。

`static/js/writing/beats.js` 注册同一模块协议：

```javascript
window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    selectedBeatOrder: 1,
    selectProjectBeat(order) {
        this.selectedBeatOrder = Number(order || 1);
    },
    selectedProjectBeat() {
        const beats = this.selectedProjectBeats()?.content?.beats || [];
        return beats.find((beat) => Number(beat.order) === Number(this.selectedBeatOrder))
            || beats[0]
            || null;
    },
}));
```

模板只通过 `x-model` 编辑 `selectedProjectBeat()` 返回的原对象，不复制数据，不改变保存 payload。全集模式继续只读展示，但把 11 列表格改成每集摘要和可展开场景列表，避免首屏同时渲染所有长文本输入框。

- [ ] **步骤 4：验证编辑、全集与长文本行为**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_beats_workspace_uses_command_and_inspect_layout tests/test_webgui_ui_changes.py::test_beats_workspace_edits_continuity_updates_as_structured_fields_not_json tests/test_role_page_content.py::test_project_beats_uses_multiline_controls_for_long_content tests/test_role_page_content.py::test_project_beats_exposes_story_change_cost_and_continuity_ledger tests/test_role_page_content.py::test_project_beats_long_text_controls_wrap_without_clipping tests/test_episode_all_view_browser.py -q
```

预期：全部 PASS；保存、锁定、连续性字段、全集查看和下载入口不回归。

- [ ] **步骤 5：提交聚焦编辑器**

```powershell
git add templates/common/workspace/project_beats.html static/js/writing/beats.js static/css/workspaces.css tests/test_webgui_ui_changes.py tests/test_role_page_content.py tests/test_episode_all_view_browser.py
git commit -m "feat: 优化 Beats 内容编辑工作台"
```

### 任务 9：按页面职责拆分 `creative.js`

**文件：**
- 修改：`static/js/common/creative.js`
- 创建：`static/js/selection/drama_reviews.js`
- 创建：`static/js/common/prompt_templates.js`
- 创建：`static/js/writing/creative_episodes.js`
- 创建：`static/js/common/creative_generation.js`
- 修改：`templates/selection/topics.html`
- 修改：`templates/selection/hits.html`
- 修改：`templates/selection/dramas.html`
- 修改：`templates/planning/projects.html`
- 修改：`templates/planning/brief.html`
- 修改：`templates/planning/characters.html`
- 修改：`templates/writing/projects.html`
- 修改：`templates/writing/outlines.html`
- 修改：`templates/writing/beats.html`
- 修改：`templates/writing/scripts.html`
- 修改：`templates/common/workspace/prompt_template_center.html`
- 修改：`templates/common/workspace/prompt_template_effects.html`
- 修改：`tests/test_frontend_performance_contract.py`
- 修改：`tests/test_episode_all_view_browser.py`
- 修改：`tests/test_hongguo_provider_browser.py`
- 修改：`tests/test_original_project_browser.py`
- 修改：`tests/test_original_project_entry_regression.py`
- 修改：`tests/test_outline_episode_cards_browser.py`
- 修改：`tests/test_page_authorization.py`
- 修改：`tests/test_project_member_stage_editing_browser.py`
- 修改：`tests/test_role_page_content.py`
- 修改：`tests/test_prompt_library_ui.py`
- 修改：`tests/test_prompt_marketplace_browser.py`
- 修改：`tests/test_prompt_workspace_browser.py`
- 修改：`tests/test_video_parameter_recommendation.py`
- 修改：`tests/test_video_parameter_recommendation_browser.py`
- 修改：`tests/test_webgui_ui_changes.py`

- [ ] **步骤 1：写入失败的路由资源与体积预算**

在 `tests/test_frontend_performance_contract.py` 增加：

```python
def test_creative_modules_are_route_scoped():
    selection = source("templates/selection/topics.html")
    beats = source("templates/writing/beats.html")
    prompt = source("templates/common/workspace/prompt_template_center.html")
    assert "selection/drama_reviews.js" in selection
    assert "common/prompt_templates.js" not in selection
    assert "writing/creative_episodes.js" in beats
    assert "selection/drama_reviews.js" not in beats
    assert "common/prompt_templates.js" in prompt
    assert "writing/creative_episodes.js" not in prompt


def test_common_creative_bundle_stays_within_source_budget():
    creative = ROOT / "static/js/common/creative.js"
    assert creative.stat().st_size <= 160_000
```

在 `tests/test_role_page_content.py` 的 JavaScript 语法参数列表中加入四个新文件，并验证所有模块脚本先于 Alpine 初始化完成注册。

- [ ] **步骤 2：运行测试确认资源仍未拆分**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py::test_creative_modules_are_route_scoped tests/test_frontend_performance_contract.py::test_common_creative_bundle_stays_within_source_budget -q
```

预期：FAIL；当前 `creative.js` 约 277 KB，模板未加载拆分模块。

- [ ] **步骤 3：按已有方法边界做机械提取**

每个新文件都使用同一包装，不改方法名、参数、请求 URL、payload 和状态字段：

```javascript
window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
    // 原方法逐个原样迁入此对象。
}));
```

按以下真实方法组迁移：

- `selection/drama_reviews.js`：`dramaReviewStatusLabel` 至 `openTopicReviewCapture`。
- `writing/creative_episodes.js`：`latestProjectBlueprints` 至 `downloadSelectedEpisodes`，但项目通用标签、可编辑判断和任务解析继续留在 `creative.js`。
- `common/prompt_templates.js`：`promptStageLabel` 至 `saveImportedPromptTemplate`，包含治理、观察、市场、导入和绑定能力。
- `common/creative_generation.js`：`openFreeCreativeGeneration` 至文件末尾，包含生成、进度、重试、保存、锁定和下载 URL 能力。
- `creative.js`：保留项目列表/选择、原创建、模式标签、通用 helper、权限能力和 `resolveCreativeWorkflowTask`。

迁移过程只剪切现有方法并修复末尾逗号；不同时重命名、格式化或改写业务逻辑。

- [ ] **步骤 4：按路由显式组合模块**

脚本顺序必须是：`creative.js` → 页面领域模块 → `creative_generation.js` → 页面自身脚本。具体组合：

- `templates/selection/topics.html`、`templates/selection/hits.html`：`creative.js` + `selection/drama_reviews.js` + 原页面脚本。
- `templates/selection/dramas.html`：`creative.js` + 原 `selection/dramas.js`，不加载提示词、写作和生成模块。
- `templates/planning/projects.html`、`templates/planning/brief.html`、`templates/planning/characters.html`：`creative.js` + `prompt_templates.js` + `creative_generation.js` + 原页面脚本。
- `templates/writing/outlines.html`、`templates/writing/beats.html`、`templates/writing/scripts.html`：`creative.js` + `writing/creative_episodes.js` + `prompt_templates.js` + `creative_generation.js` + 原页面脚本。
- `templates/writing/projects.html`：只加载 `creative.js`，因为该页只列出并打开项目。
- `templates/common/workspace/prompt_template_center.html`、`templates/common/workspace/prompt_template_effects.html`：`creative.js` + `prompt_templates.js`，不加载评论、写作和生成模块。

更新“文件”列表中的浏览器 fixture，使其按生产模板相同顺序注入拆分脚本；更新直接读取 `creative.js` 的断言，使其读取真正承载该方法的新模块。不通过在 `creative.js` 留重复代码来让旧测试通过。

- [ ] **步骤 5：验证语法、模块契约和关键流程**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_role_page_content.py::test_all_workspace_javascript_has_valid_syntax tests/test_prompt_library_ui.py tests/test_webgui_ui_changes.py -q
uv run pytest tests/test_episode_all_view_browser.py tests/test_hongguo_provider_browser.py tests/test_original_project_browser.py tests/test_original_project_entry_regression.py tests/test_outline_episode_cards_browser.py tests/test_page_authorization.py tests/test_project_member_stage_editing_browser.py tests/test_prompt_marketplace_browser.py tests/test_prompt_workspace_browser.py tests/test_video_parameter_recommendation.py tests/test_video_parameter_recommendation_browser.py -q
```

预期：全部 PASS；`creative.js` 不超过 160,000 bytes；选题页不下载提示词/写作模块，提示词页不下载写作模块。

- [ ] **步骤 6：提交资源拆分**

```powershell
git add static/js/common/creative.js static/js/selection/drama_reviews.js static/js/common/prompt_templates.js static/js/writing/creative_episodes.js static/js/common/creative_generation.js templates/selection/topics.html templates/selection/hits.html templates/selection/dramas.html templates/planning/projects.html templates/planning/brief.html templates/planning/characters.html templates/writing/projects.html templates/writing/outlines.html templates/writing/beats.html templates/writing/scripts.html templates/common/workspace/prompt_template_center.html templates/common/workspace/prompt_template_effects.html tests/test_frontend_performance_contract.py tests/test_episode_all_view_browser.py tests/test_hongguo_provider_browser.py tests/test_original_project_browser.py tests/test_original_project_entry_regression.py tests/test_outline_episode_cards_browser.py tests/test_page_authorization.py tests/test_project_member_stage_editing_browser.py tests/test_role_page_content.py tests/test_prompt_library_ui.py tests/test_prompt_marketplace_browser.py tests/test_prompt_workspace_browser.py tests/test_video_parameter_recommendation.py tests/test_video_parameter_recommendation_browser.py tests/test_webgui_ui_changes.py
git commit -m "perf: 按路由拆分创作工作台脚本"
```

提交前使用 `git diff --cached --name-only` 确认 `templates` 只包含实际资源引用变更，没有把工作区内无关模板带入提交。

### 任务 10：让轮询可暂停、可销毁且不重叠

**文件：**
- 修改：`static/js/common/tasks.js`
- 修改：`static/js/common/creative_generation.js`
- 修改：`tests/test_webgui_ui_changes.py`
- 修改：`tests/test_llm_settings_browser.py`

- [ ] **步骤 1：写入失败的轮询生命周期测试**

在 `tests/test_webgui_ui_changes.py` 增加：

```python
def test_workspace_polling_pauses_when_hidden_and_stops_on_pagehide():
    tasks = _source("static/js/common/tasks.js")
    assert "pollers: new Map()" in tasks
    assert "schedulePoller(name" in tasks
    assert "clearPollers()" in tasks
    assert "document.addEventListener('visibilitychange'" in tasks
    assert "window.addEventListener('pagehide'" in tasks
    assert "setInterval(() => this.fetchLogs(), 1000)" not in tasks
    assert "setInterval(() => this.fetchJobs()" not in tasks
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_workspace_polling_pauses_when_hidden_and_stops_on_pagehide tests/test_webgui_ui_changes.py::test_jobs_polling_is_scoped_to_pages_that_consume_job_state -q
```

预期：生命周期测试 FAIL，页面范围测试 PASS。

- [ ] **步骤 3：用递归 timeout 替代固定 interval**

在 `appData()` 状态中加入 `pollers: new Map()` 和下列实现：

```javascript
schedulePoller(name, callback, intervalMs, immediate = false) {
    this.stopPoller(name);
    const poller = {callback, intervalMs, timer: null, running: false};
    this.pollers.set(name, poller);
    const run = async () => {
        if (document.hidden || poller.running || !this.pollers.has(name)) return;
        poller.running = true;
        try {
            await callback();
        } finally {
            poller.running = false;
            if (!document.hidden && this.pollers.has(name)) {
                poller.timer = window.setTimeout(run, poller.intervalMs);
            }
        }
    };
    poller.run = run;
    poller.timer = window.setTimeout(run, immediate ? 0 : intervalMs);
},
stopPoller(name) {
    const poller = this.pollers.get(name);
    if (poller?.timer) window.clearTimeout(poller.timer);
    this.pollers.delete(name);
},
pausePollers() {
    for (const poller of this.pollers.values()) {
        if (poller.timer) window.clearTimeout(poller.timer);
        poller.timer = null;
    }
},
resumePollers() {
    for (const poller of this.pollers.values()) {
        if (!poller.running && !poller.timer) poller.timer = window.setTimeout(poller.run, 0);
    }
},
clearPollers() {
    for (const name of [...this.pollers.keys()]) this.stopPoller(name);
},
```

`init()` 中仅为需要 job 状态的页面注册 5 秒 job 轮询，仅为 `admin-jobs` 注册 1 秒日志轮询。`visibilitychange` 隐藏时调用 `pausePollers()`，恢复可见时调用 `resumePollers()`；`pagehide` 调用 `clearPollers()`。在 `creative_generation.js` 的 `startCreativeGeneration()` 中启动名为 `creative-elapsed` 的 1 秒 poller，在 `finishCreativeGeneration()`、失败、取消和离开页面时停止，因此没有活跃生成任务时不存在秒表定时器。

- [ ] **步骤 4：验证无重叠请求和隐藏页行为**

在 `tests/test_llm_settings_browser.py` 的 Playwright 环境中 mock `fetchJobs` 为可控 Promise：连续推进 10 秒 fake timer 时，前一次未完成不得发起第二次；触发 `visibilitychange` 后计数停止，恢复可见后只立即增加一次。

运行：

```powershell
uv run pytest tests/test_webgui_ui_changes.py::test_workspace_polling_pauses_when_hidden_and_stops_on_pagehide tests/test_webgui_ui_changes.py::test_jobs_polling_is_scoped_to_pages_that_consume_job_state tests/test_llm_settings_browser.py -q
```

预期：全部 PASS；不存在重叠轮询，隐藏页没有持续网络请求。

- [ ] **步骤 5：提交轮询优化**

```powershell
git add static/js/common/tasks.js static/js/common/creative_generation.js tests/test_webgui_ui_changes.py tests/test_llm_settings_browser.py
git commit -m "perf: 管理工作台轮询生命周期"
```

### 任务 11：缓存静态资源版本并移除 `/tasks` 无关运行时

**文件：**
- 修改：`web/page_router.py`
- 修改：`tests/test_page_authorization.py`
- 修改：`tests/test_frontend_performance_contract.py`

- [ ] **步骤 1：把现有动态版本测试改成进程级缓存测试**

在 `tests/test_page_authorization.py` 用下列意图替换“每次文件改变都立即变化”的测试：

```python
def test_asset_version_scans_static_tree_once(tmp_path: Path, monkeypatch):
    static_root = tmp_path / "static"
    static_root.mkdir()
    asset = static_root / "app.js"
    asset.write_text("one", encoding="utf-8")
    monkeypatch.setattr(page_router, "STATIC_ASSET_ROOT", static_root)
    page_router.static_asset_version.cache_clear()

    os.utime(asset, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

    first = page_router.static_asset_version()
    asset.write_text("two", encoding="utf-8")
    os.utime(asset, ns=(1_700_000_002_000_000_000, 1_700_000_002_000_000_000))

    assert page_router.static_asset_version() == first
    page_router.static_asset_version.cache_clear()
    assert page_router.static_asset_version() != first
```

再增加：

```python
def test_tasks_page_does_not_load_workspace_runtime(page_app):
    client = _client(page_app, _principal(roles=("system_admin",)))
    response = client.get("/tasks")
    assert response.status_code == 200
    assert "/static/js/common/my_tasks.js" in response.text
    assert "/static/js/common/tasks.js" not in response.text
    assert "/static/js/common/creative.js" not in response.text
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```powershell
uv run pytest tests/test_page_authorization.py -k "asset_version or tasks_page_does_not_load_workspace_runtime" -q
```

预期：FAIL；当前函数每次递归扫描 `static`，且 `/tasks` 仍加载 `common/tasks.js`。

- [ ] **步骤 3：缓存扫描并为任务页关闭工作区运行时**

在 `web/page_router.py` 增加可单测的扫描函数和标准库缓存：

```python
from functools import lru_cache


def _scan_static_asset_version() -> str:
    return str(max(
        (path.stat().st_mtime_ns for path in STATIC_ASSET_ROOT.rglob("*") if path.is_file()),
        default=0,
    ))


@lru_cache(maxsize=1)
def static_asset_version() -> str:
    """Return the process-level version for static browser assets."""
    return _scan_static_asset_version()
```

生产进程启动后静态文件应由重新部署替换，开发模式文件改变会触发应用 reload；测试通过 `cache_clear()` 隔离状态。

把 `_page_response()` 的运行时判定改成显式集合，使 `common/my_tasks.html` 与两个 dashboard 一样不加载 Alpine 工作区：

```python
workspace_runtime=template_name not in {
    "common/my_tasks.html",
    "selection/dashboard.html",
    "writing/dashboard.html",
},
```

- [ ] **步骤 4：补充静态性能预算**

在 `tests/test_frontend_performance_contract.py` 加入 `/tasks` 模板只引用 `my_tasks.js` 的静态断言。在 `tests/test_page_authorization.py` 用 `monkeypatch` 把 `_scan_static_asset_version` 替换为递增计数函数，清空 cache 后连续调用 `static_asset_version()` 两次，断言计数为 1；测试结束再次 `cache_clear()`，避免影响其他测试。

运行：

```powershell
uv run pytest tests/test_page_authorization.py tests/test_frontend_performance_contract.py -q
```

预期：全部 PASS；静态树每进程扫描一次，任务页不下载约 60 KB 的 `tasks.js`。

- [ ] **步骤 5：提交静态资源优化**

```powershell
git add web/page_router.py tests/test_page_authorization.py tests/test_frontend_performance_contract.py
git commit -m "perf: 缓存静态版本并精简任务页资源"
```

---

## 最终验收

### 任务 12：运行合并后的定向验收

**文件：**
- 不修改文件；若命令失败，只修改导致失败的任务文件，并追加到对应任务提交。

- [ ] **步骤 1：确认工作区隔离**

运行：

```powershell
git status --short
git log --oneline -11
```

预期：最近 11 个提交与任务 1–11 一一对应；没有 `.uv-cache*`、`.ruff_cache/` 或用户原有未提交文件被意外暂存。

- [ ] **步骤 2：验证工具链**

运行：

```powershell
uv lock --check
uv sync --locked --group dev
uv run ruff check .
uv run ty check
uv run pre-commit run ruff-check --all-files
```

预期：全部退出码为 0；ty 只覆盖四个 schema 文件；Ruff 没有 `--fix`、formatter、大范围 `noqa` 或 per-file ignore。

- [ ] **步骤 3：验证 UI 与性能契约**

运行：

```powershell
uv run pytest tests/test_frontend_performance_contract.py tests/test_page_authorization.py tests/test_role_page_content.py tests/test_webgui_ui_changes.py -q
uv run pytest tests/test_original_project_browser.py tests/test_episode_all_view_browser.py tests/test_prompt_workspace_browser.py tests/test_prompt_marketplace_browser.py tests/test_llm_settings_browser.py -q
```

预期：全部 PASS。必要测试通过后停止，不运行无关数据库集成套件。

- [ ] **步骤 4：核对性能预算和资源归属**

运行：

```powershell
Get-Item static/js/common/creative.js,static/js/common/tasks.js,static/js/common/prompt_templates.js,static/js/writing/creative_episodes.js,static/js/common/creative_generation.js | Select-Object Name,Length
rg -n "echarts\.min\.js|creative\.js|creative_episodes\.js|prompt_templates\.js|drama_reviews\.js" templates
```

预期：

- `static/js/common/creative.js` 不超过 160,000 bytes。
- ECharts 仍只由 `templates/admin/analytics.html` 引用。
- `/tasks` 不加载 `common/tasks.js`、`creative.js` 或创作领域模块。
- selection 页面不加载提示词和写作模块；prompt 页面不加载写作模块；writing 页面不加载评论采集模块。
- 隐藏标签页不继续 jobs/logs 轮询，任何轮询都不会并发重叠。
- 静态资源版本目录扫描每进程最多一次。

- [ ] **步骤 5：人工可用性检查**

运行：

```powershell
uv run python WebGUI.py
```

在 `http://localhost:8080/tasks` 和任一 Beats 编辑 URL 以 1440×900、1024×768、390×844 三个视口检查：键盘可到达跳转链接、搜索、筛选、场景导航和主要动作；焦点清晰；文本不截断；窄屏无页面级横向滚动；加载、空数据、错误、进行中、成功和禁用状态均可辨识。检查完终止服务。

预期：任务页第一屏能回答“现在要做什么”；Beats 页第一屏能回答“正在编辑哪一集、哪一场、是否可保存/锁定”；视觉保持现有深色品牌且没有新框架痕迹。

## 完成标准

1. `pyproject.toml` 是唯一人工维护的 Python 依赖源，`uv.lock` 可复现，仓库没有 `requirements.txt` 安装入口。
2. Ruff 的 `E9,F63,F7,F82` 全仓通过；pre-commit 检查本次 Python 文件，CI 检查全仓。
3. ty 只检查 `identity/schema.py`、`analysis/schema.py`、`workflow/schema.py`、`writer_agent/schema.py`，四文件零诊断，仍处于 CI 观察阶段。
4. `/tasks` 成为清晰的 Operate + Monitor 页面；Beats 成为 Command / Inspect 编辑器；现有 API、保存 payload、权限和创作流程保持不变。
5. 前端资源按路由加载，`creative.js` 降到 160 KB 以内；ECharts 不进入基础壳；`/tasks` 不加载工作区运行时。
6. 静态资源版本不再每个请求递归扫描目录；隐藏页不轮询，轮询无重叠且页面离开后销毁。
7. 所列定向测试全部通过，三种视口可用性检查完成，且没有覆盖或提交用户原有未提交改动。

## 明确不在本计划内

- Ruff formatter、全仓 import 排序、全仓 ty、删除 `mypy.ini`。
- Playwright、LLM、ASR、视频处理、数据库查询或平台网络请求的运行时 profiling 与架构改写。
- 新增 SPA 框架、CSS 框架、动画库、图表库或前端打包器。
- 修改创作 API、项目状态机、权限模型、数据库 schema 或任务归属规则。
- 为达到体积预算进行压缩/混淆；本计划通过职责拆分减少实际路由下载与解析量。
