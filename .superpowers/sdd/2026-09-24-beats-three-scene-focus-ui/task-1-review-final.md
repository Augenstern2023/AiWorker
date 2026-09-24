# 任务 1 定向规格与代码质量复审

## 范围与结论

- 审查提交：`6c8b18d8`、`08ac56be`、`e151aa59`
- 参考材料：`docs/superpowers/plans/2026-09-24-beats-three-scene-focus-ui.md`、
  `.superpowers/sdd/2026-09-24-beats-three-scene-focus-ui/task-1-report.md`
- 审查方式：只读检查提交 diff、最终提交树和报告；未重跑报告中已通过的测试。

**规格合规 ❌**

**代码质量 ❌**

核心校验、自由模式锁定和优化 API 的保存/采纳入口已接入，但优化任务生成阶段仍有服务端写入路径绕过共同 validator，不能判定任务 1 完全合规。

## 规格逐项核对

| 要求 | 结论 | 证据 |
|---|---|---|
| 1、2、3 通过；4 拒绝 | ✅ | `creative/script_service.py:374-404` 先拒绝空值/超限，再做字段与序号校验；`tests/test_creative_phase4_generation.py:1166-1196` 覆盖 1/2/3 与 4、legacy 超限。 |
| legacy 只读兼容；超限写入/锁定拒绝 | ✅（就目标提交可见路径） | `creative/repositories/versions.py:2678-2707` 的读取只解析内容，不调用 validator；创建/更新/锁定及批量锁定分别在 `:1871-1887`、`:2027-2028`、`:2717-2721`、`:2791-2796` 校验。SQLite 对应路径也有同等写入/锁定校验。 |
| 自由模式 `beats:[]` 可暂存、锁定非法 | ✅ | `creative/free_mode_service.py:678-679`、`:742-743`、`:810-811` 对空数组保留暂存例外；`:971-980` 与 `:1053-1058` 锁定前无条件调用 validator，空数组及超限均拒绝。 |
| 优化草稿保存/采纳 | ❌ | API 的用户保存与采纳分别在 `screenplay_optimization/api.py:407-417`、`:513-618` 校验；但生成阶段写入见下方 Important。 |
| 单集/批量 prompt 与 `max_beats=3` | ✅ | `creative/prompt_templates.py:212-238` 明确单集和批量均为 1-3；四个内置模板在 `creative/prompt_registry.py:150`、`:185`、`:223`、`:261` 均为 3，模板及覆盖校验在 `:347-354`、`:417-425`。 |
| 不改变 payload/状态机 | ✅（从目标 diff 可核实） | 变更只增加校验/提示文本/参数约束；优化采纳仍沿用原有创建、锁定、`accept_script_optimization_draft` 顺序，未改 API 请求模型或状态流转。 |

## 审查问题

### Critical

无。

### Important

1. **❌ 优化草稿生成阶段的 Beats 写入绕过共同 validator。**
   - `creative/repositories/reviews.py:651-729` 的 `save_script_optimization_episode` 直接把 `draft["content"]` 写入 `script_optimization_drafts`，没有按 `kind == "beats"` 调用 `validate_beats`。
   - 同一 PostgreSQL repository 的批量入口 `creative/repositories/reviews.py:760-814`，以及 legacy SQLite 入口 `creative/legacy_sqlite_repository.py:6258-6310` 也直接插入草稿内容。
   - 实际 worker 调用位于 `screenplay_optimization/worker.py:230`。因此模型生成 4 个 Beats 时仍可先持久化为优化草稿；API 的后续编辑/采纳校验（`screenplay_optimization/api.py:408-415`、`:516-518`）只能阻止后续动作，不能满足“所有服务端写入入口共同防线”。
   - 建议：在上述草稿创建/批量 checkpoint 的事务写入前，对 `beats` 调用 `validate_beats(..., allow_legacy=True)`；不要截断，并保留 legacy 超限只读语义。

### Minor

1. **⚠️ 空草稿例外的边界略宽。** `creative/free_mode_service.py:678`、`:742`、`:810` 用 `(content.get("beats") or [])` 判断，因此缺少 `beats` 键或 `beats: null` 也会绕过暂存校验。锁定仍会失败，所以未形成超限写入问题；若规格严格限定只有 `beats: []` 可暂存，建议收窄为显式空数组判断。
2. **⚠️ 相关集成证据不完整（`screenplay_optimization/api.py:407-417`、`:513-618`）。** 报告记录 PostgreSQL 相关自由模式/优化 API 测试因 fixture 不可用而跳过，无法从本次 diff 独立证明真实数据库事务中的保存、锁定和采纳拒绝行为。

## 已有验证输出（未重跑）

以下输出来自关联报告，本次按要求未重跑已通过测试：

```text
uv run pytest tests/test_creative_phase4_generation.py tests/test_prompt_library.py tests/test_creative_free_mode.py -q
2 passed, 194 skipped in 7.08s

uv run pytest tests/test_free_mode_beats_validation.py -q
4 passed in 0.17s

uv run pytest tests/test_creative_free_mode.py -q
2 passed, 40 skipped in 0.55s

uv run pytest tests/test_prompt_workspace_browser.py -q
6 passed in 11.75s

uv run pytest tests/test_creative_phase4_generation.py -q
114 skipped in 1.48s

uv run python -m compileall -q creative screenplay_optimization
退出码 0，无输出
```

报告另有独立边界检查输出：`1/2/3 accepted; 4 rejected`；模板检查确认单集/批量规则为 1-3、内置 `max_beats <= 3`、覆盖 4 被拒绝。
