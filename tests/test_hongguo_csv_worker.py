# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_hongguo_csv_worker.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import json

from agents.hongguo_csv_worker import run_job
from core.job import CrawlJob


def test_hongguo_csv_worker_imports_latest_two_snapshots(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    write_csv(exports / "20260707/top10_rankings.csv", [["红果短剧热播榜", "1", "昨日剧"]])
    write_csv(exports / "20260708/top10_rankings.csv", [["红果短剧热播榜", "1", "今日剧"]])
    monkeypatch.chdir(tmp_path)

    job = CrawlJob(job_id="job-hg", platform="hg", crawler_type="rank")

    assert run_job(job, exports_root=exports) == 0

    rank_dir = tmp_path / "data/hg/jobs/job-hg/rank"
    files = sorted(rank_dir.glob("hongguo_rank_*.json"))
    assert [path.name for path in files] == ["hongguo_rank_20260707_000000.json", "hongguo_rank_20260708_000000.json"]
    payload = json.loads(files[-1].read_text(encoding="utf-8"))
    assert payload["source"] == "csv_import"
    assert payload["records"][0]["剧名"] == "今日剧"


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "榜单类型,排名,剧名,标签,简介,评分,分享,点赞,收藏,热度,推荐指数,评分人数,期待指数,热搜指数,最高热度,热评"
    lines = [header]
    for row in rows:
        full = row + [""] * (16 - len(row))
        lines.append(",".join(full))
    path.write_text("\n".join(lines), encoding="utf-8")
