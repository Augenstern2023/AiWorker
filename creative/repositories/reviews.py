# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Script reviews, project long reviews and script optimizations.

This mixin is the PostgreSQL port of the review half of the frozen SQLite
repository. It assumes only the ``CreativeRepositoryBase`` contract, so the
coordinating repository can compose it without the mixin knowing how
connections are obtained.

Type changes applied here, and nowhere else in the read/write path:

* legacy epoch ``REAL`` timestamps are ``TIMESTAMPTZ``. Reads flow through
  :meth:`CreativeRepositoryBase.row_dict`, which turns every ``datetime`` back
  into the epoch seconds callers already expect, and writes use
  :func:`creative.repositories.base.utc_now`. No legacy epoch value is ever
  written on this surface, so ``from_epoch_seconds`` is not needed.
* legacy ``*_json TEXT`` columns are ``JSONB``. Writes pass Python ``dict`` /
  ``list`` objects straight through (never ``json.dumps``) and reads pop the
  ``_json`` key and assign the already-decoded value, keeping the legacy empty
  defaults (``[]`` for lists, ``{}`` for objects). :func:`jsonb_copy` keeps
  stored state from aliasing a caller's object.
* legacy 0/1 ``INTEGER`` flags are ``BOOLEAN``; this domain has none.
* the legacy raised plain ``ValueError`` for every illegal review/optimization
  transition, and still does: no review-specific exception class exists in
  :mod:`creative.legacy_sqlite_repository` to import, so the identical Chinese
  messages are raised as ``ValueError`` exactly as before.

Idempotency and lock order
--------------------------

``create_project_review_once`` keeps the legacy deterministic request key:
``source_payload["request_key"]`` identifies one confirmation request, and a
duplicate must return the row already stored instead of creating a second one.

The PostgreSQL schema carries no unique index over that key (it lives inside a
JSONB payload), so uniqueness is enforced with a transaction-scoped PostgreSQL
advisory lock instead of ``ON CONFLICT``. The key is derived from the creative
store ID plus the local project ID::

    pg_advisory_xact_lock(int8 of sha256("{store_id}:project:{project_id}")[:8])

**Lock order** (the only order this domain may be called in): the per-project
creative advisory lock is taken *first*, then ``script_reviews`` rows are
locked. This matches the plan-wide order "creative project advisory lock →
creative resource row → workflow row → audit insert"; no caller may lock a
``script_reviews`` row and then ask for this advisory key, and no other domain
may take it in the opposite order. One lock is held per transaction, so this
domain cannot deadlock against itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.engine import Connection

from creative.database_schema import (
    script_optimization_drafts,
    script_optimizations,
    script_reviews,
)
from creative.repositories.base import jsonb_copy, utc_now
from creative.script_service import normalize_script_content


def _review_request_key(row: Mapping[str, Any]) -> str:
    """Return the idempotency key stored inside ``source_payload``.

    The legacy helper read the key out of the raw ``source_payload_json`` text.
    Here the JSONB column has already been decoded by ``row_dict``/``_script_
    review``, so the payload arrives as a ``dict``; a JSON *string* is still
    accepted so the helper keeps working for legacy-shaped input. Unreadable
    payloads yield ``""`` exactly like the legacy parse failure.
    """

    payload: Any = row.get("source_payload")
    if payload is None:
        payload = row.get("source_payload_json")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, ValueError):
            return ""
    if not isinstance(payload, Mapping):
        return ""
    return str(payload.get("request_key") or "")


def _is_project_long_review(row: Mapping[str, Any]) -> bool:
    """Report whether a review row is a full-project (long) review."""

    payload: Any = row.get("source_payload")
    if payload is None:
        payload = row.get("source_payload_json")
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload or "{}")
        except (TypeError, ValueError):
            return False
    if not isinstance(payload, Mapping):
        return False
    return bool(payload.get("audit_scope"))


