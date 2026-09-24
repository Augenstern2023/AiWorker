# 任务 2 报告：Beats 三场景聚焦式 UI

## 审查与修复

- 单集编辑器首屏保留并突出「场景、人物、转折、结尾钩子」四个核心字段；详细分析字段与连续性检查默认折叠。
- 全集视图使用逐集摘要卡，显示状态、场次数量 `N/3`、四个核心字段和单集入口；历史超限数据只读展示，不截断。
- 单集场次导航、新增按钮、保存和完成并锁定按钮保留权限与状态判断；超限时显示 `N/3` 警告并禁用保存/锁定/新增，批量锁定也会阻断超限数据。
- `creativeTemplateProvenance` 对缺少模板元数据的历史资源统一显示「经典兼容模板」，不改变 payload 或 API。
- 修正切换集数后场次选择可能落空的问题，保持当前集首个有效场次可见。
- 增强 Beats 样式的长文本换行、核心字段语义色彩及 1440/1024/390 断点；场次导航、`aria-live` 状态提示和按钮 aria 文案保留键盘操作路径。

## 验证

命令：

```text
uv run pytest tests/test_prompt_library_ui.py tests/test_role_page_content.py::test_project_beats_uses_multiline_controls_for_long_content tests/test_episode_all_view_browser.py -q
node --check static/js/common/creative.js
git diff --check
```

结果：

- `28 passed`。
- JavaScript 语法检查通过（退出码 0）。
- `git diff --check` 通过；仅有 Git 关于现有 LF/CRLF 的提示。

## 范围与提交

本任务仅提交 Beats UI 文件、对应 UI 契约测试和本报告。未提交或修改 `creative/free_mode_service.py`、`static/js/writing/optimization_draft.js`。

## 定向修复（2026-09-24）

- 以 `b3b71395` 的 `project_beats.html` 为基准确认模板已恢复并保持受版本控制，未留下删除状态。
- 单集编辑区调用已有 `removeProjectBeat`，补充可访问的删除场次按钮；权限/锁定状态仍通过 `creativeResourceEditable` 禁用，历史超限可删除场次恢复到 3 以内。
- 全集卡片移除模板来源，只保留版本状态、`N/3`、四个核心字段与单集入口；单集 provenance 保留。
- 收紧连续性账本及其输入项的最小宽度、表格固定布局和长文本换行，避免 1024/390 页面级横向溢出。
- 场次导航改为完整换行并提供包含场景与钩子的 `aria-label`，不再依赖截断文本。
