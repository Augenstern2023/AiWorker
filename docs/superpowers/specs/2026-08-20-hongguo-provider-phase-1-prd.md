# 红果独立 Provider 改造一期 PRD

> 文档状态：待评审  
> 日期：2026-08-20  
> 适用项目：AiWorker 选品工作台、剧目检索下载、单集分析  
> 一期定位：建立独立 Provider 基础，恢复七个第三方来源的剧目检索、人工选源及所选来源下载

## 1. 背景

红果下载链路目前以本地抓包目录和外部 8776 视频抓包服务为主。此前已有的配置中心、激活码、七个第三方剧目来源、来源选择和播放接口下载逻辑已从当前主流程中移除，导致以下问题：

- 用户只能检索已经存在于抓包目录中的剧目，无法通过第三方接口主动检索剧目。
- 搜索、剧集、播放地址和下载能力没有稳定的来源边界。
- 后续增加“第三方失败后重新选源、再转抓包、最后人工确认抖音”时，容易在 Worker 中形成大量来源分支。
- 不同来源的 `book_id`、`video_id` 和红果原生 `series_id` 存在误用风险。

最终目标采用“独立 Provider + 下载编排器”。一期先交付可独立验收的第三方来源纵向链路，为后续编排器接入提供稳定契约。

## 2. 一期目标

一期必须实现：

1. 建立统一的剧目 Provider 数据模型、接口和注册机制。
2. 恢复配置中心地址、激活码及七个第三方来源配置解析。
3. 并发检索全部已启用第三方来源，并保留原标准化剧名精确匹配逻辑。
4. 按来源分组展示候选，要求用户人工选择“来源 + 具体剧目”。
5. 使用选定来源读取剧集、解析播放地址并下载选中集数。
6. 保留原第三方下载的付费识别、地址刷新、断点续传、大小限制和失败诊断逻辑。
7. 保持现有抓包目录、8776 服务、CENC 解密和抖音手动入口可用，不因一期改造发生回归。
8. 保持现有 API 请求结构和 Job 类型兼容。

## 3. 一期非目标

以下内容属于后续阶段，不在一期实现：

- 第三方下载失败后，在同一个 Job 中暂停并等待重新选择其他 Provider。
- 新 Provider 接管失败集及全部剩余集的断点编排。
- 第三方失败后自动进入抓包阶段。
- 抓包 `series_id` 的交互式发现与跨来源身份映射。
- 抓包失败后在同一个 Job 中人工确认抖音候选。
- 跨 Provider 的统一 `orchestration.json` 状态机。

一期中，选定第三方来源下载失败时，Job 按现有任务机制失败并保留清单与 `.part`；用户可以重新发起任务并选择其他来源。现有抓包下载仍作为独立入口保留。该行为是一期过渡状态，不代表最终产品规则。

## 4. 用户角色与使用场景

### 4.1 选品人员

- 输入剧名检索可用第三方来源。
- 查看每个来源的候选、集数、简介和健康状态。
- 明确选择一个来源和对应剧目。
- 选择单集、多集或全集下载。
- 查看每集下载、付费跳过和失败状态。

### 4.2 内容分析人员

- 在剧目检索页选择来源、剧目和单集。
- 下载所选来源的视频。
- 沿用现有转写、剧本生成和工作流产物逻辑。

### 4.3 系统管理员

- 配置第三方配置中心地址和激活码。
- 检查配置中心及七个来源的可用状态。
- 继续配置抓包根目录、8776 地址和等待时长。

## 5. 核心用户流程

### 5.1 剧目检索与下载

```text
输入剧名
  ↓
并发检索七个第三方来源
  ↓
按来源展示精确匹配候选和局部错误
  ↓
用户选择来源 + 剧目
  ↓
通过该来源读取剧集列表
  ↓
用户选择集数
  ↓
通过同一来源解析播放地址并下载
  ↓
校验成品并生成 manifest/source 产物
```

一期禁止：

- 搜索结果只有一条时自动替用户选择来源。
- 某来源失败后静默切换到另一个第三方来源。
- 第三方失败后自动调用抓包或抖音。
- 使用其他来源的 `book_id` 或 `video_id` 下载。

### 5.2 单集分析

单集分析沿用同一 Provider 搜索、剧集和下载链路。视频校验成功后，继续执行现有音频提取、ASR、字幕和剧本生成流程。Provider 改造不得改变分析产物目录和工作流任务关联。

## 6. Provider 产品契约

### 6.1 Provider 清单

一期恢复以下来源：

