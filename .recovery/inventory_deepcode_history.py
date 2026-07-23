import json
import subprocess
from collections import Counter
from pathlib import Path

HISTORY = Path(r"C:\Users\Administrator\.deepcode\projects\D-Crawler-AiWorker\file-history")
CUTOFF = "698e21f41e6f4153fde54340ce73dcdc392cd548"

def git(*args: str, text: bool = True):
    return subprocess.run(
        ["git", *args], cwd=HISTORY, check=True,
        capture_output=True, text=text, encoding="utf-8" if text else None,
    ).stdout

commits = git("rev-list", "--reverse", CUTOFF).splitlines()
latest: dict[str, dict] = {}
versions = Counter()
for commit in commits:
    try:
        raw = git("show", f"{commit}:.deepcode-file-history.json")
        payload = json.loads(raw)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        continue
    timestamp = git("show", "-s", "--format=%aI", commit).strip()
    for entry in payload.get("files", {}).values():
        path = entry.get("path")
        blob = entry.get("blob")
        if not path or not blob:
            continue
        versions[path] += 1
        latest[path] = {"path": path, "blob": blob, "commit": commit, "timestamp": timestamp}

print(json.dumps({
    "cutoff": CUTOFF,
    "commit_count": len(commits),
    "recoverable_file_count": len(latest),
    "total_versions_seen": sum(versions.values()),
    "files": sorted(latest.values(), key=lambda item: item["path"].lower()),
}, ensure_ascii=False, indent=2))
