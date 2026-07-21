# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
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

from analysis.exporters.json_exporter import export_json
from analysis.schema import BilibiliAnalysisResult


def test_export_json_writes_stable_fields(tmp_path):
    result = BilibiliAnalysisResult(
        aid=123,
        bvid="BV123",
        title="测试标题",
        url="https://www.bilibili.com/video/BV123",
        transcript_source="subtitle",
        transcript="正文",
    )

    file_path = export_json(result, tmp_path)
    data = json.loads(file_path.read_text(encoding="utf-8"))

    assert file_path.name == "123.json"
    assert data["source"] == "bilibili"
    assert data["aid"] == 123
    assert data["title"] == "测试标题"
    assert data["transcript_source"] == "subtitle"
    assert data["transcript"] == "正文"
    assert data["summary"] == ""

