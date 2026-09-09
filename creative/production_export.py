# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Read-only, atomic production handoff (no repository initialization or writes).

Contract 1.0: project and brief are row dictionaries; characters, relationships,
 outlines, beats and scripts are lists of row dictionaries. Original columns,
 including *_json, versions and timestamps, are preserved. Each *_json column
 also has a decoded alias without that suffix (content, upstream_refs, etc.).
 Episodes are sorted ascending; cast rows are sorted by id. Empty relationships
 are valid. Omitted episode_numbers means every episode in target_episode_count.

The active brief/cast/outline pointers are authoritative. Beats and scripts use
 the latest locked/approved version, never a draft or an older fallback hiding a
 stale locked revision. Incomplete or stale inputs raise ValueError; absent or
 deleted projects raise KeyError; absent database files raise FileNotFoundError.

fingerprint is lowercase SHA256 of canonical UTF-8 JSON (sorted keys, compact
 separators, ensure_ascii=False), excluding fingerprint itself, recursively
 excluding timestamp metadata (keys ending _at, timestamp, timestamps) and raw
 *_json duplicates. Time/duration fields inside story content remain meaningful.
 No clock or machine-dependent database path is included in provenance.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Callable

_LOCKED = {"locked", "approved"}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _row(raw: sqlite3.Row) -> dict[str, Any]:
    result = dict(raw)
    for key, value in tuple(result.items()):
        if key.endswith("_json"):
            try:
                parsed = json.loads(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid {key} on resource {result.get('id')}") from exc
            result[key[:-5]] = parsed
    if "content" in result and not isinstance(result["content"], dict):
        raise ValueError(f"invalid content on resource {result.get('id')}")
    return result


def _locked(item: dict[str, Any], label: str) -> None:
    statuses = _LOCKED | {"brief_locked"} if label == "brief" else _LOCKED
    if item.get("status") not in statuses:
        raise ValueError(f"{label}: missing locked input")


def _refs(item: dict[str, Any], key: str, expected: dict[str, Any], label: str) -> None:
    saved = item.get(key)
    if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in expected.items()):
        raise ValueError(f"{label}: stale or missing {key}")


def _latest(rows: list[dict[str, Any]], stage: str, number: int,
            batch_id: str | None = None) -> dict[str, Any]:
    candidates = [r for r in rows if r["stage"] == stage
                  and r["episode_number"] == number and r["status"] in _LOCKED
                  and (batch_id is None or r["batch_id"] == batch_id)]
    if not candidates:
        raise ValueError(f"{stage}: missing locked input for episode {number}")
    return max(candidates, key=lambda r: (r["version"], r["id"]))


def _authority(item: dict[str, Any], expected: dict[str, Any], outline: dict[str, Any]) -> None:
    """Match repository's strict authority, including historical outline hashes."""
    if not isinstance(item.get("upstream_refs"), dict):
        raise ValueError(f"{item['stage']}: stale or missing upstream_refs for episode {item['episode_number']}")
    saved = dict(item["upstream_refs"])
    fingerprint = saved.pop("outline_content_fingerprint", None)
    content = outline["content"]
    without_overview = {k: v for k, v in content.items() if k != "series_overview"}
    if saved != expected or fingerprint not in {_digest(content), _digest(without_overview)}:
        raise ValueError(f"{item['stage']}: stale or missing upstream_refs for episode {item['episode_number']}")


def _fingerprint_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _fingerprint_payload(v) for k, v in value.items()
                if not k.endswith("_at") and k not in {"timestamp", "timestamps"}
                and not (k.endswith("_json") and k[:-5] in value)}
    if isinstance(value, list):
        return [_fingerprint_payload(v) for v in value]
    return value


class _Inspection:
    def __init__(self, project: dict[str, Any]) -> None:
        self.project = project
        self.stages: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        self.errors: list[ValueError] = []

    def check(self, stage: str, operation: Callable, *, number: int | None = None,
              dependencies: bool = True) -> Any:
        route = 'planning' if stage in {'project', 'brief', 'characters', 'relationships', 'outline'} else 'writing'
        page = {'project': 'brief', 'relationships': 'characters', 'outline': 'outlines', 'script': 'scripts'}.get(stage, stage)
        result = {
            'stage': stage, 'episode_number': number, 'state': 'blocked',
            'versions': [], 'message': '上游未通过检查，无法验证',
            'edit_url': f'/{route}/projects/{self.project["id"]}/{page}',
        }
        self.stages.append(result)
        if not dependencies:
            return None
        try:
            value = operation()
        except ValueError as exc:
            detail = str(exc)
            if 'invalid' in detail or 'JSON' in detail:
                code, message = 'invalid_content', '内容或元数据格式无效，请修复后重新锁定'
            elif 'stale' in detail:
                code, message = 'stale_upstream', '引用的上游版本已变化，请更新后重新锁定'
            else:
                code, message = 'missing_locked_input', '缺少完整的锁定版本，请完成并锁定'
            result.update(state='invalid', message=message)
            self.issues.append({**result, 'code': code})
            self.errors.append(exc)
            return None
        records = value if isinstance(value, list) else [value]
        result.update(state='ready', message='检查通过', versions=[
            {'id': record['id'], 'version': record.get('version', 0)} for record in records
        ])
        return value

    def preview(self, package: dict[str, Any] | None) -> dict[str, Any]:
        return {
            'project': {key: self.project[key] for key in ('id', 'name', 'target_episode_count')},
            'ready': package is not None, 'fingerprint': package['fingerprint'] if package else None,
            'stages': self.stages, 'issues': self.issues,
        }