| Provider ID | 来源键 | 说明 |
| --- | --- | --- |
| `hg` | `hg` | 原红果来源 |
| `hgmj` | `hgmj` | 原红果马甲来源 |
| `hgzh` | `hgzh` | 原红果综合来源 |
| `redguo` | `redguo` | 原 redguo 来源 |
| `hm` | `hm` | 原 hm 来源 |
| `huolong` | `huolong` | 原火龙来源 |
| `xingyadongli` | `xingyadongli` | 原星芽动力来源 |

Provider 显示名称由应用内固定映射提供，不直接信任配置中心返回的任意 HTML 或脚本文本。

### 6.2 统一能力

每个 Provider 必须实现：

```python
class DramaProvider(Protocol):
    provider_id: str
    display_name: str

    def health(self) -> ProviderHealth: ...
    def search(self, title: str, page: int = 1) -> list[DramaCandidate]: ...
    def search_exact(self, title: str) -> list[DramaCandidate]: ...
    def episodes(self, series_ref: str) -> list[DramaEpisode]: ...
    def resolve_playable(
        self,
        series_ref: str,
        episode_ref: str,
    ) -> PlayableMedia: ...
    def download_episode(
        self,
        media: PlayableMedia,
        target: Path,
        context: DownloadContext,
    ) -> DownloadResult: ...
```

一期可以由一个共享的第三方 HTTP Provider 类按来源配置实例化七次，但对上层必须表现为七个独立 Provider。

### 6.3 标识隔离

业务对象必须携带以下组合标识：

```text
provider_id + series_ref + episode_ref
```

- `series_ref` 对第三方来源通常对应该来源的 `book_id`。
- `episode_ref` 对第三方来源通常对应该来源的 `video_id`。
- `book_id` 继续作为 API 兼容字段，但不能脱离 `provider_id` 单独使用。
- 红果原生 `series_id`、第三方 `book_id` 和抖音 `aweme_id` 不得相互转换或复用。

## 7. 第三方配置恢复

### 7.1 设置项

设置页增加并恢复：

- 配置中心地址 `config_url`。
- 激活码 `activation_code`。
- “检查第三方接口”按钮。
- 七个来源的启用、缺失、正常或异常状态。

同时保留现有：

- `capture_root`。
- `capture_service_url`。
- `wait_timeout_seconds`。
- 抓包目录和 8776 健康检查。

### 7.2 设置迁移

- 读取旧文件中的 `config_url` 和 `activation_code`。
- 读取当前文件中的抓包设置。
- 读取设置时不自动改写磁盘文件。
- 管理员保存后统一写入同时包含第三方和抓包配置的新结构。
- 公共设置响应不返回激活码原文，只返回 `has_activation_code`。
- 日志、异常、审计记录和测试快照不得包含激活码。

### 7.3 配置中心解析

配置中心响应必须解析 `platforms` 字典或列表结构，并读取每个来源的：

```text
searchUrl
bookUrl
playUrl
playRequiresBookId
```

单个来源配置不完整时只将该来源标记为不可用；只要仍有一个来源可检索，搜索接口不得整体失败。

## 8. 剧目检索需求

### 8.1 搜索规则

- 查询必须去除首尾空白。
- 使用 NFKC、大小写折叠和中英文数字字符过滤后的标准化剧名进行精确匹配。
- 七个来源并发检索，禁止按来源串行等待。
- 每个来源一期至少检索第一页，并保留旧实现对来源分页的兼容能力。
- 同一来源按 `series_ref` 去重。
- 不同来源的同名剧目不得合并成一个候选。
- 单个来源错误必须作为该来源的局部错误返回。
- 所有来源均无精确匹配时返回空列表，不自动进入抖音。

### 8.2 搜索结果

搜索响应同时提供兼容扁平结果和来源分组：

```json
{
  "status": "success",
  "source": "hongguo_providers",
  "candidates": [],
  "providers": [
    {
      "provider_id": "hg",
      "display_name": "红果源",
      "status": "available",
      "elapsed_ms": 320,
      "candidates": [],
      "error": ""
    }
  ],
  "capture_candidates": []
}
```

每个第三方候选至少包含：

```text
provider_id
source_platform
series_ref
book_id
title
cover
summary
episode_count
```

接口响应不得包含来源原始响应对象、播放地址或激活码。

### 8.3 Suggest

`/api/hongguo/drama/suggest` 恢复第三方剧目建议：

- 查询已启用第三方 Provider。
- 对标准化标题去重。
- 最多返回 8 条。
- 每条建议保留 `provider_id`，便于用户进入完整检索后选择来源。
- 抓包目录标题建议可以继续返回，但必须标记 `provider_id=hongguo_capture`，不能伪装成第三方结果。

