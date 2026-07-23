import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.douyin_drama_fallback import DouyinDramaFallbackClient

INTEREST = ("mix", "series", "collection", "episode", "sec_uid", "aweme_list")

def walk(value, path="root", depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if any(word in key.lower() for word in INTEREST):
                if isinstance(child, (dict, list)):
                    summary = f"{type(child).__name__} len={len(child)}"
                else:
                    summary = repr(child)
                print(child_path, summary)
            walk(child, child_path, depth + 1)
    elif isinstance(value, list):
        for index, child in enumerate(value[:5]):
            walk(child, f"{path}[{index}]", depth + 1)

async def main():
    client = DouyinDramaFallbackClient()
    crawler, playwright = await client._client()
    try:
        detail = await crawler.dy_client.get_video_by_id("7663512106062466331")
        print("DETAIL_KEYS", sorted(detail.keys()))
        walk(detail)
        Path(".recovery/longtou_detail.json").write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
    finally:
        await client._close(crawler, playwright)

asyncio.run(main())
