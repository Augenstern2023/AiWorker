# SemIf Sidecar Contract

AIWorker only depends on the versioned HTTP contract implemented by
`screenplay_review.semif_adapter.SemIfAdapter`. Model code and weights do not
belong in this repository.

## Local contract smoke test

Start the deterministic fake service:

```powershell
uv run uvicorn deploy.semif.fake_server:app --host 127.0.0.1 --port 18766
```

Verify it:

```powershell
curl.exe http://127.0.0.1:18766/health
```

Then set `SCREENPLAY_REVIEW_ENGINE=semif` only for an explicit internal test.
The fake service validates integration and recovery; it does not measure review
quality.

## Required real-service endpoints

- `GET /health` returns `status`, `model_name`, and immutable `model_revision`.
- `POST /v1/reviews` accepts `semif-review-request-v1` and returns
  `semif-review-response-v1`.
- Repeating a `client_request_id` must be safe and should return the same result.
- Logs must not retain content windows, prompts, credentials, or screenplay text.
- Unknown rule, option, scope, or schema versions must return a 4xx response.

## Container environment

`docker-compose.yml` is an environment contract, not a bundled implementation.
Set `SEMIF_IMAGE` to the approved image and `SEMIF_MODEL_DIR` to a local model
directory before starting it. No default image or weight name is intentionally
provided because the real SemIf distribution and licence have not yet been
confirmed.

```powershell
$env:SEMIF_IMAGE = "approved-registry/semif:approved-revision"
$env:SEMIF_MODEL_DIR = "D:\models\approved-semif-model"
docker compose -f deploy/semif/docker-compose.yml up -d
```

An RTX 4060 8 GB result only proves that the tested quantized profile starts and
serves the benchmark. It does not establish BF16 production capacity.