## 9. 剧集与下载需求

### 9.1 剧集读取

用户选定候选后，前端提交该候选的 `provider_id` 和 `series_ref`。后端只能调用该 Provider 的剧集接口。

每集至少返回：

```text
episode
episode_ref
video_id
title
is_paid
paid_reason
provider_id
```

明确付费集禁用选择并显示原因。未知付费状态不得推断为付费。

### 9.2 播放地址解析

- 使用选定 Provider 的 `playUrl`。
- 请求携带该 Provider 的 `episode_ref`。
- 仅当来源配置声明 `playRequiresBookId` 时附带该来源的 `series_ref`。
- 播放接口明确返回付费时跳过该集。
- 返回地址必须是 HTTP 或 HTTPS。
- 播放地址不得进入公开 API、Job 日志、`source.json` 或 `manifest.json`。

### 9.3 下载行为

保留原第三方下载逻辑：

- `.part` 断点续传。
- 校验 `206` 和 `Content-Range`。
- 来源忽略 Range 时从干净的 `.part` 重新下载。
- 单文件上限 2 GiB。
- 支持暂停和恢复。
- 地址失效或首次传输失败时重新请求一次播放地址。
- 刷新后仍失败则保留 `.part` 并将该集标记失败。
- 成功文件通过临时文件原子替换。
- 至少校验合理大小和 MP4 `ftyp`；检测到 `ffprobe` 时执行流解析。

批量任务继续处理其他集，但任一非付费集最终失败时，Job 最终标记失败并保留完整清单。付费跳过不算失败。

## 10. API 兼容要求

继续保留：

```text
GET/PUT /api/hongguo/drama/settings
POST    /api/hongguo/drama/test
POST    /api/hongguo/drama/search
POST    /api/hongguo/drama/download/search
POST    /api/hongguo/drama/suggest
POST    /api/hongguo/drama/episodes
POST    /api/hongguo/drama/jobs
POST    /api/hongguo/drama/download/jobs
```

旧请求继续接受：

```json
{
  "book_id": "来源内剧目标识",
  "source_platform": "hg"
}
```

新前端优先发送：

```json
{
  "series_ref": "来源内剧目标识",
  "provider_id": "hg"
}
```

后端标准化规则：

- `provider_id` 缺失时使用 `source_platform`。
- `series_ref` 缺失时使用 `book_id`。
- 两组字段同时存在但值冲突时返回 `400`。
- 未注册、未启用或配置不完整的 Provider 返回结构化错误。

## 11. UI 需求

### 11.1 设置页

- 页面标题调整为“红果剧目来源与抓包设置”。
- 第三方设置和抓包设置分为两个卡片区域。
- 激活码使用密码输入框，加载时不回填原文。
- 第三方检查结果逐来源显示。
- 抓包检查结果继续显示目录统计和 8776 状态。

### 11.2 搜索结果

- 按 Provider 分组展示。
- 显示来源名、候选数量、耗时和局部错误。
- 每个候选显示剧名、封面、简介和集数。
- 即使只有一个第三方候选，也必须点击确认。
- 选择候选后明显显示当前来源。
- 来源失败时允许用户返回搜索结果选择其他候选，但一期不在同一 Job 中续接。

### 11.3 任务状态

- 显示当前 Provider 名称。
- 每集显示下载、付费跳过、失败或成功。
- 第三方下载失败提示不得表述为抓包失败。
- 抓包任务继续显示手机端播放和剩余等待时间。
- 沿用 Bootstrap、Alpine 和现有响应式布局，不增加前端依赖。
- 补齐加载、空结果、局部错误、重试、禁用、键盘焦点、`aria-live` 和移动端折行。

## 12. 错误与安全

一期统一错误代码：

| 错误代码 | 含义 | 产品行为 |
| --- | --- | --- |
| `configuration_error` | 配置中心或来源配置错误 | 来源局部不可用或设置检查失败 |
| `provider_unavailable` | 来源超时、网络或服务异常 | 显示局部错误；下载 Job 记录失败 |
| `series_not_found` | 来源没有精确匹配剧目 | 该来源返回空候选 |
| `episode_not_found` | 来源缺少目标集 | 禁止创建无效集数任务 |
| `paid_content` | 明确付费内容 | 跳过，不算失败 |
| `play_url_expired` | 地址刷新后仍不可用 | 保留清单和 `.part`，该集失败 |
| `transfer_failed` | 传输重试耗尽 | 保留清单和 `.part`，该集失败 |
| `invalid_media` | 成品校验失败 | 不发布伪成品 |

