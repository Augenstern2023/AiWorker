# AIWorker SemIf sidecar

这个目录把 SemIf 固定在独立环境中。它不会把 Torch、Transformers 或
llama.cpp 安装进 AIWorker 主环境，也不会替代现有剧本审核 API。

固定版本见 `upstream-manifest.json`。当前只开放两个内部接口：

- `GET /health`
- `POST /v1/score`

`/v1/score` 接受一个 `state` 和一组 `criteria`，服务端将它们转换成 SemIf
原生的 `id/state/question/options` 行。`shared` 模式要求全部规则复用完全相同
的 state。返回值保留上游概率、logits、Prompt 哈希、模型元数据和计时；服务
不会生成理由、引文或修改建议。

## 安装

先从上游 README 指定的模型仓库取得
`Qwen_Qwen3.5-4B-Q4_K_M.gguf`，然后计算文件 SHA-256。模型约 3 GB，当前
上游 llama.cpp 实现固定 `n_gpu_layers=0`，只使用 CPU。

固定文件 revision 和预期 SHA-256 已记录在 `upstream-manifest.json`。当前预期
哈希为 `13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983`；
启动时仍会重新计算本地文件并拒绝不一致的模型。

以下示例把虚拟环境、uv 缓存、Hugging Face 缓存和模型全部放在 D 盘；
仓库本身可以继续保留在 C 盘。先在仓库根目录执行安装：

```powershell
$env:UV_PROJECT_ENVIRONMENT = "D:\AiWorker\SemIf\.venv"
$env:UV_CACHE_DIR = "D:\AiWorker\uv-cache"
$env:HF_HOME = "D:\AiWorker\huggingface-cache"
$env:HF_HUB_CACHE = "D:\AiWorker\huggingface-cache\hub"

uv sync --frozen --project deploy/semif
```

把模型放到 `D:\AiWorker\SemIf\models` 后，在同一个 PowerShell 窗口启动服务：

```powershell
$env:UV_PROJECT_ENVIRONMENT = "D:\AiWorker\SemIf\.venv"
$env:UV_CACHE_DIR = "D:\AiWorker\uv-cache"
$env:HF_HOME = "D:\AiWorker\huggingface-cache"
$env:HF_HUB_CACHE = "D:\AiWorker\huggingface-cache\hub"
$env:SEMIF_GGUF_PATH = "D:\AiWorker\SemIf\models\Qwen_Qwen3.5-4B-Q4_K_M.gguf"
$env:SEMIF_GGUF_SHA256 = "13c16f426047e2de38cd075bdade4a7bcbc8c774384876f677740cda65f8a983"
$env:SEMIF_LLAMA_THREADS = "6"

uv run --frozen --project deploy/semif aiworker-semif
```

服务就绪后，`Invoke-RestMethod http://127.0.0.1:8765/health` 应返回
`status: ok`、`backend: llamacpp` 和 `n_gpu_layers: 0`。当前上游实现即使机器有
NVIDIA 显卡也只使用 CPU；环境、缓存和模型均在 D 盘不代表模型会使用 GPU。

模型首次启动还需要下载固定 revision 的参考 tokenizer。生产环境应预先缓存，
并把实际 GGUF SHA-256 纳入部署记录。

## 接入 AIWorker

AIWorker 默认仍使用 Legacy 审核。仅在试点环境设置：

```powershell
$env:SCREENPLAY_REVIEW_ENGINE = "semif"
$env:SCREENPLAY_REVIEW_SEMIF_URL = "http://127.0.0.1:8765"
$env:SCREENPLAY_REVIEW_SEMIF_FALLBACK = "legacy"
```

SemIf 结果始终标记为 `manual_review`。选项概率是给定选项之间的条件分数，
没有完成剧本领域校准前不得显示为业务“置信度”或用于自动通过。

服务启动后可用真实 UTF-8 剧本文本测量当前硬件：

```powershell
uv run python scripts/benchmark_semif_screenplay.py `
  --text-file "project-1-script (1).txt" `
  --repeat 3 `
  --output data/semif-benchmark.json
```
