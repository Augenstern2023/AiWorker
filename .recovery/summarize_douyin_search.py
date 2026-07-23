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
            keyword="龙头短剧",
            search_channel=SearchChannelType.VIDEO,
            sort_type=SearchSortType.GENERAL,
            publish_time=PublishTimeType.UNLIMITED,
        )
        for index, item in enumerate(response.get("data") or []):
            info = item.get("aweme_info") or {}
            print(json.dumps({
                "index": index,
                "aweme_id": info.get("aweme_id"),
                "desc": info.get("desc"),
                "author": (info.get("author") or {}).get("nickname"),
                "sec_uid": (info.get("author") or {}).get("sec_uid"),
                "aweme_list_count": len(item.get("aweme_list") or []),
                "series_basic_info": info.get("series_basic_info"),
                "series_info": info.get("series_info"),
                "mix_info": info.get("mix_info"),
                "item_keys_with_series": [key for key in item if any(word in key.lower() for word in ("mix", "series", "collection"))],
                "info_keys_with_series": [key for key in info if any(word in key.lower() for word in ("mix", "series", "collection"))],
            }, ensure_ascii=False))
    finally:
        await client._close(crawler, playwright)

asyncio.run(main())
