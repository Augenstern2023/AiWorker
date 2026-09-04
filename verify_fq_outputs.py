# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/verify_fq_outputs.py
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

import csv,json,collections,datetime
from pathlib import Path
base=Path(r'E:\hg')
rank=base/'exports/20260722_top10/top10_rankings.csv'
audit=base/'exports/20260722_top10/group_audit.csv'
flowp=base/'raw/mitm_flows_20260722.json'
responses=base/'raw/mitm_responses_20260722'
with rank.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
with audit.open(encoding='utf-8-sig',newline='') as f: audits=list(csv.DictReader(f))
print('RANK_COLUMNS', list(rows[0]) if rows else [])
print('AUDIT_COLUMNS', list(audits[0]) if audits else [])
label_col=next((c for c in rows[0] if c in ('榜单类型','榜单','ranking_name')),None)
title_col=next((c for c in rows[0] if c in ('剧名','书名','标题','title','book_name')),None)
id_col=next((c for c in rows[0] if c in ('series_id','短剧ID','书籍ID','book_id','id')),None)
groups=collections.Counter(r.get(label_col,'') for r in rows)
dup_within=0
for g in groups:
 vals=[r.get(id_col,'') for r in rows if r.get(label_col,'')==g and r.get(id_col,'')]
 dup_within += len(vals)-len(set(vals))
empty_title=sum(not (r.get(title_col,'') or '').strip() for r in rows) if title_col else None
empty_id=sum(not (r.get(id_col,'') or '').strip() for r in rows) if id_col else None
bad_group_counts={k:v for k,v in groups.items() if v!=10}
json_files=list(responses.glob('*.json'))
valid=0; invalid=[]
for p in json_files:
 try: json.loads(p.read_text(encoding='utf-8')); valid+=1
 except Exception as e: invalid.append((p.name,str(e)))
flows=json.loads(flowp.read_text(encoding='utf-8'))
dates=collections.Counter()
target=[]; status=collections.Counter(); errors=0
for f in flows:
 req=f.get('request') or {}; ts=req.get('timestamp_start')
 if ts:
  dates[datetime.datetime.fromtimestamp(float(ts)).astimezone().date().isoformat()]+=1
 if req.get('host')=='api5-normal-sinfonlineb.fqnovel.com' and 'bookmall/cell/change' in (req.get('path') or ''):
  target.append(f); resp=f.get('response') or {}; status[str(resp.get('status_code','none'))]+=1; errors+=bool(f.get('error'))
metric_keys=['分享','点赞','收藏','热度','评分']
metric_coverage={m:sum(any(m in c and (r.get(c) or '').strip() for c in r) for r in rows) for m in metric_keys}
print(json.dumps({'rows':len(rows),'groups':len(groups),'bad_group_counts':bad_group_counts,'empty_title':empty_title,'empty_id':empty_id,'duplicate_ids_within_group':dup_within,'audit_rows':len(audits),'audit_non_ok':[r for r in audits if r.get('status')!='ok' or str(r.get('Count'))!='10'],'response_json_files':len(json_files),'response_json_valid':valid,'response_json_invalid':invalid,'flow_local_dates':dates,'target_flows':len(target),'target_status':status,'target_errors':errors,'metric_nonempty_rows':metric_coverage},ensure_ascii=False,default=dict))
