# 榜单集中式 PostgreSQL 迁移运行手册

本手册用于把抖音、红果榜单从 `192.168.1.34` 的本地文件回填到 PostgreSQL，并验证 `192.168.1.34` 与 `192.168.1.100` 使用同一份集中数据。所有命令均不得携带或打印数据库密码；密码通过交互提示或受控环境变量提供。

## 1. 前置检查

在两台机器的项目目录分别执行：

```powershell
git status --short
git rev-parse HEAD
uv sync
uv run alembic current
uv run alembic heads
```

预期证据：

- 两台机器部署的是同一个已审核提交，且没有被部署流程覆盖的本地改动。
- `AIWORKER_SYSTEM_DATABASE_URL` 使用 `postgresql+psycopg://`，但终端和日志不输出它的值。
- `uv run alembic heads` 只显示 `0016_rank_storage (head)`。
- 升级前记录 `uv run alembic current` 的实际 revision；若为 `0012_writer_stage_edit`，必须正常连续升级 `0013`、`0014`、`0015`、`0016`，不得使用 `stamp` 跳过 migration。

## 2. 备份生产数据库

在 `192.168.1.34` 上创建备份目录并执行自定义格式备份：

```powershell
New-Item -ItemType Directory -Force E:\postgresql\backups | Out-Null
pg_dump -h 127.0.0.1 -U aiworker_app -d aiworker -Fc -f E:\postgresql\backups\aiworker-before-rank-migration-20260902.dump
Get-Item E:\postgresql\backups\aiworker-before-rank-migration-20260902.dump | Select-Object FullName, Length, LastWriteTime
pg_restore -l E:\postgresql\backups\aiworker-before-rank-migration-20260902.dump | Select-Object -First 20
```

预期证据：`pg_dump` 退出码为 0，备份文件存在且 `Length` 大于 0，`pg_restore -l` 能读取目录。命令在数据库主机本机执行，因此使用 `127.0.0.1`；当前 `pg_hba.conf` 没有放行服务器通过自身局域网地址 `192.168.1.34` 回连。记录文件路径、大小和时间；不要记录密码。

## 3. 升级 schema

在 `192.168.1.34` 的已部署项目目录执行：

```powershell
uv run alembic upgrade head
uv run alembic current
uv run alembic heads
```

预期证据：升级退出码为 0，`current` 与 `heads` 均为 `0016_rank_storage (head)`。

也可在数据库中只读核对：

```sql
SELECT version_num FROM alembic_version;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('rank_snapshots', 'rank_entries')
ORDER BY table_name;
```

预期：revision 为 `0016_rank_storage`，两张表均存在。

## 4. 回填历史榜单

在 `192.168.1.34` 执行：

```powershell
uv run python scripts/migrate_rank_files_to_postgres.py --project-root D:\Crawler\AiWorker --hongguo-exports-root E:\hg\exports --platform all
```

预期：命令输出一份不含数据库连接串的 JSON 报告，`failed=0`；`discovered` 等于当时实际发现的抖音 JSON 与红果 CSV 总数。

立即原样执行第二次：

```powershell
uv run python scripts/migrate_rank_files_to_postgres.py --project-root D:\Crawler\AiWorker --hongguo-exports-root E:\hg\exports --platform all
```

预期：已导入来源全部为 `skipped`，`failed=0`，数据库快照数和条目数不增加。这是幂等校验，不能省略。

## 5. 数据库只读验收

```sql
SELECT
    s.platform,
    COUNT(DISTINCT s.id) AS snapshot_count,
    COUNT(e.id) AS entry_count,
    MIN(s.snapshot_date) AS earliest_date,
    MAX(s.snapshot_date) AS latest_date
FROM rank_snapshots AS s
LEFT JOIN rank_entries AS e ON e.snapshot_id = s.id
GROUP BY s.platform
ORDER BY s.platform;

SELECT
    s.id,
    s.platform,
    s.source_ref,
    s.record_count,
    COUNT(e.id) AS actual_entry_count
FROM rank_snapshots AS s
LEFT JOIN rank_entries AS e ON e.snapshot_id = s.id
GROUP BY s.id
HAVING s.record_count <> COUNT(e.id)
ORDER BY s.id;
```

预期：`dy`、`hg` 均有快照与条目，日期范围符合源文件；第二条查询返回 0 行。

## 6. 双机 API 一致性

确保两台 Web 服务都已使用同一实现提交重启。当前服务端口为 `8899`，且榜单 API 受登录会话保护。以下命令通过交互式凭据分别创建会话，不把密码写入脚本或命令历史；随后检查最新日期，并按数据库统计选取一个真实历史日期：

