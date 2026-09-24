# 仿写剧目与来源原剧目对比设计

## 1. 背景

当前仿写项目通过 `creative_projects.topic_id`、`breakdown_id` 关联来源选题及原剧拆解，通过 `locked_brief_id` 关联当前锁定的仿写方向 Brief。系统已有仿写方向中的 `borrowed_elements`、`different_elements` 和相似风险评估，但项目详情页还没有把原剧与仿写内容放在一起进行创作差异检查。

本功能首期服务于创作质量检查：帮助创作者判断仿写剧是否形成了新的主角目标、冲突机制、卖点和开篇钩子，而不是只替换人物名称或故事背景。

## 2. 目标与范围

### 目标

- 在仿写项目详情页提供“原剧对比”视图。
- 基于项目当前绑定的原剧拆解版本和仿写 Brief 生成结构化对比。
- 明确区分保留、改造、新增、未体现和无法判断的内容。
- 输出可执行的创新补强建议，并保留分析所依据的字段。
- 在原始字段对比可用但 LLM 不可用时，仍能展示确定性结果。

### 首期不做

- 不比较分集大纲、Beats、场次剧本或对白正文。
- 不把结果作为法律意义上的抄袭、侵权或版权结论。
- 不自动修改 Brief、人物卡或大纲。
- 不要求原剧存在逐集剧本或完整转写。
- 不对原创项目显示该功能。

## 3. 数据边界与版本规则

只允许对 `creation_mode = 'imitation'` 的项目生成报告。

原剧来源严格使用：

- `creative_projects.topic_id`
- `creative_projects.breakdown_id`

仿写来源严格使用：

- `creative_projects.locked_brief_id`

不得在对比时自动替换为“最新草稿”或“最新拆解”。项目绑定的来源版本发生变化后，旧报告应被识别为过期，不能静默复用。

如果缺少原剧拆解或锁定 Brief，接口返回可操作的资料不完整状态，前端提示用户先完成对应工作流。

## 4. 对比维度与字段映射

服务层先把两类内容归一化为统一的比较模型，再执行字段对比和 AI 分析。首期字段映射如下：

| 对比维度 | 原剧拆解 | 仿写 Brief/项目 | 判断重点 |
| --- | --- | --- | --- |
| 故事定位 | `title`、`category`、`target_audience` | `title`、项目 `genre`、`audience`、项目 `target_audience` | 是否只是更换包装 |
| 主角设定 | `protagonist_identity` | `protagonist` | 身份、目标和人物功能是否变化 |
| 核心冲突 | `resistance_source`、`core_conflict` | `core_conflict`、`main_goal` | 冲突来源和解决路径是否重构 |
| 卖点设计 | `one_line_selling_point`、`selling_points` | `highlights`、`imitation_reason` | 是否形成新的观看理由 |
| 开篇钩子 | `hook_points` | `first_three_hooks` | 钩子是复用还是重新设计 |
| 反转与情绪 | `reversal_nodes`、`emotional_curve` | Brief 中可对应的结构描述 | 是否有新的情绪推进方式 |
| 背景与世界 | 原剧拆解中的相关字段 | `background`、`worldview` | 背景变化是否改变了故事机制 |
| 借鉴元素 | `imitable_elements` | `borrowed_elements` | 借鉴是否被有效转化 |
| 创新元素 | 原剧对应维度 | `different_elements` | 差异是否具体、可验证 |
| 信息缺口 | 原剧所有已提供字段 | 仿写 Brief 缺少的对应内容 | 是否因资料不足无法判断 |

每个维度至少返回原剧值、仿写值、变化类型、说明和依据字段。不能从未提供的字段推断具体事实，应标记为“资料不足”或“无法判断”。

变化类型固定为：

- `retained`：主要保留
- `adapted`：进行了明确改造
- `new`：仿写新增
- `missing`：原剧有而仿写未体现
- `unclear`：资料不足或需要人工确认

## 5. 报告模型

建议新增独立的对比报告资源，报告主体存储为 JSON。接口返回模型如下：

```json
{
  "schema_version": "1",
  "analysis_status": "completed",
  "source": {
    "topic_id": 1,
    "breakdown_id": 2,
    "breakdown_version": 3,
    "title": "原剧标题"
  },
  "imitation": {
    "project_id": 10,
    "brief_id": 11,
    "brief_version": 2,
    "title": "仿写剧标题"
  },
  "summary": {
    "innovation_level": "partial",
    "conclusion": "……",
    "retained_elements": [],
    "innovations": [],
    "similarity_concerns": [],
    "revision_suggestions": []
  },
  "dimensions": [
    {
      "key": "core_conflict",
      "label": "核心冲突",
      "source_value": "……",
      "imitation_value": "……",
      "change_type": "adapted",
      "analysis": "……",
      "evidence": ["source.core_conflict", "imitation.core_conflict"],
      "confidence": "high"
    }
  ],
  "completeness": {
    "source_missing_fields": [],
    "imitation_missing_fields": [],
    "manual_review_items": []
  },
  "limitations": ["本报告用于创作差异检查，不构成法律结论。"]
}
```

