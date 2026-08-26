# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Freeze the current creative resources used by a complete-project review."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable


AUDIT_SCOPES = frozenset({"outline", "script", "project"})
_ACTIVE_STATUSES = frozenset({"draft", "returned", "submitted"})
_EXCLUDED_STATUSES = frozenset({"superseded", "historical"})
_SUMMARY_LIMIT = 200


def batch_size_for(total: int) -> int:
    """Return the worker batch size for an actual stage work-unit count."""
    if total < 1:
        return 0
    if total < 10:
        return 1
    if total <= 200:
        return 10
    return 20


def build_batches(episode_numbers: list[int]) -> list[list[int]]:
    """Normalize real episode numbers and split them using the dynamic policy."""
    numbers = sorted({_positive_int(number) for number in episode_numbers if _positive_int(number)})
    size = batch_size_for(len(numbers))
    return [numbers[index:index + size] for index in range(0, len(numbers), size)] if size else []


def build_project_review_source(
    project: dict[str, object], audit_scope: str
) -> dict[str, object]:
    """Freeze current active resources, manifest, mismatches, batches and fingerprint."""
    if audit_scope not in AUDIT_SCOPES:
        raise ValueError("audit_scope 必须是 outline、script 或 project")

    project_data = _mapping(project)
    project_id = _positive_int(project_data.get("id"))
    project_meta = {
        "id": project_id,
        "name": str(project_data.get("name") or ""),
        "revision": _as_int(project_data.get("revision")),
        "target_episode_count": _as_int(project_data.get("target_episode_count")),
    }

    brief = _select_brief(_mappings(project_data.get("blueprints")), project_data)
    characters, relationships, character_batch_id = _select_character_batch(project_data)
    all_outlines, outline_batch_id = _select_outlines(project_data)
    all_beats = _select_episode_resources(project_data, "beats")
    all_scripts = _select_episode_resources(project_data, "script")
    script_numbers = {int(item["episode_number"]) for item in all_scripts}
    if audit_scope == "outline":
        outlines, beats, scripts = all_outlines, [], []
    elif audit_scope == "script":
        outlines = [item for item in all_outlines if int(item["episode_number"]) in script_numbers]
        beats = [item for item in all_beats if int(item["episode_number"]) in script_numbers]
        scripts = all_scripts
    else:
        outlines, beats, scripts = all_outlines, all_beats, all_scripts

    outline_by_episode = {int(item["episode_number"]): item for item in outlines}
    beats_by_episode = {int(item["episode_number"]): item for item in beats}
    outline_units = [
        {"episode_number": int(resource["episode_number"]), "resource": resource}
        for resource in outlines
    ]
    script_units = [
        {
            "episode_number": int(resource["episode_number"]),
            "resource": resource,
            "outline": outline_by_episode.get(int(resource["episode_number"])),
            "beats": beats_by_episode.get(int(resource["episode_number"])),
        }
        for resource in scripts
    ]

    stages: dict[str, object] = {}
    if audit_scope in {"outline", "project"}:
        stages["outline"] = _stage_snapshot(
            "outline", outline_units, outline_batch_id
        )
    if audit_scope in {"script", "project"}:
        stages["script"] = _stage_snapshot("script", script_units, "")

    unreadable_resources = _unreadable_resources(
        [item for item in [brief] if item is not None]
        + characters + relationships + outlines + beats + scripts
    )
    source: dict[str, object] = {
        "project": project_meta,
        "project_id": project_id,
        "project_name": project_meta["name"],
        "audit_scope": audit_scope,
        "brief": brief,
        "characters": {
            "batch_id": character_batch_id,
            "items": characters,
            "relationships": relationships,
        },
        "relationships": relationships,
        "outline_batch_id": outline_batch_id,
        "stages": stages,
        "upstream_mismatches": _upstream_mismatches(
            brief=brief,
            characters=characters,
            relationships=relationships,
            character_batch_id=character_batch_id,
            outline_batch_id=outline_batch_id,
            outlines=outlines,
            beats=beats,
            scripts=scripts,
        ),
        "unreadable_resources": unreadable_resources,
    }
    source["manifest"] = _build_manifest(
        source,
        all_outlines=outlines,
        all_beats=beats,
        all_scripts=scripts,
    )
    source["fingerprint"] = project_review_fingerprint(source)
    return source