```powershell
$rankHosts = @('192.168.1.34', '192.168.1.100')
$platforms = @('dy', 'hg')
$rankPort = 8899
$rankSessions = @{}
foreach ($rankHost in $rankHosts) {
    $credential = Get-Credential -Message "登录 ${rankHost} 的 AiWorker"
    $session = [Microsoft.PowerShell.Commands.WebRequestSession]::new()
    $loginBody = @{
        username = $credential.UserName
        password = $credential.GetNetworkCredential().Password
    } | ConvertTo-Json
    $null = Invoke-RestMethod -Method Post -ContentType 'application/json' `
        -Uri "http://${rankHost}:${rankPort}/api/auth/login" `
        -Body $loginBody -WebSession $session
    $rankSessions[$rankHost] = $session
    foreach ($platform in $platforms) {
        Invoke-RestMethod `
            -Uri "http://${rankHost}:${rankPort}/api/rank/today?platform=${platform}&limit=10&refresh=true" `
            -WebSession $session
    }
}

$historyDate = '2026-08-31'
Invoke-RestMethod `
    -Uri "http://192.168.1.34:${rankPort}/api/rank/today?platform=dy&date=${historyDate}&limit=10&refresh=true" `
    -WebSession $rankSessions['192.168.1.34']
Invoke-RestMethod `
    -Uri "http://192.168.1.100:${rankPort}/api/rank/today?platform=dy&date=${historyDate}&limit=10&refresh=true" `
    -WebSession $rankSessions['192.168.1.100']
```

计算忽略 `cache_status` 后的响应摘要：

```powershell
function Get-RankDigest(
    [string]$Uri,
    [Microsoft.PowerShell.Commands.WebRequestSession]$WebSession
) {
    $payload = Invoke-RestMethod -Uri $Uri -WebSession $WebSession
    $payload.PSObject.Properties.Remove('cache_status')
    $json = $payload | ConvertTo-Json -Depth 100 -Compress
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($bytes)).ToLowerInvariant()
}

Get-RankDigest "http://192.168.1.34:${rankPort}/api/rank/today?platform=dy&limit=10&refresh=true" $rankSessions['192.168.1.34']
Get-RankDigest "http://192.168.1.100:${rankPort}/api/rank/today?platform=dy&limit=10&refresh=true" $rankSessions['192.168.1.100']
Get-RankDigest "http://192.168.1.34:${rankPort}/api/rank/today?platform=hg&limit=10&refresh=true" $rankSessions['192.168.1.34']
Get-RankDigest "http://192.168.1.100:${rankPort}/api/rank/today?platform=hg&limit=10&refresh=true" $rankSessions['192.168.1.100']
```

预期：相同平台、相同日期与 limit 的两台机器摘要一致；接口不依赖 `192.168.1.100` 的本地榜单目录。

## 7. 新榜单和周报验收

启动一份新的抖音或红果榜单任务。worker 的成功事件应包含集中同步的 `status`、`source_ref`、`record_count`；原始 JSON/CSV 与分析文件仍保留在 `192.168.1.34`。

```sql
SELECT platform, source_ref, source_checksum, record_count, imported_at
FROM rank_snapshots
ORDER BY imported_at DESC
LIMIT 10;
```

预期：数据库新增快照，或同一 `source_ref` 内容变化时原快照被完整更新；随后 `192.168.1.100` 使用 `refresh=true` 能看到新日期。若集中同步失败，worker 应返回失败并发出 `central_sync_failed`，且不得删除源文件或输出数据库连接串。

最后执行一次周报任务。预期：周报成功生成，来源 revision 取自 PostgreSQL；即使 `192.168.1.100` 没有本地榜单文件，也能读取集中榜单。

## 8. 回滚

出现问题时先停止两台机器上的榜单自动任务和 Web/周报 worker，避免继续写入。保留所有源文件与备份，并记录当前 revision 和失败证据。

应用回滚：两台机器切回迁移前的已知可用提交并重启，确保两端版本一致。旧代码不会读取新榜单表，但新表可暂时保留以便调查。

schema 回滚只在确认必须撤销且已验证备份后执行：

```powershell
uv run alembic downgrade 0015_llm_settings
uv run alembic current
```

该操作会删除 `rank_entries` 与 `rank_snapshots`，其中的集中榜单数据将丢失；执行前必须再次确认自定义格式备份可读。需要恢复整个数据库时，应由数据库管理员在独立恢复演练后使用 `pg_restore`，不要直接覆盖当前生产库。

## 9. 交付记录

交付说明至少记录：实现提交、两台机器部署提交、备份路径与大小、升级前后 Alembic revision、首次回填报告摘要、第二次幂等回填摘要、两平台快照/条目/日期统计、双机 API 摘要、新榜单自动同步结果和周报结果。不得记录密码或包含密码的 URL。
