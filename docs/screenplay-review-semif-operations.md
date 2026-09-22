# SemIf 剧本评审运维说明

## 开关与回滚

- 默认保持 `SCREENPLAY_REVIEW_ENGINE=legacy`。
- 内部灰度通过创建请求显式传 `engine=semif`，该值会冻结到审核来源快照。
- 显式 SemIf 请求入队前执行最长 5 秒的 `/health` 预检；不可用时 API 返回 503，避免生成必然失败的新任务。
- SemIf 不可用、输出非法或超时后，当前任务进入 `failed`；恢复服务后使用原“继续审核”入口从检查点续跑。
- 回滚只需停止创建新的 SemIf 审核并恢复 `legacy` 默认值；既有 SemIf 报告不得删除或覆盖。

## 配置

```text
SCREENPLAY_REVIEW_ENGINE=legacy|semif
SEMIF_BASE_URL=http://127.0.0.1:18766
SEMIF_TIMEOUT_SECONDS=120
SEMIF_MAX_RETRIES=2
SEMIF_MODEL_PROFILE=local-default
```

Worker 使用创建任务时冻结的 engine、模型 profile、规则包和版本，不会在恢复时重新解释环境变量。
范围复审使用父审核行事务锁和 `request_key` 保证并发幂等；同一幂等键不能换 scope 或 source fingerprint。

## 验证顺序

1. 启动 `deploy.semif.fake_server`，验证健康检查、请求契约、失败恢复和报告导出。
2. 运行 fake benchmark，确认确定性与指标文件格式。
3. 接入获批的真实 SemIf 镜像和不可变模型 revision。
4. 使用受控脱敏数据集运行至少 100 个双人标注案例。
5. 误放行率低于 5% 且其他质量指标经负责人批准后，才允许灰度结果直接产生 `passed`。

## Benchmark

```powershell
uv run python scripts/run_screenplay_review_benchmark.py --fixtures tests/fixtures/screenplay_review --engine fake --output .runtime/screenplay-review-benchmark.json
```

仓库内合成 fixture 永远不满足发布样本量，只用于合约冒烟。真实剧本、个人信息、密钥和完整模型 prompt 不得写入仓库、stdout 或 Job 事件。
