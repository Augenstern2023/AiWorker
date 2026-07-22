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
