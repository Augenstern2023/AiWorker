# 任务 1：统一 Beats 业务约束报告

## 工作区与范围

- 工作区：`D:\Crawler\AiWorker\.workspace\beats-three-scene-focus-20260924`
- 未修改计划文件。
- 未修改任务 2/3 的前端模板、JavaScript 或 CSS。
- 未启动或停止服务。

## 修改文件

- `creative/script_service.py`
  - `validate_beats` 统一限制为 1-3 个；先检查数量再走 legacy 兼容分支，因此 legacy 数据可读但 4 个及以上会被拒绝，不会截断。
- `creative/prompt_templates.py`
  - 单集和批量 Beats 提示词同步为 1-3。
- `creative/prompt_registry.py`
  - 四个内置模板的 `max_beats` 同步为 3。
  - 模板内容和运行时参数覆盖中的 `max_beats` 均校验为整数 1-3，超限直接拒绝。
- `creative/free_mode_service.py`
  - 自由模式 Beats 写入（保留空草稿例外）及单集/全集锁定复用 `validate_beats`；超限资源不会被锁定。
- `screenplay_optimization/api.py`
  - 优化草稿 Beats 保存已有共同校验，采纳前补充共同校验，避免绕过校验写入或锁定。
- `creative/repositories/versions.py`、`creative/legacy_sqlite_repository.py`
  - 单集 Beats 创建入口补充共同校验，防止直接 repository 写入绕过服务层。
- `tests/test_creative_phase4_generation.py`、`tests/test_prompt_library.py`
  - 更新 1-3 契约，补充 3 个通过、4 个拒绝、legacy 超限拒绝和模板覆盖超限拒绝断言。

## 测试与真实输出

命令：

```text
uv run pytest tests/test_creative_phase4_generation.py tests/test_prompt_library.py tests/test_creative_free_mode.py -q
```

输出：

```text
2 passed, 194 skipped in 7.08s
```

其中自由模式及部分 creative 测试依赖 PostgreSQL fixture，在当前环境被 pytest 自动跳过；未将跳过误报为通过。

命令：

```text
uv run pytest tests/test_creative_phase4_generation.py::test_beats_generation_limits_an_episode_to_three_beats tests/test_creative_phase4_generation.py::test_legacy_beats_are_readable_within_limit_but_not_when_over_limit tests/test_prompt_library.py::test_included_examples_and_parameter_overrides_reach_effective_instruction -q
```

输出：

```text
3 skipped
```

命令：

```text
uv run python -m compileall -q creative screenplay_optimization
```

输出：无输出，退出码 0。

另外执行了无外部服务依赖的 Python 边界检查，真实输出：

```text
validator: 1,2,3 accepted; 4 rejected; legacy over-limit rejected
prompt templates: single/batch 1-3; builtin max_beats <= 3; override 4 rejected
```

## 疑虑与后续

- PostgreSQL 相关自由模式和优化 API 集成测试因环境 fixture 不可用而未实际执行；代码路径已补充共同 validator，但建议在具备 PostgreSQL 的 CI/验收环境复跑对应测试。
- 空白自由 Beats 草稿仍允许暂存，锁定/采纳时必须通过非空的 1-3 校验，符合计划中的空草稿例外。

## 后续修复：自由模式边界（2026-09-24）

- 修复 `creative/free_mode_service.py` 中 `create_episode_resource` 的错误校验位置，确保不会引用未定义的 `resource_ref`。
- `update_resource`、`update_item` 对非空 Beats 内容调用 `validate_beats(..., allow_legacy=True)`。
- `lock_resource` 在 Beats 更新状态前读取当前资源内容并校验；超限时事务提前失败，不执行状态更新。
- 新增 `tests/test_free_mode_beats_validation.py`，覆盖创建、资源更新、单项更新和锁定前拒绝超限。

本次验证：

```text
uv run pytest tests/test_free_mode_beats_validation.py -q
4 passed in 0.17s

uv run pytest tests/test_creative_free_mode.py -q
2 passed, 40 skipped in 0.55s
```

自由模式主测试仍有 PostgreSQL 依赖项被跳过；新增测试不依赖数据库并实际执行通过。