def project_review_fingerprint(source: dict[str, object]) -> str:
    """Hash canonical JSON while excluding the existing fingerprint field."""
    canonical = json.dumps(
        {key: value for key, value in source.items() if key != "fingerprint"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _select_brief(items: list[dict[str, Any]], project: dict[str, Any]) -> dict[str, Any] | None:
    active_items = [item for item in _eligible(items) if _status(item) in _ACTIVE_STATUSES]
    if active_items:
        return _resource_snapshot(max(active_items, key=_version_key), "brief", _positive_int(project.get("id")))

    locked_id = _positive_int(project.get("locked_brief_id"))
    locked = next((item for item in _eligible(items) if _positive_int(item.get("id")) == locked_id), None)
    if locked is not None:
        return _resource_snapshot(locked, "brief", _positive_int(project.get("id")))
    current = _current(items)
    return _resource_snapshot(current, "brief", _positive_int(project.get("id"))) if current else None


def _select_character_batch(project: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    characters = _mappings(project.get("characters"))
    relationships = _mappings(project.get("relationships"))
    selected_batch = _select_active_batch([*characters, *relationships], project.get("character_batch_id"))
    if not selected_batch:
        return [], [], ""
    project_id = _positive_int(project.get("id"))
    return (
        [
            _resource_snapshot(item, "character", project_id)
            for item in _sorted_resources(characters, selected_batch)
        ],
        [
            _resource_snapshot(item, "relationship", project_id)
            for item in _sorted_resources(relationships, selected_batch)
        ],
        selected_batch,
    )


def _select_outlines(project: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    episodes = _mappings(project.get("episodes"))
    outlines = [item for item in episodes if str(item.get("stage") or "") == "outline"]
    batch_id = _select_active_batch(outlines, project.get("outline_batch_id"))
    if not batch_id:
        return [], ""
    selected: list[dict[str, Any]] = []
    for episode_number in _episode_numbers(outlines, batch_id=batch_id):
        item = _current([
            row for row in outlines
            if str(row.get("batch_id") or "") == batch_id
            and _positive_int(row.get("episode_number")) == episode_number
        ])
        if item is not None:
            selected.append(_resource_snapshot(item, "outline", _positive_int(project.get("id"))))
    return selected, batch_id


def _select_episode_resources(project: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    rows = [
        item for item in _mappings(project.get("episodes"))
        if str(item.get("stage") or "") == stage
    ]
    selected: list[dict[str, Any]] = []
    for episode_number in _episode_numbers(rows):
        item = _current([
            row for row in rows if _positive_int(row.get("episode_number")) == episode_number
        ])
        if item is not None:
            selected.append(_resource_snapshot(item, stage, _positive_int(project.get("id"))))
    return selected


def _select_active_batch(rows: list[dict[str, Any]], fallback: object) -> str:
    candidates: dict[str, list[dict[str, Any]]] = {}
    for item in _eligible(rows):
        batch_id = str(item.get("batch_id") or "").strip()
        if batch_id:
            candidates.setdefault(batch_id, []).append(item)
    active = [
        (max(active_items, key=_version_key), batch_id)
        for batch_id, items in candidates.items()
        if (active_items := [item for item in items if _status(item) in _ACTIVE_STATUSES])
    ]
    if active:
        return max(active, key=lambda pair: (*_version_key(pair[0]), pair[1]))[1]
    fallback_id = str(fallback or "").strip()
    if fallback_id in candidates:
        return fallback_id
    return ""


def _stage_snapshot(stage_kind: str, units: list[dict[str, Any]], batch_id: str) -> dict[str, object]:
    episode_numbers = [int(unit["episode_number"]) for unit in units]
    return {
        "stage_kind": stage_kind,
        "batch_id": batch_id,
        "episode_numbers": episode_numbers,
        "batch_size": batch_size_for(len(episode_numbers)),
        "batches": build_batches(episode_numbers),
        "units": units,
    }


def _build_manifest(
    source: dict[str, object],
    *,
    all_outlines: list[dict[str, Any]],
    all_beats: list[dict[str, Any]],
    all_scripts: list[dict[str, Any]],
) -> dict[str, object]:
    source_stages = _mapping(source.get("stages"))
    manifest_stages = {
        name: _manifest_stage(_mapping(stage))
        for name, stage in source_stages.items()
    }
    characters = _mapping(source.get("characters"))
    character_items = _mappings(characters.get("items"))
    relationships = _mappings(characters.get("relationships"))
    outline_numbers = [int(item["episode_number"]) for item in all_outlines]
    return {
        "project": deepcopy(_mapping(source.get("project"))),
        "project_id": _positive_int(source.get("project_id")),
        "project_name": str(source.get("project_name") or ""),
        "audit_scope": str(source.get("audit_scope") or ""),
        "brief": _manifest_resource(_mapping(source.get("brief"))) if source.get("brief") else None,
        "characters": {
            "batch_id": str(characters.get("batch_id") or ""),
            "character_count": len(character_items),
            "relationship_count": len(relationships),
            "content_chars": _resources_chars([*character_items, *relationships]),
            "items": [_manifest_resource(item) for item in character_items],
            "relationships": [_manifest_resource(item) for item in relationships],
        },
        "outline_count": len(all_outlines),
        "script_count": len(all_scripts),
        "outline_episode_numbers": outline_numbers,
        "script_episode_numbers": [int(item["episode_number"]) for item in all_scripts],
        "missing_episode_numbers": _missing_episode_numbers(outline_numbers),
        "stages": manifest_stages,
        "context": {
            "beats_count": len(all_beats),
            "beats": [_manifest_resource(item) for item in all_beats],
        },
        "unreadable_resources": [
            deepcopy(item) for item in _mappings(source.get("unreadable_resources"))
        ],
        "upstream_mismatches": [
            deepcopy(item) for item in _mappings(source.get("upstream_mismatches"))
        ],
        "excluded_statuses": sorted(_EXCLUDED_STATUSES),
    }


def _manifest_stage(stage: dict[str, Any]) -> dict[str, object]:
    units = []
    for unit in _mappings(stage.get("units")):
        rendered = {
            "episode_number": _positive_int(unit.get("episode_number")),
            "resource": _manifest_resource(_mapping(unit.get("resource"))),
        }
        for name in ("outline", "beats"):
            if unit.get(name) is not None:
                rendered[name] = _manifest_resource(_mapping(unit.get(name)))
        units.append(rendered)
    return {
        "stage_kind": str(stage.get("stage_kind") or ""),
        "batch_id": str(stage.get("batch_id") or ""),
        "episode_numbers": list(stage.get("episode_numbers") or []),
        "batch_size": _as_int(stage.get("batch_size")),
        "batches": deepcopy(list(stage.get("batches") or [])),
        "unit_count": len(units),
        "content_chars": sum(_as_int(unit["resource"].get("content_chars")) for unit in units),
        "units": units,
    }


def _manifest_resource(resource: dict[str, Any]) -> dict[str, object]:
    return {
        "id": _positive_int(resource.get("id")),
        "stage_kind": str(resource.get("stage_kind") or ""),
        "episode_number": _positive_int(resource.get("episode_number")),
        "batch_id": str(resource.get("batch_id") or ""),
        "version": _as_int(resource.get("version")),
        "status": str(resource.get("status") or ""),
        "content_fingerprint": str(resource.get("content_fingerprint") or ""),
        "content_chars": _as_int(resource.get("content_chars")),
        "summary": _summary(resource.get("content")),
        "source_url": str(resource.get("source_url") or ""),
    }


def _resource_snapshot(item: dict[str, Any], stage_kind: str, project_id: int) -> dict[str, Any]:
    content = deepcopy(item.get("content"))
    return {
        "id": _positive_int(item.get("id")),
        "stage_kind": stage_kind,
        "episode_number": _positive_int(item.get("episode_number")),
        "batch_id": str(item.get("batch_id") or ""),
        "version": _as_int(item.get("version")),
        "status": _status(item),
        "content": content,
        "upstream_refs": deepcopy(_mapping(item.get("upstream_refs"))),
        "content_fingerprint": _content_fingerprint(content),
        "content_chars": _content_chars(content),
        "source_url": _source_url(project_id, stage_kind, _positive_int(item.get("episode_number"))),
    }


def _upstream_mismatches(
    *,
    brief: dict[str, Any] | None,
    characters: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    character_batch_id: str,
    outline_batch_id: str,
    outlines: list[dict[str, Any]],
    beats: list[dict[str, Any]],
    scripts: list[dict[str, Any]],
) -> list[dict[str, object]]:
    outline_by_episode = {int(item["episode_number"]): item for item in outlines}
    beats_by_episode = {int(item["episode_number"]): item for item in beats}
    identity_refs = {
        "brief_id": _positive_int(brief.get("id")) if brief else 0,
        "character_batch_id": character_batch_id,
    }
    generation_refs = {
        **identity_refs,
        "brief_version": _as_int(brief.get("version")) if brief else 0,
        "brief_content_fingerprint": str(brief.get("content_fingerprint") or "") if brief else "",
        "character_content_fingerprint": _character_content_fingerprint(characters, relationships),
    }
    entries: list[dict[str, object]] = []
    for stage_kind, resources in (("outline", outlines), ("beats", beats), ("script", scripts)):
        for resource in resources:
            refs = _mapping(resource.get("upstream_refs"))
            expected: dict[str, object] = dict(
                identity_refs if stage_kind == "outline" else generation_refs
            )
            if stage_kind in {"beats", "script"}:
                expected["outline_batch_id"] = outline_batch_id
            outline = outline_by_episode.get(int(resource["episode_number"]))
            if stage_kind in {"beats", "script"} and outline is not None:
                expected.update({
                    "outline_id": outline["id"],
                    "outline_version": outline["version"],
                    "outline_content_fingerprint": outline["content_fingerprint"],
                })
            beats_resource = beats_by_episode.get(int(resource["episode_number"]))
            if stage_kind == "script" and beats_resource is not None:
                expected.update({
                    "beats_id": beats_resource["id"],
                    "beats_version": beats_resource["version"],
                    "beats_content_fingerprint": beats_resource["content_fingerprint"],
                })
            mismatches = [
                {
                    "field": key,
                    "expected": expected_value,
                    "actual": refs.get(key),
                }
                for key, expected_value in expected.items()
                if not _reference_matches(key, refs.get(key), expected_value, outline)
            ]
            if mismatches:
                entries.append({
                    "stage_kind": stage_kind,
                    "episode_number": int(resource["episode_number"]),
                    "resource_id": int(resource["id"]),
                    "version": int(resource["version"]),
                    "mismatches": mismatches,
                })
    return entries


def _unreadable_resources(resources: Iterable[dict[str, Any]]) -> list[dict[str, object]]:
    return [
        {
            "stage_kind": str(resource.get("stage_kind") or ""),
            "episode_number": _positive_int(resource.get("episode_number")),
            "resource_id": _positive_int(resource.get("id")),
            "version": _as_int(resource.get("version")),
            "reason": "empty_content",
            "source_url": str(resource.get("source_url") or ""),
        }
        for resource in resources
        if _empty_content(resource.get("content"))
    ]


def _current(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = _eligible(items)
    active = [item for item in candidates if _status(item) in _ACTIVE_STATUSES]
    return max(active or candidates, key=_version_key) if (active or candidates) else None


def _eligible(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if _status(item) not in _EXCLUDED_STATUSES]


def _sorted_resources(rows: list[dict[str, Any]], batch_id: str) -> list[dict[str, Any]]:
    return sorted(
        [
            item for item in _eligible(rows)
            if str(item.get("batch_id") or "") == batch_id
        ],
        key=lambda item: (_as_int(item.get("version")), _positive_int(item.get("id"))),
    )


def _episode_numbers(rows: list[dict[str, Any]], *, batch_id: str | None = None) -> list[int]:
    return sorted({
        _positive_int(item.get("episode_number"))
        for item in _eligible(rows)
        if _positive_int(item.get("episode_number"))
        and (batch_id is None or str(item.get("batch_id") or "") == batch_id)
    })


def _missing_episode_numbers(numbers: list[int]) -> list[int]:
    if len(numbers) < 2:
        return []
    return [
        number for number in range(min(numbers), max(numbers) + 1) if number not in set(numbers)
    ]


def _source_url(project_id: int, stage_kind: str, episode_number: int) -> str:
    if stage_kind == "brief":
        return f"/planning/projects/{project_id}/brief"
    if stage_kind in {"character", "relationship"}:
        return f"/planning/projects/{project_id}/characters"
    if stage_kind == "outline":
        return f"/writing/projects/{project_id}/outlines"
    if stage_kind == "beats":
        return f"/writing/projects/{project_id}/episodes/{episode_number}/beats"
    return f"/writing/projects/{project_id}/episodes/{episode_number}/{stage_kind}s"


def _content_fingerprint(content: object) -> str:
    return hashlib.sha256(_reference_canonical_content(content).encode("utf-8")).hexdigest()


def _outline_content_fingerprint(content: object) -> str:
    normalized = dict(content) if isinstance(content, dict) else content
    if isinstance(normalized, dict):
        normalized.pop("series_overview", None)
    return _content_fingerprint(normalized)


def _character_content_fingerprint(
    characters: list[dict[str, Any]], relationships: list[dict[str, Any]]
) -> str:
    return _content_fingerprint({
        "characters": [
            {"id": item["id"], "version": item.get("version"), "content": item.get("content") or {}}
            for item in sorted(characters, key=lambda item: _positive_int(item.get("id")))
        ],
        "relationships": [
            {"id": item["id"], "version": item.get("version"), "content": item.get("content") or {}}
            for item in sorted(relationships, key=lambda item: _positive_int(item.get("id")))
        ],
    })


def _content_chars(content: object) -> int:
    return len(_canonical_content(content))


def _canonical_content(content: object) -> str:
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def _reference_canonical_content(content: object) -> str:
    return json.dumps(content or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _summary(content: object) -> str:
    return _canonical_content(content)[:_SUMMARY_LIMIT]


def _resources_chars(resources: Iterable[dict[str, Any]]) -> int:
    return sum(_as_int(resource.get("content_chars")) for resource in resources)


def _empty_content(content: object) -> bool:
    return content is None or content == "" or content == {} or content == []


def _same_ref(actual: object, expected: object) -> bool:
    return str(actual or "") == str(expected or "")


def _reference_matches(
    key: str, actual: object, expected: object, outline: dict[str, Any] | None
) -> bool:
    if key != "outline_content_fingerprint":
        return _same_ref(actual, expected)
    if outline is None:
        return not actual
    saved = str(actual or "")
    return bool(saved) and saved in {
        _content_fingerprint(outline.get("content")),
        _outline_content_fingerprint(outline.get("content")),
    }


def _version_key(item: dict[str, Any]) -> tuple[int, int]:
    return _as_int(item.get("version")), _positive_int(item.get("id"))


def _status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "").strip().lower()


def _positive_int(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else _as_int(value) if _as_int(value) > 0 else 0


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _mappings(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
