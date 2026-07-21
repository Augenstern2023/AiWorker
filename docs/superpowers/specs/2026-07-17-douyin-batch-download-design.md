# Douyin Batch Download Design

## Goal

Improve the Hongguo-to-Douyin fallback download flow so the first Douyin result is selected by default, additional results can be selected for batch download, mix results are expanded, and explicitly paid videos are explained and skipped.

## Behavior

- Preserve Douyin provider order and mark the first result as primary.
- Select the first non-paid result by default while retaining checkboxes for other results.
- Expand every `aweme_mix_info.mix_items` entry and deduplicate by `aweme_id`.
- Treat each selected Douyin video as one downloadable item; labels parsed from titles are descriptive only.
- Accept ordered `aweme_ids` while retaining legacy `aweme_id` compatibility.
- Process selected items serially, refresh each video URL immediately before download, and retry a failed URL once.
- Write each item under `douyin_fallback/item_{index}_{aweme_id}/` and write a batch `manifest.json`.
- Report downloaded, paid-skipped, and failed counts through progress events.
- Honor pause markers between network, media, and ASR stages; cancellation remains process-based.

## Paid Content Policy

Only explicit provider fields or explicit provider payment errors classify content as paid. Title text such as "VIP" is not sufficient. Known paid results are shown but disabled; paid status discovered during execution is recorded as `skipped_paid` and processing continues. If every item is paid, the job completes with a warning rather than failing.

The same runtime paid-content check applies to Hongguo episode download and analysis workers.

## Compatibility

Existing single-video requests remain valid. Existing Hongguo book/episode downloads keep their API contract. The topic-library modal changes only its Douyin fallback branch.

