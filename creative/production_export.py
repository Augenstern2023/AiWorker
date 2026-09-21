# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Read-only, atomic production handoff (no repository initialization or writes).

Contract 1.0: project and brief are row dictionaries; standard imitation
 projects also include their locked source breakdown. Characters, relationships,
 outlines, beats and scripts are lists of row dictionaries. Original columns,
 including *_json, versions and timestamps, are preserved. Each *_json column
 also has a decoded alias without that suffix (content, upstream_refs, etc.).
 Episodes are sorted ascending; cast rows are sorted by id. Empty relationships
 are valid. Omitted episode_numbers means every episode in target_episode_count.

Sequential projects use the active brief/cast/outline pointers; their beats and
scripts use the latest locked/approved version. Free projects use the exact
resource selected by each current free-mode head. Neither mode falls back from
a draft or stale current selection to an older locked revision. Incomplete or
stale inputs raise ValueError; absent or deleted projects raise KeyError;
absent database files raise FileNotFoundError.

Passing a version_manifest opts into an explicit export-only snapshot. Standard
projects still require every selected downstream resource to match its upstream
references. Free projects may combine independently locked resources; callers
surface the returned consistency warning before export.

fingerprint is lowercase SHA256 of canonical UTF-8 JSON (sorted keys, compact
 separators, ensure_ascii=False), excluding fingerprint itself, recursively
 excluding timestamp metadata (keys ending _at, timestamp, timestamps) and raw
 *_json duplicates. Time/duration fields inside story content remain meaningful.
 No clock or machine-dependent database path is included in provenance.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection

from creative.postgres_repository import PostgresCreativeRepository
from creative.script_service import normalize_script_content
from creative.video_parameter_recommendation import build_video_parameter_source

_LOCKED = {"locked", "approved"}


