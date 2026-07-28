# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/inspect_fq_groups.py
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

import json, urllib.parse, collections, datetime
from pathlib import Path
p=Path(r'E:\hg\raw\mitm_flows_20260722.json')
flows=json.loads(p.read_text(encoding='utf-8'))
counts=collections.Counter(); offsets=collections.defaultdict(set); samples={}
for f in flows:
    req=f.get('request') or {}
    if 'bookmall/cell/change' not in (req.get('path') or ''): continue
    q=urllib.parse.parse_qs(urllib.parse.urlsplit(req.get('url') or ('https://x'+req.get('path',''))).query)
    sel=(q.get('selected_items') or [''])[0]
    sub=(q.get('sub_selected_items') or [''])[0]
    off=(q.get('offset') or [''])[0]
    key=(sel,sub)
    counts[key]+=1; offsets[key].add(off); samples.setdefault(key, req.get('path',''))
for key,n in sorted(counts.items()):
    if key[0] in {'human','comic_series_rank','ai_playlet'}:
        print(json.dumps({'key':key,'flows':n,'offsets':sorted(offsets[key])},ensure_ascii=False))
