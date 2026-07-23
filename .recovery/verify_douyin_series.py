import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.douyin_drama_fallback import DouyinDramaFallbackClient

async def main():
    result = await DouyinDramaFallbackClient().resolve_series("龙头", "7649802799609285903")
    print(json.dumps({
        "count": len(result["episodes"]),
        "ids": [item["aweme_id"] for item in result["episodes"]],
        "labels": [item["episode_label"] for item in result["episodes"]],
        "sources": [item["source_kind"] for item in result["episodes"]],
        "range_label": result["range_label"],
        "missing": result["missing_episodes"],
        "collection_name": result["collection_name"],
        "warnings": result["warnings"],
    }, ensure_ascii=False, indent=2))

asyncio.run(main())
