import json
import subprocess
from pathlib import Path

ROOT = Path(r"D:\Crawler\AiWorker")
HISTORY = Path(r"C:\Users\Administrator\.deepcode\projects\D-Crawler-AiWorker\file-history")
INVENTORY = ROOT / ".recovery" / "deepcode_pre_revert_inventory.json"
DEST = ROOT / ".recovery" / "deepcode-pre-revert-20260723-165348"

payload = json.loads(INVENTORY.read_text(encoding="utf-8-sig"))
written = []
for item in payload["files"]:
    source_path = Path(item["path"])
    relative = source_path.relative_to(ROOT)
    target = DEST / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    content = subprocess.run(
        ["git", "cat-file", "blob", item["blob"]],
        cwd=HISTORY,
        check=True,
        capture_output=True,
    ).stdout
    target.write_bytes(content)
    written.append({"path": str(relative), "blob": item["blob"], "bytes": len(content)})

manifest = {
    "source_checkpoint": payload["cutoff"],
    "source_time": "2026-07-23T16:53:48+08:00",
    "destination": str(DEST),
    "file_count": len(written),
    "files": written,
}
(DEST / "RECOVERY_MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(json.dumps(manifest, ensure_ascii=False, indent=2))