`innovation_level` 仅用于创作反馈，取值为 `strong`、`partial`、`weak`、`insufficient`，不能命名为版权风险等级，也不能替代已有风险评估字段。

## 6. 后端组件与持久化

新增 `creative/source_comparison_service.py`，职责包括：

1. 读取项目、原剧拆解和当前锁定 Brief。
2. 校验项目模式、来源归属和来源版本。
3. 将两侧内容归一化为比较模型。
4. 执行确定性字段对比。
5. 调用 LLM 生成结构化创新分析。
6. 校验分析 JSON；失败时按现有 LLM 修复模式重试一次。
7. 生成来源内容指纹并管理报告缓存。

建议新增 `creative_project_source_comparisons` 表，至少包含：

- `id`
- `project_id`
- `topic_id`
- `breakdown_id`
- `breakdown_version`
- `brief_id`
- `brief_version`
- `source_fingerprint`
- `status`：`pending`、`running`、`completed`、`failed`
- `comparison_json`
- `generation_meta_json`
- `error`
- `created_at`、`updated_at`

相同项目和相同来源指纹优先返回最近的已完成报告；来源指纹变化时生成新报告。失败记录保留错误信息，但不覆盖上一份可用报告。

## 7. API

```http
GET  /api/creative/projects/{project_id}/source-comparison
POST /api/creative/projects/{project_id}/source-comparison/analyze
```

### GET

返回：

- 项目是否适用；
- 原剧和仿写来源元数据；
- 确定性字段对比；
- 最近一次报告及其是否过期；
- 当前资料缺口。

### POST

生成或重新生成 AI 分析。请求需要沿用项目已有的幂等和权限约定；生成完成后返回完整报告。若 LLM 不可用，仍返回确定性字段对比及 `analysis_status = unavailable`，不能伪造 AI 结论。

两个接口均复用现有项目读取权限。服务层必须再次校验资源属于该项目，防止通过外部 ID 读取其他项目的 Brief 或拆解。

## 8. 前端交互

在项目详情页增加“原剧对比”页签：

1. **原创项目**：不显示页签。
2. **资料不完整**：显示缺失来源及对应操作提示。
3. **已有报告**：显示总结卡片、维度对比表和信息完整性提示。
4. **首次分析**：显示确定性对比，并提供“生成创新分析”按钮。
5. **分析中**：显示加载状态，禁止重复提交。
6. **分析失败**：保留字段对比，显示错误原因和“重新分析”按钮。

页面重点展示“借鉴了什么、改变了什么、还缺什么”，避免将风险分数作为主视觉指标。报告顶部显示原剧拆解版本、Brief 版本、生成时间和“基于当前绑定版本”的说明。

## 9. AI 分析约束

提示词必须要求模型：

- 只依据输入字段进行判断；
- 对每个结论给出字段依据；
- 对未提供的信息返回“资料不足”；
- 区分保留、改造、新增、未体现和无法判断；
- 给出具体可执行的创新建议；
- 不输出法律结论，不使用“侵权”“抄袭已成立”等表述；
- 严格返回约定的 JSON 结构。

AI 结果只作为创作辅助，确定性的字段对比应独立于 AI 结果存在。

## 10. 异常与降级

- 项目不存在：沿用现有 404 行为。
- 项目为原创：返回不适用状态，不调用 LLM。
- 缺少原剧拆解：返回“请先完成并锁定原剧拆解”。
- 缺少 Brief：返回“请先锁定仿写方向”。
- 来源版本不属于项目：返回 409，禁止继续比较。
- LLM 服务不可用：展示确定性对比，标记分析不可用。
- LLM JSON 校验失败：自动修复一次，仍失败则保存失败状态并返回字段对比。
- 报告已过期：允许查看旧报告，但明确显示过期并提供重新分析入口。

## 11. 测试与验收

### 后端单元测试

- 原剧和 Brief 字段映射正确。
- 版本和项目归属校验正确。
- 五种变化类型判定正确。
- 缺失字段不会被误判为创新或相似。
- 来源指纹相同时命中缓存，变化后报告过期。
- AI 非法 JSON 的一次修复和最终降级行为正确。

### API 测试

- 原创项目不允许生成对比报告。
- 仿写项目可读取当前绑定来源。
- 跨项目的 Brief、拆解 ID 被拒绝。
- 无权限用户无法读取报告。
- LLM 不可用时接口仍返回确定性对比。

### 前端验收

- 资料完整、资料缺失、分析中、分析失败四种状态均可操作。
- 页面明确显示原剧版本和 Brief 版本。
- 不把创作创新等级展示为法律或版权判断。
- 报告刷新后仍能读取已保存结果。

## 12. 验收标准

- 创作者能在仿写项目详情页打开“原剧对比”。
- 能同时看到原剧拆解与仿写 Brief 的关键字段。
- 每个关键维度都能看到变化类型和分析依据。
- 系统能明确指出仿写剧已经形成的创新和仍需重构的部分。
- 原始资料不足时不生成臆测结论。
- AI 服务异常时，字段级对比仍可用。
- 不影响现有仿写生成、锁定和项目流程。