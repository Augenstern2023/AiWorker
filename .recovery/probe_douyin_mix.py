import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.douyin_drama_fallback import DouyinDramaFallbackClient

async def main():
    client = DouyinDramaFallbackClient()
    crawler, playwright = await client._client()
    try:
        for uri in ("/aweme/v1/web/mix/aweme/", "/aweme/v1/web/mix/aweme/list/"):
            try:
                result = await crawler.dy_client.get(uri, {"mix_id": "7649799164821243944", "cursor": "0", "count": "20"})
                print(uri, result.keys(), len(result.get("aweme_list") or []), result.get("status_code"), result.get("status_msg"))
                if result.get("aweme_list"):
                    print([(x.get("aweme_id"), x.get("desc"), x.get("mix_info", {}).get("statis")) for x in result["aweme_list"][:5]])
            except Exception as exc:
                print(uri, type(exc).__name__, str(exc))
    finally:
        await client._close(crawler, playwright)

asyncio.run(main())