安全要求：

- URL、激活码、Cookie、请求头和设备标识不得写入公开响应或产物清单。
- `source.json` 只记录剧名、集数、Provider、来源内脱敏标识、编码、状态和来源显示名。
- 后端日志仅记录 Provider ID、状态码、错误代码和安全错误信息。
- 配置中心返回的 URL 只允许 HTTP/HTTPS，禁止本地文件协议和任意命令执行。

## 13. 产物要求

继续使用现有 Job 目录：

```text
data/hg/jobs/{job_id}/dramas/{safe_title}/
```

每集记录：

```json
{
  "episode": 1,
  "status": "succeeded",
  "provider_id": "hg",
  "series_ref": "来源内剧目标识",
  "episode_ref": "来源内剧集标识",
  "bytes": 12389122,
  "path": "episode_01/video.mp4"
}
```

`manifest.json` 汇总成功、付费跳过和失败数量。单集分析继续生成现有 `transcript.json`、`script.md` 等产物。

## 14. 验收标准

### 14.1 功能验收

- [ ] 配置中心可解析七个来源，单个来源配置缺失不影响其他来源。
- [ ] 使用真实或受控测试接口输入剧名，可看到按来源分组的精确匹配结果。
- [ ] 多个来源有结果时，系统不自动选择，用户可选择任一来源。
- [ ] 选择来源后，只调用该来源的剧集和播放接口。
- [ ] 所选来源可完成单集、多集和全集下载。
- [ ] 地址首次失败后会按原逻辑刷新并重试。
- [ ] 付费剧集被明确标记和跳过。
- [ ] 下载结果通过 MP4 校验，失败时不生成伪成品。
- [ ] 单集分析可继续完成 ASR 和剧本产物。
- [ ] 当前抓包下载和 8776 服务调用不回归。
- [ ] 抖音仍可通过现有平台选择或独立入口手动使用，不会被自动触发。

### 14.2 兼容验收

- [ ] 旧 `book_id + source_platform` 请求仍可创建任务。
- [ ] 新 `series_ref + provider_id` 请求可创建任务。
- [ ] 现有 Job 权限、项目关联、进度、暂停恢复和文件下载保持有效。
- [ ] 旧设置文件可读取，保存后同时保留第三方与抓包设置。

### 14.3 安全验收

- [ ] API、日志、Job 事件和产物中不出现激活码原文。
- [ ] `manifest.json` 和 `source.json` 中不出现播放 URL、Cookie 或请求头。
- [ ] 不同 Provider 的剧目和剧集标识不会交叉使用。

## 15. 测试范围

新增或调整测试：

```text
tests/test_drama_providers.py
tests/test_hongguo_drama.py
tests/test_hongguo_capture.py
tests/test_hongguo_video_capture_client.py
tests/test_webgui_ui_changes.py
```

重点覆盖：

- 配置中心字典和列表结构。
- 七源注册和局部配置缺失。
- 激活码参数及脱敏。
- 并发搜索、精确匹配和去重。
- 同名多来源候选不合并。
- 剧集、播放和下载严格路由到选定 Provider。
- `playRequiresBookId`。
- 地址刷新、续传范围、大小限制和媒体校验。
- 付费跳过和批量部分失败。
- 旧 API payload 兼容。
- 抓包与抖音现有入口回归。

验证命令：

```powershell
uv run pytest tests/test_drama_providers.py tests/test_hongguo_drama.py -q
uv run pytest tests/test_hongguo_capture.py tests/test_hongguo_video_capture_client.py -q
uv run pytest tests/test_webgui_ui_changes.py -q
uv run pytest tests/ -q
uv run pre-commit run --all-files
```

## 16. 发布与回滚

- 一期上线前保留当前抓包设置和代码路径，不删除抓包 Provider 所需文件。
- 第三方 Provider 搜索结果为空或局部异常时，不修改抓包索引。
- 新搜索响应保留旧 `candidates` 字段，便于前端分批升级。
- 若一期第三方链路需要回滚，可关闭第三方 Provider 注册并恢复抓包检索入口；不得回滚或删除已生成的用户视频和清单。

## 17. 后续阶段依赖

一期完成后，二期在不修改 Provider 内部实现的前提下增加：

- 持久化下载编排器。
- 第三方失败后的人工重新选源。
- 新来源接管失败集及剩余集。
- 抓包身份映射和 8776 自动调用。
- 抓包失败后的抖音人工确认。
- 同一 Job 的全来源尝试轨迹。

一期验收通过的 Provider 契约、标识隔离和错误模型是二期开始的前置条件。