class ReviewsRepository:
    """Reviews, project long reviews, optimizations and optimization drafts."""

    # -- script reviews -----------------------------------------------------

    def create_script_review(
        self,
        *,
        source_type: str,
        mode: str,
        project_id: int | None,
        episode_numbers: list[int],
        source_fingerprint: str,
        source_payload: dict[str, Any],
        created_by: str,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction_scope() as connection:
            review_id = connection.execute(
                insert(script_reviews)
                .values(
                    project_id=project_id,
                    source_type=source_type,
                    mode=mode,
                    episode_numbers_json=jsonb_copy(episode_numbers),
                    source_fingerprint=source_fingerprint,
                    source_payload_json=jsonb_copy(source_payload),
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
                .returning(script_reviews.c.id)
            ).scalar_one()
            row = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one_or_none()
        return self._script_review(self.row_dict(row)) if row is not None else {}

    def create_project_review_once(
        self,
        *,
        project_id: int,
        episode_numbers: list[int],
        source_fingerprint: str,
        source_payload: dict[str, Any],
        created_by: str,
        request_key: str,
    ) -> tuple[dict[str, Any], bool]:
        """Create the project long review for one confirmation request, once.

        The per-project advisory lock is taken before any ``script_reviews`` row
        is read, so two concurrent callers serialize here and the loser re-reads
        the winner's row instead of inserting a duplicate. A duplicate never
        raises; only a reused key pointing at a different snapshot does.
        """

        if not str(request_key).strip():
            raise ValueError("确认请求缺少幂等键")
        if not source_payload.get("audit_scope"):
            raise ValueError("完整项目审核缺少审核范围")
        payload = dict(source_payload)
        payload["request_key"] = str(request_key)
        now = utc_now()
        with self.transaction_scope() as connection:
            self._lock_project_reviews(connection, int(project_id))
            candidates = connection.execute(
                select(script_reviews)
                .where(
                    script_reviews.c.created_by == created_by,
                    script_reviews.c.project_id == int(project_id),
                )
                .order_by(script_reviews.c.id.asc())
            ).mappings().all()
            for candidate in candidates:
                existing = self._script_review(self.row_dict(candidate))
                if _review_request_key(existing) != str(request_key):
                    continue
                if str(existing["source_fingerprint"]) != str(source_fingerprint):
                    raise ValueError("确认请求已用于不同项目快照")
                return existing, False
            review_id = connection.execute(
                insert(script_reviews)
                .values(
                    project_id=int(project_id),
                    source_type="project",
                    mode="collection",
                    episode_numbers_json=jsonb_copy(episode_numbers),
                    source_fingerprint=source_fingerprint,
                    source_payload_json=payload,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
                .returning(script_reviews.c.id)
            ).scalar_one()
            created = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one_or_none()
        return (
            self._script_review(self.row_dict(created)) if created is not None else {},
            True,
        )

    def get_script_review(self, review_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one_or_none()
        return self._script_review(self.row_dict(row)) if row is not None else None

    def list_script_reviews(
        self,
        *,
        created_by: str | None = None,
        project_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        statement = select(script_reviews)
        if created_by is not None:
            statement = statement.where(script_reviews.c.created_by == created_by)
        if project_ids is not None:
            if project_ids:
                statement = statement.where(
                    script_reviews.c.project_id.in_(sorted(int(item) for item in project_ids))
                )
            else:
                statement = statement.where(script_reviews.c.project_id.is_(None))
        # Explicit total order: identity order is never guaranteed by PostgreSQL.
        statement = statement.order_by(
            script_reviews.c.created_at.desc(), script_reviews.c.id.desc()
        )
        with self.connection_scope() as connection:
            rows = connection.execute(statement).mappings().all()
        return [self._script_review(self.row_dict(row)) for row in rows]

    def delete_script_review(self, review_id: int) -> None:
        """Remove one review and its review-scoped optimization drafts."""

        with self.transaction_scope() as connection:
            exists = connection.execute(
                select(script_reviews.c.id).where(
                    script_reviews.c.id == int(review_id)
                )
            ).scalar_one_or_none()
            if exists is None:
                raise ValueError("审核记录不存在")
            optimization_ids = [
                int(item)
                for item in connection.execute(
                    select(script_optimizations.c.id)
                    .where(script_optimizations.c.review_id == int(review_id))
                    .order_by(script_optimizations.c.id.asc())
                ).scalars()
            ]
            if optimization_ids:
                connection.execute(
                    delete(script_optimization_drafts).where(
                        script_optimization_drafts.c.optimization_id.in_(optimization_ids)
                    )
                )
            connection.execute(
                delete(script_optimizations).where(
                    script_optimizations.c.review_id == int(review_id)
                )
            )
            connection.execute(
                delete(script_reviews).where(script_reviews.c.id == int(review_id))
            )

    def set_script_review_job(self, review_id: int, job_id: str) -> dict[str, Any]:
        return self._update_script_review(review_id, {"job_id": job_id})

    def start_script_review(self, review_id: int) -> dict[str, Any]:
        return self._update_script_review(review_id, {"status": "running", "error": ""})

    def complete_script_review(
        self, review_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        return self._update_script_review(
            review_id,
            {
                "status": "pending_confirmation",
                "ai_verdict": str(report["ai_verdict"]),
                "report_json": jsonb_copy(report),
                "error": "",
            },
        )

    def complete_project_review(
        self, review_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Finish a full-project review without a human confirmation step."""

        with self.transaction_scope() as connection:
            row = connection.execute(
                select(script_reviews)
                .where(script_reviews.c.id == int(review_id))
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("审核记录不存在")
            review = self.row_dict(row)
            if not _is_project_long_review(review):
                raise ValueError("不是完整项目审核记录")
            connection.execute(
                update(script_reviews)
                .where(script_reviews.c.id == int(review_id))
                .values(
                    status="completed",
                    ai_verdict=str(report.get("ai_verdict") or ""),
                    report_json=jsonb_copy(report),
                    error="",
                    updated_at=utc_now(),
                )
            )
            completed = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one_or_none()
        return self._script_review(self.row_dict(completed)) if completed is not None else {}

    def prepare_project_review_resume(self, review_id: int) -> dict[str, Any]:
        """Re-queue a failed full-project review, or replay a live one."""

        with self.transaction_scope() as connection:
            row = connection.execute(
                select(script_reviews)
                .where(script_reviews.c.id == int(review_id))
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("审核记录不存在")
            review = self.row_dict(row)
            if not _is_project_long_review(review):
                raise ValueError("不是完整项目审核记录")
            status = str(review["status"])
            if status in {"queued", "running"}:
                return self._script_review(review)
            if status == "completed":
                raise ValueError("完整项目审核已完成，不能恢复")
            if status != "failed":
                raise ValueError("完整项目审核当前不能恢复")
            # Compare-and-swap: only a failed row may be re-queued.
            resumed_id = connection.execute(
                update(script_reviews)
                .where(
                    script_reviews.c.id == int(review_id),
                    script_reviews.c.status == "failed",
                )
                .values(status="queued", error="", updated_at=utc_now())
                .returning(script_reviews.c.id)
            ).scalar_one_or_none()
            resumed = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one_or_none()
            if resumed_id is None and resumed is None:
                raise ValueError("审核记录不存在")
        return self._script_review(self.row_dict(resumed)) if resumed is not None else {}

    def prepare_script_review_resume(self, review_id: int) -> dict[str, Any]:
        """Requeue a failed formal review while preserving its checkpoint."""

        with self.transaction_scope() as connection:
            row = connection.execute(
                select(script_reviews)
                .where(script_reviews.c.id == int(review_id))
                .with_for_update()
            ).mappings().one_or_none()
            if row is None:
                raise ValueError("审核记录不存在")
            review = self.row_dict(row)
            if _is_project_long_review(review):
                raise ValueError("完整项目审核必须从项目审核入口恢复")
            status = str(review["status"])
            if status in {"queued", "running"}:
                return self._script_review(review)
            if status != "failed":
                raise ValueError("只有失败的剧本审核可以继续")
            connection.execute(
                update(script_reviews)
                .where(
                    script_reviews.c.id == int(review_id),
                    script_reviews.c.status == "failed",
                )
                .values(status="queued", error="", updated_at=utc_now())
            )
            resumed = connection.execute(
                select(script_reviews).where(script_reviews.c.id == int(review_id))
            ).mappings().one()
        return self._script_review(self.row_dict(resumed))

    def checkpoint_script_review(
        self, review_id: int, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist a resumable, non-final report while a review is still running."""

        return self._update_script_review(
            review_id,
            {
                "status": "running",
                "report_json": jsonb_copy(report),
                "error": "",
            },
        )

    def fail_script_review(self, review_id: int, error: str) -> dict[str, Any]:
        return self._update_script_review(
            review_id, {"status": "failed", "error": str(error)[:2000]}
        )

    def cancel_script_review(self, review_id: int, reason: str = "") -> dict[str, Any]:
        return self._update_script_review(
            review_id,
            {
                "status": "cancelled",
                "error": str(reason or "审核任务已取消")[:2000],
            },
        )

    def confirm_script_review(
        self,
        review_id: int,
        final_verdict: str,
        confirmation_note: str,
        confirmed_by: str,
        current_fingerprint: str,
    ) -> dict[str, Any]:
        if final_verdict not in {"通过", "建议修改"}:
            raise ValueError("最终结论必须是通过或建议修改")
        review = self.get_script_review(review_id)
        if not review:
            raise ValueError("审核记录不存在")
        if review["source_fingerprint"] != current_fingerprint:
            self.mark_script_review_stale_if_changed(review_id, current_fingerprint)
            raise ValueError("剧本已更新，请重新审核后再确认")
        if review["status"] != "pending_confirmation":
            raise ValueError("该审核记录当前不能确认")
        # Conditional transition: only a pending_confirmation row may become
        # confirmed, so a concurrent double confirmation cannot both win.
        with self.transaction_scope() as connection:
            confirmed_id = connection.execute(
                update(script_reviews)
                .where(
                    script_reviews.c.id == int(review_id),
                    script_reviews.c.status == "pending_confirmation",
                )
                .values(
                    status="confirmed",
                    final_verdict=final_verdict,
                    confirmation_note=confirmation_note.strip(),
                    confirmed_by=confirmed_by,
                    confirmed_at=utc_now(),
                    updated_at=utc_now(),
                )
                .returning(script_reviews.c.id)
            ).scalar_one_or_none()
            if confirmed_id is None:
                raise ValueError("该审核记录当前不能确认")
        return self.get_script_review(review_id) or {}

    def mark_script_review_stale_if_changed(
        self, review_id: int, current_fingerprint: str
    ) -> dict[str, Any]:
        """Keep an unconfirmed project review from being presented as current."""

        review = self.get_script_review(review_id)
        if not review:
            raise ValueError("审核记录不存在")
        if (
            review["source_type"] != "project"
            or review["status"] != "pending_confirmation"
            or review["source_fingerprint"] == current_fingerprint
        ):
            return review
        return self._update_script_review(
            review_id,
            {"status": "stale", "error": "剧本已更新，请重新审核后再确认"},
        )

    def _update_script_review(
        self, review_id: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        # ``RETURNING`` keeps this to one statement. The legacy update was
        # unguarded, so a missing row still yields ``{}`` rather than an error.
        with self.transaction_scope() as connection:
            updated = connection.execute(
                update(script_reviews)
                .where(script_reviews.c.id == int(review_id))
                .values(**values, updated_at=utc_now())
                .returning(script_reviews)
            ).mappings().one_or_none()
        return self._script_review(self.row_dict(updated)) if updated is not None else {}

    # -- script optimizations -----------------------------------------------

    def create_script_optimization(
        self,
        *,
        review_id: int,
        project_id: int,
        mode: str,
        source_fingerprint: str,
        selected_findings: list[dict[str, Any]],
        target_episode_numbers: list[int],
        created_by: str,
        prompt_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.transaction_scope() as connection:
            optimization_id = connection.execute(
                insert(script_optimizations)
                .values(
                    review_id=int(review_id),
                    project_id=int(project_id),
                    mode=mode,
                    source_fingerprint=source_fingerprint,
                    selected_findings_json=jsonb_copy(selected_findings),
                    target_episode_numbers_json=jsonb_copy(target_episode_numbers),
                    prompt_snapshot_json=jsonb_copy(prompt_snapshot or {}),
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
                .returning(script_optimizations.c.id)
            ).scalar_one()
        # ``get_script_optimization`` adds the (empty) ``drafts`` list, matching
        # the legacy return shape callers already rely on.
        return self.get_script_optimization(int(optimization_id)) or {}

    def get_script_optimization(self, optimization_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(script_optimizations).where(
                    script_optimizations.c.id == int(optimization_id)
                )
            ).mappings().one_or_none()
            if row is None:
                return None
            result = self._script_optimization(self.row_dict(row))
            drafts = connection.execute(
                select(script_optimization_drafts)
                .where(
                    script_optimization_drafts.c.optimization_id
                    == int(optimization_id)
                )
                .order_by(
                    script_optimization_drafts.c.episode_number.asc(),
                    script_optimization_drafts.c.kind.asc(),
                    script_optimization_drafts.c.id.asc(),
                )
            ).mappings().all()
        result["drafts"] = [
            self._script_optimization_draft(self.row_dict(item)) for item in drafts
        ]
        return result

    def list_script_optimizations(self, review_id: int) -> list[dict[str, Any]]:
        with self.connection_scope() as connection:
            optimization_ids = [
                int(item)
                for item in connection.execute(
                    select(script_optimizations.c.id)
                    .where(script_optimizations.c.review_id == int(review_id))
                    .order_by(
                        script_optimizations.c.created_at.desc(),
                        script_optimizations.c.id.desc(),
                    )
                ).scalars()
            ]
        return [
            self.get_script_optimization(optimization_id) or {}
            for optimization_id in optimization_ids
        ]

    def start_script_optimization(self, optimization_id: int) -> dict[str, Any]:
        return self._update_script_optimization(
            optimization_id, {"status": "running", "error": ""}
        )

    def set_script_optimization_job(
        self, optimization_id: int, job_id: str
    ) -> dict[str, Any]:
        return self._update_script_optimization(optimization_id, {"job_id": job_id})

    def prepare_script_optimization_resume(
        self, optimization_id: int
    ) -> dict[str, Any]:
        """Claim one failed task without removing completed episode drafts."""

        with self.transaction_scope() as connection:
            updated = connection.execute(
                update(script_optimizations)
                .where(
                    script_optimizations.c.id == int(optimization_id),
                    script_optimizations.c.status == "failed",
                )
                .values(status="queued", error="", updated_at=utc_now())
                .returning(script_optimizations.c.id)
            ).scalar_one_or_none()
            if updated is None:
                raise ValueError("只有失败的优化任务可以继续生成")
        return self.get_script_optimization(optimization_id) or {}

    def save_script_optimization_episode(
        self,
        optimization_id: int,
        episode_number: int,
        drafts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Commit a whole episode before the next model request starts."""

        now = utc_now()
        with self.transaction_scope() as connection:
            row = connection.execute(
                select(script_optimizations)
                .where(script_optimizations.c.id == int(optimization_id))
                .with_for_update()
            ).mappings().one_or_none()
            if row is None or row["status"] != "running":
                raise ValueError("优化任务未运行，无法保存草稿")
            targets = row["target_episode_numbers_json"] or []
            if int(episode_number) not in targets:
                raise ValueError("优化草稿不在任务目标集数中")
            expected = {"beats", "script"} if row["mode"] == "structure" else {"script"}
            if len(drafts) != len(expected) or {
                str(draft.get("kind") or "") for draft in drafts
            } != expected or any(
                int(draft.get("episode_number") or 0) != int(episode_number)
                for draft in drafts
            ):
                raise ValueError("当前集优化草稿不完整")
            existing = connection.execute(
                select(script_optimization_drafts.c.kind).where(
                    script_optimization_drafts.c.optimization_id
                    == int(optimization_id),
                    script_optimization_drafts.c.episode_number
                    == int(episode_number),
                )
            ).scalars().all()
            if existing:
                if len(existing) != len(expected) or set(existing) != expected:
                    raise ValueError("当前集已有不完整的优化草稿")
            else:
                inserted: dict[str, int] = {}
                for draft in drafts:
                    kind = str(draft["kind"])
                    draft_id = connection.execute(
                        insert(script_optimization_drafts)
                        .values(
                            optimization_id=int(optimization_id),
                            project_id=int(row["project_id"]),
                            episode_number=int(episode_number),
                            kind=kind,
                            source_episode_id=draft.get("source_episode_id"),
                            source_fingerprint=str(
                                draft.get("source_fingerprint") or ""
                            ),
                            content_json=jsonb_copy(draft.get("content") or {}),
                            peer_draft_id=None,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(script_optimization_drafts.c.id)
                    ).scalar_one()
                    inserted[kind] = int(draft_id)
                if row["mode"] == "structure":
                    connection.execute(
                        update(script_optimization_drafts)
                        .where(script_optimization_drafts.c.id == inserted["beats"])
                        .values(peer_draft_id=inserted["script"])
                    )
                    connection.execute(
                        update(script_optimization_drafts)
                        .where(script_optimization_drafts.c.id == inserted["script"])
                        .values(peer_draft_id=inserted["beats"])
                    )
                connection.execute(
                    update(script_optimizations)
                    .where(script_optimizations.c.id == int(optimization_id))
                    .values(updated_at=now)
                )
        return self.get_script_optimization(optimization_id) or {}

    def finish_script_optimization(self, optimization_id: int) -> dict[str, Any]:
        """Mark complete only after every target episode is checkpointed."""

        with self.transaction_scope() as connection:
            row = connection.execute(
                select(script_optimizations)
                .where(script_optimizations.c.id == int(optimization_id))
                .with_for_update()
            ).mappings().one_or_none()
            if row is None or row["status"] != "running":
                raise ValueError("优化任务未运行")
            expected = {"beats", "script"} if row["mode"] == "structure" else {"script"}
            for number in row["target_episode_numbers_json"] or []:
                kinds = connection.execute(
                    select(script_optimization_drafts.c.kind).where(
                        script_optimization_drafts.c.optimization_id
                        == int(optimization_id),
                        script_optimization_drafts.c.episode_number == int(number),
                    )
                ).scalars().all()
                if len(kinds) != len(expected) or set(kinds) != expected:
                    raise ValueError(f"第 {number} 集优化草稿尚未完成")
            connection.execute(
                update(script_optimizations)
                .where(script_optimizations.c.id == int(optimization_id))
                .values(status="completed", error="", updated_at=utc_now())
            )
        return self.get_script_optimization(optimization_id) or {}

    def complete_script_optimization(
        self, optimization_id: int, drafts: list[dict[str, Any]]
    ) -> dict[str, Any]:
        optimization = self.get_script_optimization(optimization_id)
        if not optimization:
            raise ValueError("优化任务不存在")
        now = utc_now()
        # Draft rows, the structure peer links and the task status land in one
        # transaction: a partially written optimization must never be readable.
        with self.transaction_scope() as connection:
            inserted: list[tuple[dict[str, Any], int]] = []
            for draft in drafts:
                draft_id = connection.execute(
                    insert(script_optimization_drafts)
                    .values(
                        optimization_id=int(optimization_id),
                        project_id=int(
                            draft.get("project_id") or optimization["project_id"]
                        ),
                        episode_number=int(draft["episode_number"]),
                        kind=str(draft["kind"]),
                        source_episode_id=draft.get("source_episode_id"),
                        source_fingerprint=str(draft.get("source_fingerprint") or ""),
                        content_json=jsonb_copy(draft.get("content") or {}),
                        peer_draft_id=draft.get("peer_draft_id"),
                        created_at=now,
                        updated_at=now,
                    )
                    .returning(script_optimization_drafts.c.id)
                ).scalar_one()
                inserted.append((draft, int(draft_id)))
            if optimization["mode"] == "structure":
                pairs: dict[int, dict[str, int]] = {}
                for draft, draft_id in inserted:
                    kind = str(draft.get("kind") or "")
                    if kind in {"beats", "script"}:
                        pairs.setdefault(int(draft["episode_number"]), {})[kind] = draft_id
                for pair in pairs.values():
                    if "beats" in pair and "script" in pair:
                        connection.execute(
                            update(script_optimization_drafts)
                            .where(script_optimization_drafts.c.id == pair["beats"])
                            .values(peer_draft_id=pair["script"])
                        )
                        connection.execute(
                            update(script_optimization_drafts)
                            .where(script_optimization_drafts.c.id == pair["script"])
                            .values(peer_draft_id=pair["beats"])
                        )
            connection.execute(
                update(script_optimizations)
                .where(script_optimizations.c.id == int(optimization_id))
                .values(status="completed", error="", updated_at=now)
            )
        return self.get_script_optimization(optimization_id) or {}

    def fail_script_optimization(
        self, optimization_id: int, error: str
    ) -> dict[str, Any]:
        return self._update_script_optimization(
            optimization_id, {"status": "failed", "error": str(error)[:2000]}
        )

    def get_script_optimization_draft(self, draft_id: int) -> dict[str, Any] | None:
        with self.connection_scope() as connection:
            row = connection.execute(
                select(script_optimization_drafts).where(
                    script_optimization_drafts.c.id == int(draft_id)
                )
            ).mappings().one_or_none()
        return self._script_optimization_draft(self.row_dict(row)) if row is not None else None

    def list_episode_script_optimization_drafts(
        self, project_id: int, episode_number: int, kind: str
    ) -> list[dict[str, Any]]:
        with self.connection_scope() as connection:
            rows = connection.execute(
                select(script_optimization_drafts)
                .where(
                    script_optimization_drafts.c.project_id == int(project_id),
                    script_optimization_drafts.c.episode_number
                    == int(episode_number),
                    script_optimization_drafts.c.kind == kind,
                )
                .order_by(
                    script_optimization_drafts.c.created_at.desc(),
                    script_optimization_drafts.c.id.desc(),
                )
            ).mappings().all()
        return [self._script_optimization_draft(self.row_dict(row)) for row in rows]

    def accept_script_optimization_draft(
        self, draft_id: int, episode_id: int, accepted_by: str
    ) -> dict[str, Any]:
        """Bind a ready draft to the episode version it produced, once.

        Accepting is what records the new episode version: the caller creates
        that version (the legacy API called ``create_episode_script`` /
        ``create_episode_beats`` before this call) and this method stores its ID.
        The status change and ``accepted_episode_id`` are one conditional
        ``UPDATE`` in one transaction, so a draft is never left accepted without
        a version and never accepted twice.
        """

        draft = self.get_script_optimization_draft(draft_id)
        if not draft:
            raise ValueError("优化草稿不存在")
        if draft["status"] != "ready":
            raise ValueError("该优化草稿当前不能采纳")
        now = utc_now()
        with self.transaction_scope() as connection:
            accepted_id = connection.execute(
                update(script_optimization_drafts)
                .where(
                    script_optimization_drafts.c.id == int(draft_id),
                    script_optimization_drafts.c.status == "ready",
                )
                .values(
                    status="accepted",
                    accepted_episode_id=int(episode_id),
                    accepted_by=accepted_by,
                    accepted_at=now,
                    updated_at=now,
                )
                .returning(script_optimization_drafts.c.id)
            ).scalar_one_or_none()
            if accepted_id is None:
                raise ValueError("该优化草稿当前不能采纳")
        return self.get_script_optimization_draft(draft_id) or {}

    def reject_script_optimization_draft(
        self, draft_id: int, rejection_note: str
    ) -> dict[str, Any]:
        draft = self.get_script_optimization_draft(draft_id)
        if not draft:
            raise ValueError("优化草稿不存在")
        if draft["status"] != "ready":
            raise ValueError("该优化草稿当前不能拒绝")
        with self.transaction_scope() as connection:
            rejected_id = connection.execute(
                update(script_optimization_drafts)
                .where(
                    script_optimization_drafts.c.id == int(draft_id),
                    script_optimization_drafts.c.status == "ready",
                )
                .values(
                    status="rejected",
                    rejection_note=rejection_note.strip(),
                    updated_at=utc_now(),
                )
                .returning(script_optimization_drafts.c.id)
            ).scalar_one_or_none()
            if rejected_id is None:
                raise ValueError("该优化草稿当前不能拒绝")
        return self.get_script_optimization_draft(draft_id) or {}

    def _update_script_optimization(
        self, optimization_id: int, values: dict[str, Any]
    ) -> dict[str, Any]:
        with self.transaction_scope() as connection:
            connection.execute(
                update(script_optimizations)
                .where(script_optimizations.c.id == int(optimization_id))
                .values(**values, updated_at=utc_now())
            )
        return self.get_script_optimization(optimization_id) or {}

    # -- internals ----------------------------------------------------------

    def _lock_project_reviews(self, connection: Connection, project_id: int) -> None:
        """Serialise project-review creation for one project (advisory lock).

        Held until the surrounding transaction ends. See the module docstring
        for the ordering rule this lock participates in.
        """

        digest = hashlib.sha256(
            f"{self.store_id}:project:{int(project_id)}".encode("utf-8")
        ).digest()
        connection.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": int.from_bytes(digest[:8], "big", signed=True)},
        )

    @staticmethod
    def _script_review(row: dict[str, Any]) -> dict[str, Any]:
        """Adapt the legacy JSON transform: the JSONB values are already decoded."""

        episode_numbers = row.pop("episode_numbers_json")
        row["episode_numbers"] = (
            [] if episode_numbers in (None, "") else episode_numbers
        )
        source_payload = row.pop("source_payload_json")
        row["source_payload"] = {} if source_payload in (None, "") else source_payload
        report = row.pop("report_json")
        row["report"] = {} if report in (None, "") else report
        return row

    @staticmethod
    def _script_optimization(row: dict[str, Any]) -> dict[str, Any]:
        selected_findings = row.pop("selected_findings_json")
        row["selected_findings"] = (
            [] if selected_findings in (None, "") else selected_findings
        )
        target_episode_numbers = row.pop("target_episode_numbers_json")
        row["target_episode_numbers"] = (
            [] if target_episode_numbers in (None, "") else target_episode_numbers
        )
        prompt_snapshot = row.pop("prompt_snapshot_json", None)
        row["prompt_snapshot"] = (
            {} if prompt_snapshot in (None, "") else prompt_snapshot
        )
        return row

    @staticmethod
    def _script_optimization_draft(row: dict[str, Any]) -> dict[str, Any]:
        content = row.pop("content_json")
        row["content"] = {} if content in (None, "") else content
        if row.get("kind") == "script":
            row["content"] = normalize_script_content(row["content"])
        return row


__all__ = ["ReviewsRepository"]