def _inspect_snapshot(
    db_path: str | Path,
    project_id: int,
    episode_numbers: Iterable[int] | None = None,
) -> tuple[_Inspection, dict[str, Any] | None]:
    path = Path(db_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.execute("BEGIN")
        raw_project = conn.execute(
            "SELECT * FROM creative_projects WHERE id = ? AND deleted = 0", (project_id,)
        ).fetchone()
        if raw_project is None:
            raise KeyError(project_id)
        project = dict(raw_project)
        inspection = _Inspection(project)
        try:
            project = _row(raw_project)
        except ValueError as exc:
            inspection.check('project', lambda: _row(raw_project))
            return inspection, None
        count = project["target_episode_count"]
        if episode_numbers is None:
            numbers = list(range(1, count + 1))
        else:
            if isinstance(episode_numbers, (str, bytes)):
                raise ValueError("episode_numbers must contain positive integers")
            try:
                numbers = list(episode_numbers)
            except TypeError as exc:
                raise ValueError("episode_numbers must be iterable") from exc
            if not numbers or any(type(n) is not int or not 1 <= n <= count for n in numbers):
                raise ValueError("episode_numbers must contain integers in the project range")
            numbers = sorted(set(numbers))
        if not numbers:
            def no_episodes():
                raise ValueError("episode_numbers: project has no target episodes")
            inspection.check('project', no_episodes)
            return inspection, None
        brief_row = conn.execute(
            "SELECT * FROM creative_blueprints WHERE project_id = ? AND id = ?",
            (project_id, project.get("locked_brief_id")),
        ).fetchone()
        def read_brief():
            if brief_row is None:
                raise ValueError("brief: missing locked input")
            value = _row(brief_row)
            _locked(value, 'brief')
            _digest(value)
            return value
        brief = inspection.check('brief', read_brief)
        cast: dict[str, list[dict[str, Any]]] = {}
        for category in ("characters", "relationships"):
            def read_cast():
                values = [_row(raw) for raw in conn.execute(
                    f"SELECT * FROM creative_{category} WHERE project_id = ? AND batch_id = ? ORDER BY id",
                    (project_id, project.get('character_batch_id')),
                )]
                if category == 'characters' and not values:
                    raise ValueError('characters: missing locked input')
                for item in values:
                    _locked(item, category)
                    _refs(item, 'source_refs', {'brief_id': brief['id']}, category)
                _digest(values)
                return values
            cast[category] = inspection.check(category, read_cast, dependencies=brief is not None)
        placeholders = ",".join("?" for _ in numbers)
        episodes = [dict(raw) for raw in conn.execute(
            f"SELECT * FROM creative_episodes WHERE project_id = ? AND episode_number IN ({placeholders}) "
            "AND status IN ('locked', 'approved') ORDER BY episode_number, version, id",
            (project_id, *numbers),
        )]
        upstream_ready = brief is not None and all(value is not None for value in cast.values())
        context = {
            "brief_id": brief["id"], "brief_version": brief["version"],
            "brief_content_fingerprint": _digest(brief["content"]),
            "character_batch_id": project["character_batch_id"],
            "character_content_fingerprint": _digest({
                category: [{"id": r["id"], "version": r["version"], "content": r["content"]}
                           for r in cast[category]]
                for category in ("characters", "relationships")
            }),
        } if upstream_ready else {}
        outlines, beats, scripts = [], [], []
        for number in numbers:
            def read_outline():
                if not project.get('outline_batch_id'):
                    raise ValueError('outline: missing locked input')
                value = _row(_latest(episodes, 'outline', number, project['outline_batch_id']))
                _refs(value, 'upstream_refs', {
                    'brief_id': brief['id'], 'character_batch_id': project['character_batch_id'],
                }, 'outline')
                _digest(value)
                return value
            outline = inspection.check('outline', read_outline, number=number, dependencies=upstream_ready)
            expected = dict(context, outline_batch_id=project['outline_batch_id'],
                            outline_id=outline['id'], outline_version=outline['version']) if outline else {}
            def read_beats():
                value = _row(_latest(episodes, 'beats', number))
                _authority(value, expected, outline)
                _digest(value)
                return value
            beat = inspection.check('beats', read_beats, number=number, dependencies=outline is not None)
            def read_script():
                value = _row(_latest(episodes, 'script', number))
                _authority(value, dict(expected, beats_id=beat['id'], beats_version=beat['version'],
                                       beats_content_fingerprint=_digest(beat['content'])), outline)
                _digest(value)
                return value
            script = inspection.check('script', read_script, number=number, dependencies=beat is not None)
            outlines.append(outline)
            beats.append(beat)
            scripts.append(script)
        if inspection.errors:
            return inspection, None
        package = {
            "schemaVersion": "1.0", "sourceSystem": "AiWorker", "project": project,
            "provenance": {
                "project_id": project["id"], "project_revision": project.get("revision", 0),
                "episode_numbers": numbers, "locked_brief_id": brief["id"],
                "character_batch_id": project["character_batch_id"],
                "outline_batch_id": project["outline_batch_id"],
            },
            "brief": brief, **cast, "outlines": outlines, "beats": beats, "scripts": scripts,
        }
        package["fingerprint"] = _digest(_fingerprint_payload(package))
        return inspection, package
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def export_production_package(
    db_path: str | Path, project_id: int,
    episode_numbers: Iterable[int] | None = None,
) -> dict[str, Any]:
    """Return one validated snapshot. Does not create files or alter SQLite."""
    inspection, package = _inspect_snapshot(db_path, project_id, episode_numbers)
    if inspection.errors:
        raise inspection.errors[0]
    return package


def preview_production_package(db_path: str | Path, project_id: int) -> dict[str, Any]:
    inspection, package = _inspect_snapshot(db_path, project_id)
    return inspection.preview(package)
