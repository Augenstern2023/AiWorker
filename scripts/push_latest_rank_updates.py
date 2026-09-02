# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import csv
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from agents.rank_zentao_delivery import deliver_rank_update
from analysis.rank_excel_export import build_rank_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HONGGUO_EXPORTS = Path(r"E:\hg\exports")
DEFAULT_RANK_SHARE = Path(r"\\192.168.1.11\共享\榜单日报")


def load_delivery_environment(project_root: str | Path = PROJECT_ROOT) -> None:
    env_path = Path(project_root) / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip().startswith("ZENTAO_"):
            os.environ.setdefault(key.strip(), value.strip())


def _date_tag(report_date: str) -> str:
    return datetime.strptime(report_date, "%Y-%m-%d").strftime("%Y%m%d")


def discover_today_sources(
    project_root: str | Path = PROJECT_ROOT,
    hongguo_exports_root: str | Path = DEFAULT_HONGGUO_EXPORTS,
    report_date: str | None = None,
) -> dict[str, Path]:
    project = Path(project_root)
    exports = Path(hongguo_exports_root)
    date = report_date or datetime.now().date().isoformat()
    tag = _date_tag(date)
    sources: dict[str, Path] = {}

    douyin = sorted(
        project.glob(f"data/dy/jobs/*/rank/douyin_series_rank_{tag}_*.json"),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
    )
    if douyin:
        sources["dy"] = douyin[-1]

    hongguo = exports / f"{tag}_top10" / "top10_rankings.csv"
    if hongguo.is_file():
        sources["hg"] = hongguo
    return sources


def validate_hongguo_export(source: str | Path) -> dict[str, int]:
    path = Path(source)
    audit_path = path.with_name("group_audit.csv")
    if not audit_path.is_file():
        raise ValueError(f"红果榜单缺少 group_audit.csv：{audit_path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with audit_path.open("r", encoding="utf-8-sig", newline="") as handle:
        audit = list(csv.DictReader(handle))
    complete = [
        row for row in audit
        if str(row.get("status") or "").strip() == "ok"
        and str(row.get("Count") or "").strip() == "10"
    ]
    if len(complete) != 25 or len(rows) != 250:
        raise ValueError(
            f"红果榜单未达到 25 个完整 Top10：groups={len(complete)} rows={len(rows)}"
        )
    return {"groups": len(complete), "rows": len(rows)}


def generate_today_workbooks(
    project_root: str | Path,
    hongguo_exports_root: str | Path,
    report_date: str,
    sources: dict[str, Path],
) -> dict[str, Path]:
    output_dir = Path(project_root) / ".runtime" / "rank-excel" / report_date
    outputs: dict[str, Path] = {}
    for platform in ("hg", "dy"):
        if platform not in sources:
            continue
        outputs[platform] = build_rank_workbook(
            platform,
            report_date,
            root=project_root,
            hongguo_exports_root=hongguo_exports_root,
            output_dir=output_dir,
        )
    return outputs


def copy_workbooks_to_share(
    workbooks: dict[str, Path],
    share_root: str | Path = DEFAULT_RANK_SHARE,
) -> dict[str, dict[str, Any]]:
    root = Path(share_root)
    root.mkdir(parents=True, exist_ok=True)
    copied: dict[str, dict[str, Any]] = {}
    for platform in ("hg", "dy"):
        source = workbooks.get(platform)
        if source is None:
            continue
        destination = root / source.name
        shutil.copy2(source, destination)
        if destination.read_bytes() != source.read_bytes():
            raise OSError(f"共享目录文件校验失败：{destination}")
        copied[platform] = {"path": destination, "url": _file_url(destination)}
    return copied


def _file_url(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if text.startswith("//"):
        return "file:" + quote(text, safe="/")
    return "file:///" + quote(text, safe="/:")


def push_latest_rank_updates(
    project_root: str | Path = PROJECT_ROOT,
    hongguo_exports_root: str | Path = DEFAULT_HONGGUO_EXPORTS,
    report_date: str | None = None,
) -> dict[str, Any]:
    load_delivery_environment(project_root)
    date = report_date or datetime.now().date().isoformat()
    sources = discover_today_sources(project_root, hongguo_exports_root, date)
    workbooks = generate_today_workbooks(project_root, hongguo_exports_root, date, sources)
    shared = copy_workbooks_to_share(
        workbooks,
        os.environ.get("ZENTAO_RANK_SHARE_DIR", str(DEFAULT_RANK_SHARE)),
    )
    results: dict[str, Any] = {}
    for platform in ("hg", "dy"):
        source = sources.get(platform)
        if source is None:
            results[platform] = {"status": "skipped", "reason": "today-source-missing"}
            continue
        if platform == "hg":
            validate_hongguo_export(source)
        results[platform] = deliver_rank_update(
            platform,
            source,
            report_date=date,
            workbook_url=shared.get(platform, {}).get("url", ""),
            workbook_path=str(shared.get(platform, {}).get("path", "")),
        )
    return {
        "report_date": date,
        "sources": {key: str(value) for key, value in sources.items()},
        "workbooks": {key: str(value) for key, value in workbooks.items()},
        "shared_workbooks": {
            key: {"path": str(value["path"]), "url": value["url"]}
            for key, value in shared.items()
        },
        "results": results,
    }


def main() -> int:
    result = push_latest_rank_updates()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    failed = [
        value for value in result["results"].values()
        if value.get("status") not in {"sent", "skipped"}
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
