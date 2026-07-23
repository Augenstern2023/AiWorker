import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents.douyin_drama_fallback import DouyinDramaFallbackClient
from media_platform.douyin.field import PublishTimeType, SearchChannelType, SearchSortType

async def main():
    client = DouyinDramaFallbackClient()
    crawler, playwright = await client._client()
    try:
        response = await crawler.dy_client.search_info_by_keyword(
            keyword="龙头",
            search_channel=SearchChannelType.VIDEO,
            sort_type=SearchSortType.GENERAL,
            publish_time=PublishTimeType.UNLIMITED,
        )
        for index, item in enumerate((response.get("data") or [])[:3]):
            print("ITEM", index, "keys", sorted(item.keys()))
            selected = {key: item.get(key) for key in ("aweme_info", "aweme_mix_info") if key in item}
            print(json.dumps(selected, ensure_ascii=False)[:16000])
    finally:
        await client._close(crawler, playwright)

asyncio.run(main())