class _ReadConnection:
    """Small qmark-to-SQLAlchemy adapter for the read-only snapshot queries."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def execute(self, statement: str, parameters: Iterable[Any] = ()) -> Any:
        values = tuple(parameters)
        parts = statement.split("?")
        if len(parts) - 1 != len(values):
            if values:
                raise ValueError("production export query parameter mismatch")
            return self._connection.execute(text(statement)).mappings()
        translated = parts[0]
        bindings: dict[str, Any] = {}
        for index, part in enumerate(parts[1:]):
            key = f"p{index}"
            translated += f":{key}{part}"
            bindings[key] = values[index]
        return self._connection.execute(text(translated), bindings).mappings()


def _stage_edit_url(
    project_id: int,
    stage: str,
    episode_number: int | None = None,
) -> str:
    """Return the registered workspace page for resolving an export issue."""
    if stage == "breakdown":
        return "/selection/topics"
    if stage in {"project", "brief"}:
        return f"/planning/projects/{project_id}/brief"
    if stage in {"characters", "relationships"}:
        return f"/planning/projects/{project_id}/characters"
    if stage == "outline":
        return f"/writing/projects/{project_id}/outlines"
    if stage in {"beats", "script", "video_parameters"}:
        number = max(1, int(episode_number or 1))
        page = "beats" if stage == "beats" else "scripts"
        return f"/writing/projects/{project_id}/episodes/{number}/{page}"
    return "/writing/projects"


def _exports_breakdown(project: dict[str, Any]) -> bool:
    """Only standard imitation projects carry their source breakdown."""
    return (
        str(project.get("creation_mode") or "imitation") == "imitation"
        and str(project.get("workflow_mode") or "sequential") != "free"
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _row(raw: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    for key, value in tuple(result.items()):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            result[key] = value.timestamp()
            value = result[key]
        elif isinstance(value, uuid.UUID):
            result[key] = str(value)
            value = result[key]
        if key.endswith("_json"):
            try:
                parsed = json.loads(value) if isinstance(value, (str, bytes)) else value
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid {key} on resource {result.get('id')}") from exc
            if key == "content_json" and not isinstance(parsed, dict):
                raise ValueError(f"invalid {key} on resource {result.get('id')}")
            if not isinstance(value, (str, bytes)):
                result[key] = json.dumps(parsed, ensure_ascii=False)
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
        self.version_selection: dict[str, Any] = {
            "strategy": "current",
            "manifest": {},
            "candidates": {},
            "warnings": [],
        }

    def check(self, stage: str, operation: Callable, *, number: int | None = None,
              dependencies: bool = True) -> Any:
        result = {
            'stage': stage, 'episode_number': number, 'state': 'blocked',
            'versions': [], 'message': '上游未通过检查，无法验证',
            'edit_url': _stage_edit_url(self.project["id"], stage, number),
        }
        self.stages.append(result)
        if not dependencies:
            return None
        try:
            value = operation()
        except ValueError as exc:
            detail = str(exc)
            if stage == "video_parameters":
                if "stale" in detail:
                    code = "stale_video_parameters"
                    message = "剧本或导出版本已变化，请在剧本页重新分析视频规格"
                elif "incomplete" in detail:
                    code = "incomplete_video_parameters"
                    message = "视频规格建议未覆盖完整全剧，请在全剧剧本完成后重新分析"
                elif "not ready" in detail:
                    code = "pending_video_parameters"
                    message = "视频规格建议尚未完成，请在剧本页完成分析"
                elif "invalid" in detail or "JSON" in detail:
                    code = "invalid_video_parameters"
                    message = "视频规格建议格式无效，请在剧本页重新分析"
                else:
                    code = "missing_video_parameters"
                    message = "缺少视频生成规格建议，请先在剧本页完成分析"
            elif 'invalid' in detail or 'JSON' in detail:
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
            {'id': record['id'], 'version': record.get('version', 0)}
            for record in records if isinstance(record, dict) and 'id' in record
        ])
        return value

    def preview(self, package: dict[str, Any] | None) -> dict[str, Any]:
        return {
            'project': {
                key: self.project[key]
                for key in ('id', 'name', 'target_episode_count', 'workflow_mode')
            },
            'ready': package is not None, 'fingerprint': package['fingerprint'] if package else None,
            'stages': self.stages, 'issues': self.issues,
            'version_selection': self.version_selection,
        }


def _scope_provenance(
    project: dict[str, Any], numbers: list[int],
) -> dict[str, Any]:
    total = int(project["target_episode_count"])
    complete = numbers == list(range(1, total + 1))
    return {
        "export_scope": "full" if complete else "partial",
        "project_total_episodes": total,
        "exported_episode_count": len(numbers),
    }


def _current_video_parameter_source(
    conn: "_ReadConnection", project: dict[str, Any],
) -> dict[str, Any]:
    """Build the recommendation fingerprint from this same read transaction."""
    scope = "free" if str(project.get("workflow_mode")) == "free" else "sequential"
    episodes = []
    for raw in conn.execute(
        "SELECT * FROM creative_episodes WHERE project_id = ? AND resource_scope = ? "
        "ORDER BY stage, episode_number, version",
        (int(project["id"]), scope),
    ).fetchall():
        item = _row(raw)
        if item.get("stage") == "script":
            item["content"] = normalize_script_content(item.get("content") or {})
        episodes.append(item)
    heads = [
        dict(row) for row in conn.execute(
            "SELECT * FROM creative_resource_heads WHERE project_id = ? "
            "AND resource_scope = ? ORDER BY stage, episode_number",
            (int(project["id"]), scope),
        ).fetchall()
    ]
    return build_video_parameter_source({
        **project,
        "episodes": episodes,
        "resource_heads": heads,
    })


def _read_video_parameter_recommendation(
    conn: "_ReadConnection",
    project: dict[str, Any],
    package: dict[str, Any],
) -> dict[str, Any]:
    raw = conn.execute(
        "SELECT * FROM creative_video_parameter_recommendations "
        "WHERE project_id = ?",
        (int(project["id"]),),
    ).fetchone()
    if raw is None:
        raise ValueError("video_parameters: missing recommendation")
    try:
        record = _row(raw)
    except ValueError as exc:
        raise ValueError("video_parameters: invalid JSON") from exc
    record["source_complete"] = bool(record.get("source_complete"))
    if str(record.get("status") or "") != "ready":
        raise ValueError("video_parameters: not ready")
    recommendation = record.get("recommendation")
    if not isinstance(recommendation, dict) or not isinstance(
        recommendation.get("models"), dict
    ) or not recommendation["models"]:
        raise ValueError("video_parameters: invalid recommendation")
    if not record["source_complete"]:
        raise ValueError("video_parameters: incomplete source")

    expected_numbers = list(
        range(1, int(project.get("target_episode_count") or 0) + 1)
    )
    selected_scripts = list(package.get("scripts") or [])
    selected_numbers = [int(item.get("episode_number") or 0) for item in selected_scripts]
    if selected_numbers == expected_numbers:
        frozen_scripts = [
            {
                "id": int(item.get("id") or 0),
                "episode_number": int(item.get("episode_number") or 0),
                "version": int(item.get("version") or 0),
                "status": str(item.get("status") or ""),
                "content": normalize_script_content(item.get("content") or {}),
            }
            for item in selected_scripts
        ]
        fingerprint_payload = {
            "project_id": int(project["id"]),
            "target_platform": str(project.get("target_platform") or ""),
            "target_episode_count": int(project.get("target_episode_count") or 0),
            "scripts": frozen_scripts,
        }
        source = {
            **fingerprint_payload,
            "episode_numbers": selected_numbers,
            "complete": bool(expected_numbers),
            "fingerprint": _digest(fingerprint_payload),
        }
    else:
        source = _current_video_parameter_source(conn, project)
    if (
        not source.get("complete")
        or list(record.get("source_episode_numbers") or []) != expected_numbers
    ):
        raise ValueError("video_parameters: incomplete source")
    if str(record.get("source_fingerprint") or "") != str(source["fingerprint"]):
        raise ValueError("video_parameters: stale recommendation")

    current_scripts = {
        int(item["episode_number"]): item for item in source.get("scripts") or []
    }
    for item in package.get("scripts") or []:
        number = int(item.get("episode_number") or 0)
        selected = {
            "id": int(item.get("id") or 0),
            "episode_number": number,
            "version": int(item.get("version") or 0),
            "status": str(item.get("status") or ""),
            "content": normalize_script_content(item.get("content") or {}),
        }
        if current_scripts.get(number) != selected:
            raise ValueError("video_parameters: stale export version")

    record.update({
        "stale": False,
        "current_source_fingerprint": source["fingerprint"],
        "current_episode_numbers": source["episode_numbers"],
        "current_source_complete": source["complete"],
    })
    return record


def _attach_video_parameter_recommendation(
    conn: "_ReadConnection",
    inspection: _Inspection,
    project: dict[str, Any],
    package: dict[str, Any] | None,
) -> bool:
    record = inspection.check(
        "video_parameters",
        lambda: _read_video_parameter_recommendation(conn, project, package or {}),
        dependencies=package is not None,
    )
    if package is None or record is None:
        return False
    package["video_parameter_recommendation"] = record
    package["fingerprint"] = _digest(_fingerprint_payload(package))
    return True


def _free_head(
    heads: dict[tuple[str, int], dict[str, Any]], stage: str, number: int = 0,
) -> dict[str, Any]:
    head = heads.get((stage, number))
    if head is None:
        suffix = f" for episode {number}" if number else ""
        raise ValueError(f"{stage}: missing locked input{suffix}")
    return head


def _free_head_version(
    head: dict[str, Any], resource: dict[str, Any], label: str,
) -> None:
    if int(head.get("resource_version") or 0) != int(resource.get("version") or 0):
        raise ValueError(f"{label}: stale resource head")


def _free_head_provenance(head: dict[str, Any]) -> dict[str, Any]:
    """Keep the selected source explicit without volatile head audit metadata."""
    return {
        "resource_scope": "free",
        "stage": head["stage"],
        "episode_number": int(head["episode_number"]),
        "resource_ref": str(head["resource_ref"]),
        "resource_version": int(head["resource_version"]),
    }


def _manifest_item(resource_ref: Any, version: Any) -> dict[str, Any]:
    return {
        "resource_ref": str(resource_ref),
        "version": int(version or 0),
    }


def _package_version_manifest(package: dict[str, Any]) -> dict[str, Any]:
    brief = package["brief"]
    characters = package["characters"]
    outlines = {int(item["episode_number"]): item for item in package["outlines"]}
    beats = {int(item["episode_number"]): item for item in package["beats"]}
    scripts = {int(item["episode_number"]): item for item in package["scripts"]}
    provenance = package["provenance"]
    character_version = max((int(item.get("version") or 0) for item in characters), default=0)
    outline_version = max((int(item.get("version") or 0) for item in outlines.values()), default=0)
    manifest = {
        "brief": _manifest_item(brief["id"], brief.get("version")),
        "characters": _manifest_item(
            provenance["character_batch_id"], character_version,
        ),
        "outline": _manifest_item(
            provenance["outline_batch_id"], outline_version,
        ),
        "episodes": {
            str(number): {
                "outline": _manifest_item(outlines[number]["id"], outlines[number].get("version")),
                "beats": _manifest_item(beats[number]["id"], beats[number].get("version")),
                "script": _manifest_item(scripts[number]["id"], scripts[number].get("version")),
            }
            for number in provenance["episode_numbers"]
        },
    }
    breakdown = package.get("breakdown")
    if breakdown is not None:
        manifest = {
            "breakdown": _manifest_item(
                breakdown["id"], breakdown.get("version")
            ),
            **manifest,
        }
    return manifest


def _requested_manifest_item(
    manifest: dict[str, Any], stage: str, number: int | None = None,
) -> dict[str, Any] | None:
    if not isinstance(manifest, dict):
        raise ValueError("version_manifest: must be an object")
    if number is None:
        value = manifest.get(stage)
    else:
        episodes = manifest.get("episodes", {})
        if not isinstance(episodes, dict):
            raise ValueError("version_manifest: episodes must be an object")
        episode = episodes.get(str(number), episodes.get(number, {}))
        if episode is None:
            return None
        if not isinstance(episode, dict):
            raise ValueError("version_manifest: episode entry must be an object")
        value = episode.get(stage)
    if value is None:
        return None
    if not isinstance(value, dict) or not str(value.get("resource_ref") or "").strip():
        raise ValueError("version_manifest: resource_ref is required")
    if "version" in value and (
        type(value["version"]) is not int or int(value["version"]) < 1
    ):
        raise ValueError("version_manifest: version must be a positive integer")
    return value


def _select_resource(
    candidates: list[Any], requested: dict[str, Any] | None, preferred_ref: Any,
    *, ref: Callable[[Any], Any], compatible: Callable[[Any], bool],
) -> Any | None:
    if requested is not None:
        requested_ref = str(requested["resource_ref"])
        return next(
            (item for item in candidates if str(ref(item)) == requested_ref), None,
        )
    preferred = str(preferred_ref or "")
    if preferred:
        selected = next(
            (
                item for item in candidates
                if str(ref(item)) == preferred and compatible(item)
            ),
            None,
        )
        if selected is not None:
            return selected
    compatible_rows = [item for item in candidates if compatible(item)]
    if not compatible_rows:
        return None
    return max(compatible_rows, key=lambda item: (
        int((item[0] if isinstance(item, tuple) else item).get("version") or 0),
        int((item[0] if isinstance(item, tuple) else item).get("id") or 0),
    ))


def _assert_requested_version(
    requested: dict[str, Any] | None, version: Any, label: str,
) -> None:
    if requested is not None and "version" in requested:
        if int(requested["version"]) != int(version or 0):
            raise ValueError(f"{label}: selected version no longer exists")


def _locked_cast_batches(
    conn: _ReadConnection, project_id: int, resource_scope: str,
) -> list[tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]]:
    characters = []
    relationships = []
    for target, table in (
        (characters, "creative_characters"),
        (relationships, "creative_relationships"),
    ):
        for raw in conn.execute(
            f"SELECT * FROM {table} WHERE project_id = ? "
            "AND resource_scope = ? ORDER BY version, id",
            (project_id, resource_scope),
        ).fetchall():
            try:
                target.append(_row(raw))
            except ValueError:
                continue
    batch_ids = {str(item["batch_id"]) for item in characters}
    result = []
    for batch_id in batch_ids:
        cast = [item for item in characters if str(item["batch_id"]) == batch_id]
        relations = [item for item in relationships if str(item["batch_id"]) == batch_id]
        if cast and all(item.get("status") in _LOCKED for item in cast + relations):
            result.append((cast[0], cast, relations))
    return result


def _locked_outline_batches(
    conn: _ReadConnection, project_id: int, resource_scope: str,
    numbers: list[int],
) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    rows = []
    for raw in conn.execute(
        "SELECT * FROM creative_episodes WHERE project_id = ? "
        "AND resource_scope = ? AND stage = 'outline' "
        "ORDER BY version, episode_number, id",
        (project_id, resource_scope),
    ).fetchall():
        try:
            rows.append(_row(raw))
        except ValueError:
            continue
    result = []
    for batch_id in {str(item["batch_id"]) for item in rows}:
        batch = [item for item in rows if str(item["batch_id"]) == batch_id]
        selected = [item for item in batch if int(item["episode_number"]) in numbers]
        if (
            {int(item["episode_number"]) for item in selected} == set(numbers)
            and all(item.get("status") in _LOCKED for item in selected)
        ):
            result.append((selected[0], selected))
    return result


def _custom_free_snapshot(
    conn: _ReadConnection,
    inspection: _Inspection,
    project: dict[str, Any],
    numbers: list[int],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    project_id = int(project["id"])
    heads = {
        (str(row["stage"]), int(row["episode_number"])): dict(row)
        for row in conn.execute(
            "SELECT * FROM creative_resource_heads WHERE project_id = ? "
            "AND resource_scope = 'free'",
            (project_id,),
        ).fetchall()
    }
    briefs = [
        _row(row) for row in conn.execute(
            "SELECT * FROM creative_blueprints WHERE project_id = ? "
            "AND resource_scope = 'free' AND status IN ('brief_locked', 'locked', 'approved')",
            (project_id,),
        ).fetchall()
    ]
    brief_request = _requested_manifest_item(manifest, "brief")
    brief_row = _select_resource(
        briefs, brief_request, (heads.get(("brief", 0)) or {}).get("resource_ref"),
        ref=lambda item: item["id"], compatible=lambda item: True,
    )

    def read_brief() -> dict[str, Any]:
        if brief_row is None:
            raise ValueError("brief: missing locked input")
        _assert_requested_version(brief_request, brief_row.get("version"), "brief")
        _digest(brief_row)
        return brief_row

    brief = inspection.check("brief", read_brief)
    cast_batches = _locked_cast_batches(conn, project_id, "free")
    cast_request = _requested_manifest_item(manifest, "characters")
    cast_batch = _select_resource(
        cast_batches, cast_request, (heads.get(("characters", 0)) or {}).get("resource_ref"),
        ref=lambda item: item[0]["batch_id"], compatible=lambda item: True,
    )

    def read_cast(category: str) -> list[dict[str, Any]]:
        if cast_batch is None:
            raise ValueError(f"{category}: missing locked input")
        _assert_requested_version(cast_request, cast_batch[0].get("version"), category)
        values = cast_batch[1] if category == "characters" else cast_batch[2]
        _digest(values)
        return values

    characters = inspection.check("characters", lambda: read_cast("characters"))
    relationships = inspection.check("relationships", lambda: read_cast("relationships"))
    outline_batches = _locked_outline_batches(conn, project_id, "free", numbers)
    outline_request = _requested_manifest_item(manifest, "outline")
    outline_batch = _select_resource(
        outline_batches, outline_request, (heads.get(("outline", 0)) or {}).get("resource_ref"),
        ref=lambda item: item[0]["batch_id"], compatible=lambda item: True,
    )

    def read_outline(number: int) -> dict[str, Any]:
        if outline_batch is None:
            raise ValueError(f"outline: missing locked input for episode {number}")
        _assert_requested_version(outline_request, outline_batch[0].get("version"), "outline")
        value = next(
            (
                item for item in outline_batch[1]
                if int(item["episode_number"]) == number
            ),
            None,
        )
        if value is None:
            raise ValueError(f"outline: missing locked input for episode {number}")
        _digest(value)
        return value

    outlines = [
        inspection.check(
            "outline", lambda n=number: read_outline(n),
            number=number,
        )
        for number in numbers
    ]
    episode_rows = []
    for raw in conn.execute(
        "SELECT * FROM creative_episodes WHERE project_id = ? "
        "AND resource_scope = 'free' AND stage IN ('beats', 'script') "
        "AND status IN ('locked', 'approved') ORDER BY version, id",
        (project_id,),
    ).fetchall():
        try:
            episode_rows.append(_row(raw))
        except ValueError:
            continue

    def read_episode(stage: str, number: int) -> dict[str, Any]:
        request = _requested_manifest_item(manifest, stage, number)
        candidates = [
            item for item in episode_rows
            if item["stage"] == stage and int(item["episode_number"]) == number
        ]
        selected = _select_resource(
            candidates, request, (heads.get((stage, number)) or {}).get("resource_ref"),
            ref=lambda item: item["id"], compatible=lambda item: True,
        )
        if selected is None:
            raise ValueError(f"{stage}: missing locked input for episode {number}")
        _assert_requested_version(request, selected.get("version"), stage)
        _digest(selected)
        return selected

    beats = [
        inspection.check("beats", lambda n=number: read_episode("beats", n), number=number)
        for number in numbers
    ]
    scripts = [
        inspection.check("script", lambda n=number: read_episode("script", n), number=number)
        for number in numbers
    ]
    if inspection.errors or brief is None or characters is None or relationships is None:
        return None
    package = {
        "schemaVersion": "1.0", "sourceSystem": "AiWorker", "project": project,
        "provenance": {
            "project_id": project_id,
            "project_revision": project.get("revision", 0),
            "episode_numbers": numbers,
            **_scope_provenance(project, numbers),
            "workflow_mode": "free",
            "locked_brief_id": brief["id"],
            "character_batch_id": characters[0]["batch_id"],
            "outline_batch_id": outlines[0]["batch_id"],
            "resource_heads": [],
        },
        "brief": brief, "characters": characters, "relationships": relationships,
        "outlines": outlines, "beats": beats, "scripts": scripts,
    }
    package["version_manifest"] = _package_version_manifest(package)
    package["provenance"]["resource_heads"] = [
        {
            "resource_scope": "free", "stage": stage,
            "episode_number": number,
            "resource_ref": item["resource_ref"],
            "resource_version": item["version"],
        }
        for stage, number, item in [
            ("brief", 0, package["version_manifest"]["brief"]),
            ("characters", 0, package["version_manifest"]["characters"]),
            ("outline", 0, package["version_manifest"]["outline"]),
            *(
                (stage, number, package["version_manifest"]["episodes"][str(number)][stage])
                for stage in ("beats", "script") for number in numbers
            ),
        ]
    ]
    package["fingerprint"] = _digest(_fingerprint_payload(package))
    return package


def _refs_match(
    item: dict[str, Any], key: str, expected: dict[str, Any], label: str,
) -> bool:
    try:
        _refs(item, key, expected, label)
    except ValueError:
        return False
    return True


def _authority_matches(
    item: dict[str, Any], expected: dict[str, Any], outline: dict[str, Any],
) -> bool:
    try:
        _authority(item, expected, outline)
    except ValueError:
        return False
    return True


def _custom_sequential_snapshot(
    conn: _ReadConnection,
    inspection: _Inspection,
    project: dict[str, Any],
    numbers: list[int],
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    project_id = int(project["id"])
    breakdown = None
    if _exports_breakdown(project):
        breakdown_request = _requested_manifest_item(manifest, "breakdown")

        def read_breakdown() -> dict[str, Any]:
            raw = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE id = ? AND topic_id = ?",
                (project.get("breakdown_id"), project.get("topic_id")),
            ).fetchone()
            if raw is None:
                raise ValueError("breakdown: missing locked input")
            value = _row(raw)
            _locked(value, "breakdown")
            if (
                breakdown_request is not None
                and str(breakdown_request["resource_ref"]) != str(value["id"])
            ):
                raise ValueError("breakdown: selected version no longer exists")
            _assert_requested_version(
                breakdown_request, value.get("version"), "breakdown"
            )
            _digest(value)
            return value

        breakdown = inspection.check("breakdown", read_breakdown)
    briefs = [
        _row(row) for row in conn.execute(
            "SELECT * FROM creative_blueprints WHERE project_id = ? "
            "AND resource_scope = 'sequential' "
            "AND status IN ('brief_locked', 'locked', 'approved')",
            (project_id,),
        ).fetchall()
    ]
    brief_request = _requested_manifest_item(manifest, "brief")
    brief_row = _select_resource(
        briefs, brief_request, project.get("locked_brief_id"),
        ref=lambda item: item["id"], compatible=lambda item: True,
    )

    def read_brief() -> dict[str, Any]:
        if brief_row is None:
            raise ValueError("brief: missing locked input")
        _assert_requested_version(brief_request, brief_row.get("version"), "brief")
        _digest(brief_row)
        return brief_row

    brief = inspection.check("brief", read_brief)
    cast_batches = _locked_cast_batches(conn, project_id, "sequential")
    cast_request = _requested_manifest_item(manifest, "characters")

    def cast_compatible(
        batch: tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]],
    ) -> bool:
        return brief is not None and all(
            _refs_match(item, "source_refs", {"brief_id": brief["id"]}, "characters")
            for item in batch[1] + batch[2]
        )

    cast_batch = _select_resource(
        cast_batches, cast_request, project.get("character_batch_id"),
        ref=lambda item: item[0]["batch_id"], compatible=cast_compatible,
    )

    def read_cast(category: str) -> list[dict[str, Any]]:
        if cast_batch is None:
            raise ValueError(f"{category}: missing locked input")
        _assert_requested_version(cast_request, cast_batch[0].get("version"), category)
        values = cast_batch[1] if category == "characters" else cast_batch[2]
        for item in values:
            _refs(item, "source_refs", {"brief_id": brief["id"]}, category)
        _digest(values)
        return values

    characters = inspection.check(
        "characters", lambda: read_cast("characters"), dependencies=brief is not None,
    )
    relationships = inspection.check(
        "relationships", lambda: read_cast("relationships"), dependencies=brief is not None,
    )
    upstream_ready = (
        brief is not None and characters is not None and relationships is not None
    )
    outline_batches = _locked_outline_batches(conn, project_id, "sequential", numbers)
    outline_request = _requested_manifest_item(manifest, "outline")

    def outline_compatible(batch: tuple[dict[str, Any], list[dict[str, Any]]]) -> bool:
        return upstream_ready and all(
            _refs_match(
                item, "upstream_refs",
                {"brief_id": brief["id"], "character_batch_id": characters[0]["batch_id"]},
                "outline",
            )
            for item in batch[1]
        )

    outline_batch = _select_resource(
        outline_batches, outline_request, project.get("outline_batch_id"),
        ref=lambda item: item[0]["batch_id"], compatible=outline_compatible,
    )
    outline_rows = outline_batch[1] if outline_batch is not None else []

    def read_outline(number: int) -> dict[str, Any]:
        if outline_batch is None:
            raise ValueError(f"outline: missing locked input for episode {number}")
        _assert_requested_version(outline_request, outline_batch[0].get("version"), "outline")
        value = next(
            (item for item in outline_rows if int(item["episode_number"]) == number),
            None,
        )
        if value is None:
            raise ValueError(f"outline: missing locked input for episode {number}")
        _refs(value, "upstream_refs", {
            "brief_id": brief["id"],
            "character_batch_id": characters[0]["batch_id"],
        }, "outline")
        _digest(value)
        return value

    outlines = [
        inspection.check(
            "outline", lambda n=number: read_outline(n), number=number,
            dependencies=upstream_ready,
        )
        for number in numbers
    ]
    episode_rows = []
    for raw in conn.execute(
        "SELECT * FROM creative_episodes WHERE project_id = ? "
        "AND resource_scope = 'sequential' AND stage IN ('beats', 'script') "
        "AND status IN ('locked', 'approved') ORDER BY version, id",
        (project_id,),
    ).fetchall():
        try:
            episode_rows.append(_row(raw))
        except ValueError:
            continue
    context = {
        "brief_id": brief["id"],
        "brief_version": brief["version"],
        "brief_content_fingerprint": _digest(brief["content"]),
        "character_batch_id": characters[0]["batch_id"],
        "character_content_fingerprint": _digest({
            "characters": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in characters
            ],
            "relationships": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in relationships
            ],
        }),
    } if upstream_ready else {}
    beats: list[dict[str, Any] | None] = []
    scripts: list[dict[str, Any] | None] = []
    for index, number in enumerate(numbers):
        outline = outlines[index]
        expected = (
            dict(
                context,
                outline_batch_id=outline["batch_id"],
                outline_id=outline["id"],
                outline_version=outline["version"],
            )
            if outline is not None else {}
        )
        beat_request = _requested_manifest_item(manifest, "beats", number)
        beat_candidates = [
            item for item in episode_rows
            if item["stage"] == "beats" and int(item["episode_number"]) == number
        ]
        beat_row = _select_resource(
            beat_candidates, beat_request, None,
            ref=lambda item: item["id"],
            compatible=lambda item: outline is not None and _authority_matches(
                item, expected, outline,
            ),
        )

        def read_beats() -> dict[str, Any]:
            if beat_row is None:
                raise ValueError(f"beats: missing locked input for episode {number}")
            _assert_requested_version(beat_request, beat_row.get("version"), "beats")
            _authority(beat_row, expected, outline)
            _digest(beat_row)
            return beat_row

        beat = inspection.check(
            "beats", read_beats, number=number, dependencies=outline is not None,
        )
        beats.append(beat)
        script_request = _requested_manifest_item(manifest, "script", number)
        script_candidates = [
            item for item in episode_rows
            if item["stage"] == "script" and int(item["episode_number"]) == number
        ]
        script_expected = (
            dict(
                expected,
                beats_id=beat["id"],
                beats_version=beat["version"],
                beats_content_fingerprint=_digest(beat["content"]),
            )
            if beat is not None else {}
        )
        script_row = _select_resource(
            script_candidates, script_request, None,
            ref=lambda item: item["id"],
            compatible=lambda item: beat is not None and _authority_matches(
                item, script_expected, outline,
            ),
        )

        def read_script() -> dict[str, Any]:
            if script_row is None:
                raise ValueError(f"script: missing locked input for episode {number}")
            _assert_requested_version(script_request, script_row.get("version"), "script")
            _authority(script_row, script_expected, outline)
            _digest(script_row)
            return script_row

        scripts.append(inspection.check(
            "script", read_script, number=number, dependencies=beat is not None,
        ))
    if inspection.errors:
        return None
    package = {
        "schemaVersion": "1.0", "sourceSystem": "AiWorker", "project": project,
        "provenance": {
            "project_id": project_id,
            "project_revision": project.get("revision", 0),
            "episode_numbers": numbers,
            "workflow_mode": "sequential",
            **_scope_provenance(project, numbers),
            "locked_brief_id": brief["id"],
            "character_batch_id": characters[0]["batch_id"],
            "outline_batch_id": outlines[0]["batch_id"],
        },
        "brief": brief, "characters": characters, "relationships": relationships,
        "outlines": outlines, "beats": beats, "scripts": scripts,
    }
    if breakdown is not None:
        package["breakdown"] = breakdown
        package["provenance"].update({
            "breakdown_id": breakdown["id"],
            "breakdown_version": breakdown["version"],
        })
    package["version_manifest"] = _package_version_manifest(package)
    package["fingerprint"] = _digest(_fingerprint_payload(package))
    return package


def _candidate_item(
    resource_ref: Any, version: Any, selected_ref: Any, compatible: bool,
) -> dict[str, Any]:
    value = str(resource_ref)
    return {
        "resource_ref": value,
        "version": int(version or 0),
        "label": f"v{int(version or 0)} · {value[:12]}",
        "compatible": bool(compatible),
        "disabled_reason": "与已选上游版本不兼容" if not compatible else "",
        "selected": value == str(selected_ref),
    }


def _version_candidates(
    conn: _ReadConnection,
    project: dict[str, Any],
    package: dict[str, Any],
    numbers: list[int],
) -> dict[str, Any]:
    project_id = int(project["id"])
    workflow_mode = str(project.get("workflow_mode") or "sequential")
    scope = "free" if workflow_mode == "free" else "sequential"
    strict = workflow_mode != "free"
    manifest = package["version_manifest"]
    brief = package["brief"]
    characters = package["characters"]
    relationships = package["relationships"]
    outlines = {int(item["episode_number"]): item for item in package["outlines"]}
    beats = {int(item["episode_number"]): item for item in package["beats"]}
    briefs = []
    for raw in conn.execute(
        "SELECT * FROM creative_blueprints WHERE project_id = ? AND resource_scope = ? "
        "AND status IN ('brief_locked', 'locked', 'approved') ORDER BY version DESC, id DESC",
        (project_id, scope),
    ).fetchall():
        try:
            briefs.append(_row(raw))
        except ValueError:
            continue
    selected_brief_ref = manifest["brief"]["resource_ref"]
    cast_batches = _locked_cast_batches(conn, project_id, scope)
    selected_cast_ref = manifest["characters"]["resource_ref"]
    outline_batches = _locked_outline_batches(conn, project_id, scope, numbers)
    selected_outline_ref = manifest["outline"]["resource_ref"]
    context = {
        "brief_id": brief["id"],
        "brief_version": brief["version"],
        "brief_content_fingerprint": _digest(brief["content"]),
        "character_batch_id": selected_cast_ref,
        "character_content_fingerprint": _digest({
            "characters": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in characters
            ],
            "relationships": [
                {"id": item["id"], "version": item["version"], "content": item["content"]}
                for item in relationships
            ],
        }),
    }
    candidates: dict[str, Any] = {
        "brief": [
            _candidate_item(item["id"], item["version"], selected_brief_ref, True)
            for item in briefs
        ],
        "characters": [],
        "outline": [],
        "episodes": {},
    }
    for first, cast, relations in cast_batches:
        compatible = not strict or all(
            _refs_match(item, "source_refs", {"brief_id": brief["id"]}, "characters")
            for item in cast + relations
        )
        candidates["characters"].append(_candidate_item(
            first["batch_id"], first["version"], selected_cast_ref, compatible,
        ))
    for first, rows in outline_batches:
        compatible = not strict or all(
            _refs_match(item, "upstream_refs", {
                "brief_id": brief["id"], "character_batch_id": selected_cast_ref,
            }, "outline")
            for item in rows
        )
        candidates["outline"].append(_candidate_item(
            first["batch_id"], first["version"], selected_outline_ref, compatible,
        ))
    episode_rows = []
    for raw in conn.execute(
        "SELECT * FROM creative_episodes WHERE project_id = ? AND resource_scope = ? "
        "AND stage IN ('beats', 'script') AND status IN ('locked', 'approved') "
        "ORDER BY version DESC, id DESC",
        (project_id, scope),
    ).fetchall():
        try:
            episode_rows.append(_row(raw))
        except ValueError:
            continue
    for number in numbers:
        outline = outlines[number]
        beat = beats[number]
        expected = dict(
            context,
            outline_batch_id=selected_outline_ref,
            outline_id=outline["id"],
            outline_version=outline["version"],
        )
        script_expected = dict(
            expected,
            beats_id=beat["id"],
            beats_version=beat["version"],
            beats_content_fingerprint=_digest(beat["content"]),
        )
        selected = manifest["episodes"][str(number)]
        candidates["episodes"][str(number)] = {
            "beats": [
                _candidate_item(
                    item["id"], item["version"], selected["beats"]["resource_ref"],
                    not strict or _authority_matches(item, expected, outline),
                )
                for item in episode_rows
                if item["stage"] == "beats" and int(item["episode_number"]) == number
            ],
            "script": [
                _candidate_item(
                    item["id"], item["version"], selected["script"]["resource_ref"],
                    not strict or _authority_matches(item, script_expected, outline),
                )
                for item in episode_rows
                if item["stage"] == "script" and int(item["episode_number"]) == number
            ],
        }
    return candidates


def _attach_version_selection(
    conn: _ReadConnection,
    inspection: _Inspection,
    project: dict[str, Any],
    package: dict[str, Any] | None,
    numbers: list[int],
    strategy: str,
) -> None:
    inspection.version_selection["strategy"] = strategy
    if package is None:
        return
    inspection.version_selection["manifest"] = package["version_manifest"]
    inspection.version_selection["candidates"] = _version_candidates(
        conn, project, package, numbers,
    )
    if strategy == "custom" and str(project.get("workflow_mode")) == "free":
        inspection.version_selection["warnings"] = [
            "自由创作的自定义版本可独立组合，请确认跨阶段内容一致；导出后会固化为不可变快照。"
        ]


def _inspect_free_snapshot(
    conn: _ReadConnection,
    inspection: _Inspection,
    project: dict[str, Any],
    numbers: list[int],
) -> dict[str, Any] | None:
    """Resolve a free project exclusively through its current resource heads."""
    project_id = int(project["id"])
    heads = {
        (str(row["stage"]), int(row["episode_number"])): dict(row)
        for row in conn.execute(
            "SELECT * FROM creative_resource_heads WHERE project_id = ? "
            "AND resource_scope = 'free'",
            (project_id,),
        ).fetchall()
    }

    def read_brief() -> dict[str, Any]:
        head = _free_head(heads, "brief")
        try:
            resource_id = int(head["resource_ref"])
        except (TypeError, ValueError) as exc:
            raise ValueError("brief: invalid resource head") from exc
        raw = conn.execute(
            "SELECT * FROM creative_blueprints WHERE project_id = ? AND id = ? "
            "AND resource_scope = 'free'",
            (project_id, resource_id),
        ).fetchone()
        if raw is None:
            raise ValueError("brief: missing locked input")
        value = _row(raw)
        _locked(value, "brief")
        _free_head_version(head, value, "brief")
        _digest(value)
        return value

    brief = inspection.check("brief", read_brief)

    def read_cast(category: str) -> list[dict[str, Any]]:
        head = _free_head(heads, "characters")
        values = [
            _row(raw)
            for raw in conn.execute(
                f"SELECT * FROM creative_{category} WHERE project_id = ? "
                "AND batch_id = ? AND resource_scope = 'free' ORDER BY id",
                (project_id, str(head["resource_ref"])),
            ).fetchall()
        ]
        if category == "characters" and not values:
            raise ValueError("characters: missing locked input")
        for item in values:
            _locked(item, category)
            _free_head_version(head, item, category)
        _digest(values)
        return values

    characters = inspection.check("characters", lambda: read_cast("characters"))
    relationships = inspection.check("relationships", lambda: read_cast("relationships"))

    def read_outline(number: int) -> dict[str, Any]:
        head = _free_head(heads, "outline")
        raw = conn.execute(
            "SELECT * FROM creative_episodes WHERE project_id = ? "
            "AND stage = 'outline' AND episode_number = ? AND batch_id = ? "
            "AND resource_scope = 'free'",
            (project_id, number, str(head["resource_ref"])),
        ).fetchone()
        if raw is None:
            raise ValueError(f"outline: missing locked input for episode {number}")
        value = _row(raw)
        _locked(value, "outline")
        _free_head_version(head, value, "outline")
        _digest(value)
        return value

    def read_episode(stage: str, number: int) -> dict[str, Any]:
        head = _free_head(heads, stage, number)
        try:
            resource_id = int(head["resource_ref"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{stage}: invalid resource head for episode {number}") from exc
        raw = conn.execute(
            "SELECT * FROM creative_episodes WHERE id = ? AND project_id = ? "
            "AND stage = ? AND episode_number = ? AND resource_scope = 'free'",
            (resource_id, project_id, stage, number),
        ).fetchone()
        if raw is None:
            raise ValueError(f"{stage}: missing locked input for episode {number}")
        value = _row(raw)
        _locked(value, stage)
        _free_head_version(head, value, stage)
        _digest(value)
        return value

    outlines = [
        inspection.check("outline", lambda n=number: read_outline(n), number=number)
        for number in numbers
    ]
    beats = [
        inspection.check(
            "beats", lambda n=number: read_episode("beats", n), number=number,
        )
        for number in numbers
    ]
    scripts = [
        inspection.check(
            "script", lambda n=number: read_episode("script", n), number=number,
        )
        for number in numbers
    ]
    if inspection.errors:
        return None

    head_order = [
        ("brief", 0), ("characters", 0), ("outline", 0),
        *(("beats", number) for number in numbers),
        *(("script", number) for number in numbers),
    ]
    package = {
        "schemaVersion": "1.0",
        "sourceSystem": "AiWorker",
        "project": project,
        "provenance": {
            "project_id": project_id,
            "project_revision": project.get("revision", 0),
            "episode_numbers": numbers,
            **_scope_provenance(project, numbers),
            "workflow_mode": "free",
            "locked_brief_id": brief["id"],
            "character_batch_id": heads[("characters", 0)]["resource_ref"],
            "outline_batch_id": heads[("outline", 0)]["resource_ref"],
            "resource_heads": [
                _free_head_provenance(heads[key]) for key in head_order
            ],
        },
        "brief": brief,
        "characters": characters,
        "relationships": relationships,
        "outlines": outlines,
        "beats": beats,
        "scripts": scripts,
    }
    package["version_manifest"] = _package_version_manifest(package)
    package["fingerprint"] = _digest(_fingerprint_payload(package))
    return package


def _inspect_snapshot(
    repository: PostgresCreativeRepository,
    project_id: int,
    episode_numbers: Iterable[int] | None = None,
    version_manifest: dict[str, Any] | None = None,
    *,
    connection: Connection | None = None,
) -> tuple[_Inspection, dict[str, Any] | None]:
    scope = (
        repository.read_snapshot_scope()
        if connection is None
        else nullcontext(connection)
    )
    with scope as raw_connection:
        conn = _ReadConnection(raw_connection)
        raw_project = conn.execute(
            "SELECT * FROM creative_projects WHERE id = ? AND deleted = FALSE",
            (project_id,),
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
        strategy = "custom" if version_manifest is not None else "current"
        if version_manifest is not None and not isinstance(version_manifest, dict):
            raise ValueError("version_manifest: must be an object")
        if str(project.get("workflow_mode") or "sequential") == "free":
            package = (
                _custom_free_snapshot(
                    conn, inspection, project, numbers, version_manifest or {},
                )
                if version_manifest is not None
                else _inspect_free_snapshot(conn, inspection, project, numbers)
            )
            video_parameters_ready = _attach_video_parameter_recommendation(
                conn, inspection, project, package,
            )
            _attach_version_selection(
                conn, inspection, project, package, numbers, strategy,
            )
            return inspection, package if video_parameters_ready else None
        if version_manifest is not None:
            package = _custom_sequential_snapshot(
                conn, inspection, project, numbers, version_manifest,
            )
            video_parameters_ready = _attach_video_parameter_recommendation(
                conn, inspection, project, package,
            )
            _attach_version_selection(
                conn, inspection, project, package, numbers, strategy,
            )
            return inspection, package if video_parameters_ready else None
        breakdown = None
        if _exports_breakdown(project):
            breakdown_row = conn.execute(
                "SELECT * FROM creative_breakdowns WHERE id = ? AND topic_id = ?",
                (project.get("breakdown_id"), project.get("topic_id")),
            ).fetchone()

            def read_breakdown():
                if breakdown_row is None:
                    raise ValueError("breakdown: missing locked input")
                value = _row(breakdown_row)
                _locked(value, "breakdown")
                _digest(value)
                return value

            breakdown = inspection.check("breakdown", read_breakdown)
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
                "episode_numbers": numbers, "workflow_mode": "sequential",
                **_scope_provenance(project, numbers),
                "locked_brief_id": brief["id"],
                "character_batch_id": project["character_batch_id"],
                "outline_batch_id": project["outline_batch_id"],
            },
            "brief": brief, **cast, "outlines": outlines, "beats": beats, "scripts": scripts,
        }
        if breakdown is not None:
            package["breakdown"] = breakdown
            package["provenance"].update({
                "breakdown_id": breakdown["id"],
                "breakdown_version": breakdown["version"],
            })
        package["version_manifest"] = _package_version_manifest(package)
        package["fingerprint"] = _digest(_fingerprint_payload(package))
        video_parameters_ready = _attach_video_parameter_recommendation(
            conn, inspection, project, package,
        )
        _attach_version_selection(
            conn, inspection, project, package, numbers, strategy,
        )
        return inspection, package if video_parameters_ready else None


def export_production_package(
    repository: PostgresCreativeRepository, project_id: int,
    episode_numbers: Iterable[int] | None = None,
    version_manifest: dict[str, Any] | None = None,
    *,
    connection: Connection | None = None,
) -> dict[str, Any]:
    """Return one validated snapshot without altering the PostgreSQL store."""
    inspection, package = _inspect_snapshot(
        repository, project_id, episode_numbers, version_manifest,
        connection=connection,
    )
    if inspection.errors:
        raise inspection.errors[0]
    return package


def preview_production_package(
    repository: PostgresCreativeRepository, project_id: int,
    version_manifest: dict[str, Any] | None = None,
    *,
    connection: Connection | None = None,
) -> dict[str, Any]:
    inspection, package = _inspect_snapshot(
        repository, project_id, version_manifest=version_manifest,
        connection=connection,
    )
    return inspection.preview(package)
