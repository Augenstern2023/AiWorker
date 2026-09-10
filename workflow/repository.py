# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from sqlalchemy import Connection, DateTime, and_, func, or_, select, insert, update, delete, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from datetime import date, datetime
import hashlib
import json
import re
import time
import uuid
from contextlib import nullcontext

from identity.repository import IdentityRepository
from identity.schema import DATA_SCOPE_ORDER, Principal

from system_database.schema import (
    project_members,
    role_claimable_task_types,
    role_permissions,
    roles,
    user_roles,
    users,
    workflow_instances,
    workflow_project_deletions,
    workflow_project_member_events,
    workflow_submissions,
    workflow_task_events,
    workflow_task_jobs,
    workflow_tasks,
)

from .state_machine import WorkflowStateMachine

OWNER_PROJECT_ROLES = frozenset({"owner", "project_owner"})
PROJECT_CREATOR_ROLE = "project_creator"
_MANAGED_PROJECT_ROLES = OWNER_PROJECT_ROLES | frozenset(
    {"manager", "project_manager"}
)
ESTABLISHED_WRITING_PERMISSION = "project.write.established.writing"
ESTABLISHED_WRITING_TASK_TYPES = frozenset({"outline", "beats", "script"})
ACTIVE_TASK_STATUSES = frozenset({"assigned", "in_progress", "returned"})
SYSTEM_ACTOR = Principal(
    user_id="workflow:system",
    username="workflow-system",
    display_name="Workflow System",
    role_codes=("system_admin",),
    permissions=frozenset({"*"}),
    data_scope="all",
    session_id="workflow:system",
)

# PG schema managed by Alembic

class WorkflowRepository:
    def __init__(
        self,
        repository: IdentityRepository | str | Path,
        state_machine: WorkflowStateMachine | None = None,
    ) -> None:
        self.identity_repository = (
            repository
            if isinstance(repository, IdentityRepository)
            else IdentityRepository(repository)
        )
        self.state_machine = state_machine or WorkflowStateMachine()
        # Schema managed by Alembic

    def delete_project_data(
        self,
        project_id: str,
        *,
        expected_writer_fingerprint: str | None = None,
        expected_writer_external_fingerprint: str | None = None,
        writer_external_baseline_state: dict[str, Any] | None = None,
        writer_ownership_manifest: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        """Remove workflow records that belong exclusively to a deleted project."""

        with self.identity_repository.transaction() as connection:
            self._lock_project_write(
                connection, str(project_id), allow_deleting=True
            )
            marker_preexisted = connection.execute(
                select(workflow_project_deletions.c.project_id).where(
                    workflow_project_deletions.c.project_id == str(project_id)
                )
            ).first() is not None
            if expected_writer_fingerprint is not None:
                current = self._writer_project_deletion_state_conn(
                    connection, str(project_id)
                )
                if current["fingerprint"] != expected_writer_fingerprint:
                    raise ValueError("workflow deletion state changed")
            if expected_writer_external_fingerprint is not None:
                current_external = self._writer_project_external_use_fingerprint_conn(
                    connection,
                    str(project_id),
                    baseline_state=writer_external_baseline_state,
                    manifest=writer_ownership_manifest,
                )
                if current_external != expected_writer_external_fingerprint:
                    raise ValueError("workflow project has external use")
            self._mark_project_deleting_conn(connection, str(project_id))
            workflow_ids = {
                str(row["id"])
                for row in connection.execute(
                    select(workflow_instances.c.id).where(
                        workflow_instances.c.project_id == project_id
                    )
                ).mappings()
            }
            workflow_ids.update(
                str(row["workflow_id"])
                for row in connection.execute(
                    select(workflow_tasks.c.workflow_id).where(
                        workflow_tasks.c.project_id == project_id
                    )
                ).mappings()
            )
            task_ids: set[str] = set()
            if workflow_ids:
                task_ids.update(
                    str(row["id"])
                    for row in connection.execute(
                        select(workflow_tasks.c.id).where(
                            workflow_tasks.c.workflow_id.in_(workflow_ids)
                        )
                    ).mappings()
                )
            if task_ids:
                connection.execute(
                    delete(workflow_submissions).where(
                        workflow_submissions.c.task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    delete(workflow_task_jobs).where(
                        workflow_task_jobs.c.task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    delete(workflow_task_events).where(
                        workflow_task_events.c.task_id.in_(task_ids)
                    )
                )
                connection.execute(
                    delete(workflow_tasks).where(workflow_tasks.c.id.in_(task_ids))
                )
            if workflow_ids:
                connection.execute(
                    delete(workflow_instances).where(workflow_instances.c.id.in_(workflow_ids))
                )
            connection.execute(
                delete(workflow_project_member_events).where(
                    workflow_project_member_events.c.project_id == project_id
                )
            )
            connection.execute(
                delete(project_members).where(project_members.c.project_id == project_id)
            )
            if not marker_preexisted:
                connection.execute(
                    delete(workflow_project_deletions).where(
                        workflow_project_deletions.c.project_id == str(project_id)
                    )
                )
        return {"workflow_count": len(workflow_ids), "task_count": len(task_ids)}

    @staticmethod
    def _lock_project_write(
        connection: Connection,
        project_id: str,
        *,
        allow_deleting: bool = False,
    ) -> None:
        normalized_project_id = str(project_id)
        connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"workflow-project:{normalized_project_id}",),
        )
        if allow_deleting:
            return
        deleting = connection.execute(
            select(workflow_project_deletions.c.project_id).where(
                workflow_project_deletions.c.project_id == normalized_project_id
            )
        ).first()
        if deleting is not None:
            raise ValueError("workflow project is being deleted")

    def _lock_task_project_write(
        self, connection: Connection, task_id: str
    ) -> dict[str, Any]:
        task = self._require_task(connection, task_id)
        if task.get("project_id") is not None:
            self._lock_project_write(connection, str(task["project_id"]))
            task = self._require_task(connection, task_id)
        return task

    def is_project_deleting(self, project_id: str) -> bool:
        """Return whether cross-database project deletion is still in progress."""

        with self.identity_repository.connect() as connection:
            row = connection.execute(
                select(workflow_project_deletions.c.project_id).where(
                    workflow_project_deletions.c.project_id == str(project_id)
                )
            ).first()
        return row is not None

    @staticmethod
    def _project_deletion_dict(row: Any) -> dict[str, Any]:
        marker = dict(row)
        snapshot = marker.pop("project_snapshot_json", {})
        marker["project_snapshot"] = (
            json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        ) or {}
        return marker

    def get_pending_project_deletion(
        self, project_id: str
    ) -> dict[str, Any] | None:
        """Return the durable workflow-side deletion marker, if present."""

        with self.identity_repository.connect() as connection:
            row = connection.execute(
                select(workflow_project_deletions).where(
                    workflow_project_deletions.c.project_id == str(project_id)
                )
            ).mappings().first()
        return self._project_deletion_dict(row) if row is not None else None

    def begin_project_deletion(
        self,
        project_id: str,
        *,
        owner_token: str | None = None,
        requested_by: str = "",
        project_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Fence new workflow writes before either database deletes project data."""

        normalized_project_id = str(project_id)
        marker_owner = owner_token or f"legacy-project-deletion:{normalized_project_id}"
        marker_requester = requested_by or SYSTEM_ACTOR.user_id
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(
                connection, normalized_project_id, allow_deleting=True
            )
            connection.execute(
                pg_insert(workflow_project_deletions)
                .values(
                    project_id=normalized_project_id,
                    owner_token=marker_owner,
                    requested_by=marker_requester,
                    project_snapshot_json=project_snapshot or {},
                    status="deleting",
                )
                .on_conflict_do_nothing(index_elements=["project_id"])
            )
            row = connection.execute(
                select(workflow_project_deletions).where(
                    workflow_project_deletions.c.project_id == normalized_project_id
                )
            ).mappings().one()
        return self._project_deletion_dict(row)

    @staticmethod
    def _mark_project_deleting_conn(
        connection: Connection, project_id: str
    ) -> None:
        connection.exec_driver_sql(
            """
            INSERT INTO workflow_project_deletions
                (project_id, owner_token, requested_by, project_snapshot_json,
                 status, created_at, updated_at)
            VALUES (%s, %s, %s, '{}'::jsonb, 'deleting',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(project_id) DO UPDATE
            SET status = 'deleting', updated_at = CURRENT_TIMESTAMP
            """,
            (
                str(project_id),
                f"internal-project-deletion:{project_id}",
                SYSTEM_ACTOR.user_id,
            ),
        )

    def abort_project_deletion(self, project_id: str, *, owner_token: str) -> bool:
        """Release only the marker owned by an operation that never deleted Creative."""

        with self.identity_repository.transaction() as connection:
            self._lock_project_write(
                connection, str(project_id), allow_deleting=True
            )
            result = connection.execute(
                delete(workflow_project_deletions).where(
                    workflow_project_deletions.c.project_id == str(project_id),
                    workflow_project_deletions.c.owner_token == owner_token,
                )
            )
        return result.rowcount == 1

    def complete_project_deletion(
        self, project_id: str, *, owner_token: str | None = None
    ) -> None:
        """Release the workflow write fence after Creative clears its tombstone."""

        with self.identity_repository.transaction() as connection:
            self._lock_project_write(
                connection, str(project_id), allow_deleting=True
            )
            condition = (
                workflow_project_deletions.c.project_id == str(project_id)
            )
            if owner_token is not None:
                condition = and_(
                    condition,
                    workflow_project_deletions.c.owner_token == owner_token,
                )
            connection.execute(delete(workflow_project_deletions).where(condition))

    @staticmethod
    def _writer_rollback_json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {
                str(key): WorkflowRepository._writer_rollback_json_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [
                WorkflowRepository._writer_rollback_json_value(item)
                for item in value
            ]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _writer_rollback_rows(
        connection: Connection, table: Any, condition: Any
    ) -> list[dict[str, Any]]:
        rows = [
            {
                key: WorkflowRepository._writer_rollback_json_value(value)
                for key, value in dict(row).items()
                if key not in {"created_at", "updated_at"}
            }
            for row in connection.execute(
                select(table).where(condition)
            ).mappings()
        ]
        primary_keys = [column.name for column in table.primary_key.columns]
        return sorted(
            rows,
            key=lambda row: tuple(str(row.get(key) or "") for key in primary_keys),
        )

    def _writer_project_deletion_state_conn(
        self, connection: Connection, project_id: str
    ) -> dict[str, Any]:
        project_id = str(project_id)
        workflow_ids = {
            str(row["id"])
            for row in connection.execute(
                select(workflow_instances.c.id).where(
                    workflow_instances.c.project_id == project_id
                )
            ).mappings()
        }
        workflow_ids.update(
            str(row["workflow_id"])
            for row in connection.execute(
                select(workflow_tasks.c.workflow_id).where(
                    workflow_tasks.c.project_id == project_id
                )
            ).mappings()
        )
        instances = self._writer_rollback_rows(
            connection,
            workflow_instances,
            workflow_instances.c.id.in_(workflow_ids),
        )
        tasks = self._writer_rollback_rows(
            connection,
            workflow_tasks,
            workflow_tasks.c.workflow_id.in_(workflow_ids),
        )
        task_ids = [str(row["id"]) for row in tasks]

        def task_rows(table: Any) -> list[dict[str, Any]]:
            if not task_ids:
                return []
            return self._writer_rollback_rows(
                connection, table, table.c.task_id.in_(task_ids)
            )

        payload = {
            "project_id": project_id,
            "tables": {
                "workflow_instances": instances,
                "workflow_tasks": tasks,
                "workflow_submissions": task_rows(workflow_submissions),
                "workflow_task_jobs": task_rows(workflow_task_jobs),
                "workflow_task_events": task_rows(workflow_task_events),
                "project_members": self._writer_rollback_rows(
                    connection,
                    project_members,
                    project_members.c.project_id == project_id,
                ),
                "workflow_project_member_events": self._writer_rollback_rows(
                    connection,
                    workflow_project_member_events,
                    workflow_project_member_events.c.project_id == project_id,
                ),
            },
        }
        return {
            **payload,
            "fingerprint": hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        }

    def writer_project_deletion_fingerprint(self, project_id: str) -> str:
        """Fingerprint every workflow row project deletion would remove."""

        with self.identity_repository.connect() as connection:
            state = self._writer_project_deletion_state_conn(
                connection, str(project_id)
            )
        return str(state["fingerprint"])

    def _writer_project_external_use_fingerprint_conn(
        self,
        connection: Connection,
        project_id: str,
        *,
        baseline_state: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> str:
        state = self._writer_project_deletion_state_conn(
            connection, str(project_id)
        )
        payload = self._writer_project_external_use_payload(
            state, manifest=manifest
        )
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _writer_project_external_use_payload(
        cls,
        state: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        manifest = manifest or {}
        owned_operation_ids = {
            str(value) for value in manifest.get("operation_ids") or []
        }
        owned_task_ids = {str(value) for value in manifest.get("task_ids") or []}
        owned_job_ids = {str(value) for value in manifest.get("job_ids") or []}
        tables = {
            str(name): [dict(row) for row in rows]
            for name, rows in (state.get("tables") or {}).items()
        }
        owned_task_ids.update(
            str(row.get("task_id") or "")
            for row in tables.get("workflow_task_jobs") or []
            if str(row.get("job_id") or "") in owned_job_ids
        )
        owned_event_ids: set[str] = set()
        for row in tables.get("workflow_task_events") or []:
            details = row.get("details_json") or {}
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except (TypeError, json.JSONDecodeError):
                    details = {}
            if str(details.get("writer_operation_id") or "") in owned_operation_ids:
                owned_event_ids.add(str(row.get("id") or ""))
                owned_task_ids.add(str(row.get("task_id") or ""))
        all_tasks = list(tables.get("workflow_tasks") or [])
        workflow_task_ids: dict[str, set[str]] = {}
        for row in all_tasks:
            workflow_task_ids.setdefault(str(row.get("workflow_id") or ""), set()).add(
                str(row.get("id") or "")
            )
        owned_workflow_ids = {
            workflow_id
            for workflow_id, task_ids in workflow_task_ids.items()
            if task_ids and task_ids <= owned_task_ids
        }
        tables["workflow_instances"] = [
            row for row in tables.get("workflow_instances") or []
            if str(row.get("id") or "") not in owned_workflow_ids
        ]
        tables["workflow_tasks"] = [
            row for row in all_tasks
            if str(row.get("id") or "") not in owned_task_ids
        ]
        tables["workflow_task_jobs"] = [
            row for row in tables.get("workflow_task_jobs") or []
            if not (
                str(row.get("task_id") or "") in owned_task_ids
                and str(row.get("job_id") or "") in owned_job_ids
            )
        ]
        tables["workflow_task_events"] = [
            row for row in tables.get("workflow_task_events") or []
            if str(row.get("id") or "") not in owned_event_ids
        ]
        owned_member_event_ids: set[str] = set()
        owned_new_membership_ids: set[str] = set()
        for row in tables.get("workflow_project_member_events") or []:
            if str(row.get("writer_operation_id") or "") not in owned_operation_ids:
                continue
            owned_member_event_ids.add(str(row.get("id") or ""))
            if row.get("before_json") is None:
                owned_new_membership_ids.add(str(row.get("membership_id") or ""))
        tables["project_members"] = [
            row for row in tables.get("project_members") or []
            if str(row.get("id") or "") not in owned_new_membership_ids
        ]
        tables["workflow_project_member_events"] = [
            row for row in tables.get("workflow_project_member_events") or []
            if str(row.get("id") or "") not in owned_member_event_ids
        ]
        return {"project_id": str(state.get("project_id") or ""), "tables": tables}

    def export_writer_project_external_use_state(
        self, project_id: str
    ) -> dict[str, Any]:
        """Capture the workflow baseline used to distinguish Writer-owned rows."""

        with self.identity_repository.connect() as connection:
            return self._writer_project_deletion_state_conn(
                connection, str(project_id)
            )

    @classmethod
    def writer_project_external_use_baseline_fingerprint(
        cls,
        baseline_state: dict[str, Any],
        *,
        manifest: dict[str, Any] | None = None,
    ) -> str:
        payload = cls._writer_project_external_use_payload(
            baseline_state, manifest=manifest
        )
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def writer_project_external_use_fingerprint(
        self,
        project_id: str,
        *,
        baseline_state: dict[str, Any] | None = None,
        manifest: dict[str, Any] | None = None,
    ) -> str:
        """Fingerprint project workflow rows not declared as Writer-owned."""

        with self.identity_repository.connect() as connection:
            return self._writer_project_external_use_fingerprint_conn(
                connection,
                str(project_id),
                baseline_state=baseline_state,
                manifest=manifest,
            )

    def writer_project_data_exists(self, project_id: str) -> bool:
        """Return whether project deletion still has workflow rows to remove."""

        with self.identity_repository.connect() as connection:
            state = self._writer_project_deletion_state_conn(
                connection, str(project_id)
            )
        return any(state["tables"].values())

    def _export_writer_rollback_state_conn(
        self, connection: Connection, project_id: str
    ) -> dict[str, Any]:
        instances = self._writer_rollback_rows(
            connection,
            workflow_instances,
            and_(
                workflow_instances.c.project_id == project_id,
                workflow_instances.c.status != "rolled_back",
            ),
        )
        tasks = self._writer_rollback_rows(
            connection,
            workflow_tasks,
            and_(
                workflow_tasks.c.project_id == project_id,
                workflow_tasks.c.status != "cancelled",
            ),
        )
        task_ids = [str(row["id"]) for row in tasks]
        submissions = (
            self._writer_rollback_rows(
                connection,
                workflow_submissions,
                and_(
                    workflow_submissions.c.task_id.in_(task_ids),
                    workflow_submissions.c.status != "superseded",
                ),
            )
            if task_ids
            else []
        )
        jobs = (
            self._writer_rollback_rows(
                connection,
                workflow_task_jobs,
                workflow_task_jobs.c.task_id.in_(task_ids),
            )
            if task_ids
            else []
        )
        members = self._writer_rollback_rows(
            connection,
            project_members,
            and_(
                project_members.c.project_id == project_id,
                project_members.c.status != "inactive",
            ),
        )
        payload = {
            "project_id": str(project_id),
            "tables": {
                "workflow_instances": instances,
                "workflow_tasks": tasks,
                "workflow_submissions": submissions,
                "workflow_task_jobs": jobs,
                "project_members": members,
            },
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {**payload, "fingerprint": fingerprint}

    def export_writer_rollback_state(
        self, project_id: str
    ) -> dict[str, Any]:
        """Capture mutable workflow rows owned by one creative project."""

        with self.identity_repository.connect() as connection:
            return self._export_writer_rollback_state_conn(
                connection, str(project_id)
            )

    def writer_rollback_fingerprint(self, project_id: str) -> str:
        return str(self.export_writer_rollback_state(project_id)["fingerprint"])

    @staticmethod
    def _writer_rollback_database_row(
        table: Any, row: dict[str, Any]
    ) -> dict[str, Any]:
        converted = dict(row)
        for column in table.columns:
            value = converted.get(column.name)
            if (
                value is not None
                and isinstance(value, str)
                and (
                    isinstance(column.type, DateTime)
                    or column.name.endswith("_at")
                )
            ):
                try:
                    converted[column.name] = datetime.fromisoformat(value)
                except ValueError:
                    pass
        return converted

    @staticmethod
    def _restore_writer_rollback_rows(
        connection: Connection, table: Any, rows: list[dict[str, Any]]
    ) -> None:
        primary_keys = list(table.primary_key.columns)
        for source in rows:
            row = WorkflowRepository._writer_rollback_database_row(table, source)
            predicate = and_(
                *[column == row[column.name] for column in primary_keys]
            )
            changed = connection.execute(
                update(table).where(predicate).values(**row)
            ).rowcount
            if changed == 0:
                connection.execute(insert(table).values(**row))

    def restore_writer_rollback_state(
        self,
        project_id: str,
        preimage: dict[str, Any],
        *,
        expected_fingerprint: str,
        actor: Principal,
        writer_operation_id: str | None = None,
    ) -> dict[str, Any]:
        """Restore workflow rows only when the action post-state still matches."""

        project_id = str(project_id)
        if str(preimage.get("project_id") or "") != project_id:
            raise ValueError("rollback snapshot belongs to another project")
        tables = preimage.get("tables")
        if not isinstance(tables, dict):
            raise ValueError("rollback snapshot is invalid")
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            current = self._export_writer_rollback_state_conn(
                connection, project_id
            )
            if current["fingerprint"] == preimage.get("fingerprint"):
                return current
            if current["fingerprint"] != expected_fingerprint:
                raise ValueError("workflow rollback state changed")

            pre_tasks = list(tables.get("workflow_tasks") or [])
            pre_task_ids = {str(row["id"]) for row in pre_tasks}
            current_tasks = list(current["tables"]["workflow_tasks"])
            current_task_ids = {str(row["id"]) for row in current_tasks}
            all_task_ids = current_task_ids | pre_task_ids
            previous_status = {
                str(row["id"]): str(row.get("status") or "")
                for row in current_tasks
            }

            if all_task_ids:
                connection.execute(
                    delete(workflow_task_jobs).where(
                        workflow_task_jobs.c.task_id.in_(all_task_ids)
                    )
                )
            extra_task_ids = current_task_ids - pre_task_ids
            if extra_task_ids:
                connection.execute(
                    update(workflow_tasks)
                    .where(workflow_tasks.c.id.in_(extra_task_ids))
                    .values(status="cancelled", updated_at=func.now())
                )
            self._restore_writer_rollback_rows(
                connection, workflow_tasks, pre_tasks
            )
            self._restore_writer_rollback_rows(
                connection,
                workflow_task_jobs,
                list(tables.get("workflow_task_jobs") or []),
            )

            pre_submissions = list(tables.get("workflow_submissions") or [])
            pre_submission_ids = {str(row["id"]) for row in pre_submissions}
            if all_task_ids:
                extra_submissions = {
                    str(row["id"])
                    for row in connection.execute(
                        select(workflow_submissions.c.id).where(
                            workflow_submissions.c.task_id.in_(all_task_ids)
                        )
                    ).mappings()
                    if str(row["id"]) not in pre_submission_ids
                }
                if extra_submissions:
                    connection.execute(
                        update(workflow_submissions)
                        .where(workflow_submissions.c.id.in_(extra_submissions))
                        .values(status="superseded", updated_at=func.now())
                    )
            self._restore_writer_rollback_rows(
                connection, workflow_submissions, pre_submissions
            )

            pre_instances = list(tables.get("workflow_instances") or [])
            pre_instance_ids = {str(row["id"]) for row in pre_instances}
            current_instance_ids = {
                str(row["id"])
                for row in current["tables"]["workflow_instances"]
            }
            extra_instance_ids = current_instance_ids - pre_instance_ids
            if extra_instance_ids:
                connection.execute(
                    update(workflow_instances)
                    .where(workflow_instances.c.id.in_(extra_instance_ids))
                    .values(status="rolled_back")
                )
            self._restore_writer_rollback_rows(
                connection, workflow_instances, pre_instances
            )

            pre_members = list(tables.get("project_members") or [])
            pre_member_ids = {str(row["id"]) for row in pre_members}
            current_member_ids = {
                str(row["id"])
                for row in current["tables"]["project_members"]
            }
            extra_member_ids = current_member_ids - pre_member_ids
            if extra_member_ids:
                connection.execute(
                    update(project_members)
                    .where(project_members.c.id.in_(extra_member_ids))
                    .values(status="inactive")
                )
            self._restore_writer_rollback_rows(
                connection, project_members, pre_members
            )

            for task_id in sorted(all_task_ids):
                target_status = next(
                    (
                        str(row.get("status") or "")
                        for row in pre_tasks
                        if str(row["id"]) == task_id
                    ),
                    "cancelled",
                )
                connection.execute(
                    insert(workflow_task_events).values(
                        id=str(uuid.uuid4()),
                        task_id=task_id,
                        event_type="writer.rollback",
                        actor_id=actor_id,
                        from_status=previous_status.get(task_id),
                        to_status=target_status,
                        details_json={
                            "project_id": project_id,
                            **(
                                {"writer_operation_id": writer_operation_id}
                                if writer_operation_id
                                else {}
                            ),
                        },
                    )
                )

            restored = self._export_writer_rollback_state_conn(
                connection, project_id
            )
            if restored["fingerprint"] != preimage.get("fingerprint"):
                raise ValueError("workflow rollback verification failed")
        return restored

    def create_task(
        self,
        workflow_id: str,
        task_type: str,
        role_code: str,
        *,
        project_id: str | None = None,
        topic_id: int | None = None,
        due_at: str | None = None,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        task_id = str(uuid.uuid4())
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            if project_id is not None:
                self._lock_project_write(connection, str(project_id))
            self._ensure_system_actor(connection, actor_id)
            role = connection.exec_driver_sql(
                "SELECT 1 FROM roles WHERE code = %s  AND is_enabled = 1",
                (role_code,),
            ).mappings().fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            self._ensure_workflow(
                connection,
                workflow_id,
                task_type,
                project_id,
                topic_id,
                actor_id,
            )
            connection.exec_driver_sql(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, 'pool', %s)
                """,
                (
                    task_id,
                    workflow_id,
                    task_type,
                    role_code,
                    topic_id,
                    project_id,
                    due_at,
                ),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {"role_code": role_code},
            )
            task = self._get_task(connection, task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self.identity_repository.connect() as connection:
            return self._get_task(connection, task_id)

    def ensure_topic_task(
        self,
        topic_id: int | None,
        *,
        task_type: str,
        role_code: str,
        actor: Principal | dict[str, Any],
        project_id: str | None = None,
        workflow_scope: str | None = None,
    ) -> dict[str, Any]:
        workflow_id = f"topic:{workflow_scope or topic_id}"
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            if project_id is not None:
                self._lock_project_write(connection, str(project_id))
            existing = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = %s AND task_type = %s AND topic_id = %s
                ORDER BY created_at, id LIMIT 1
                """,
                (workflow_id, task_type, topic_id),
            ).mappings().fetchone()
            if existing is not None:
                if existing["role_code"] != role_code:
                    raise ValueError("workflow task role does not match topic workflow")
                if (
                    project_id is not None
                    and existing["project_id"] not in {None, project_id}
                ):
                    raise ValueError("topic workflow is already linked to another project")
                if project_id is not None and existing["project_id"] is None:
                    connection.exec_driver_sql(
                        "UPDATE workflow_instances SET project_id = %s, resource_type = 'project', "
                        "resource_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                        (project_id, project_id, workflow_id),
                    )
                    connection.exec_driver_sql(
                        "UPDATE workflow_tasks SET project_id = %s, updated_at = CURRENT_TIMESTAMP "
                        "WHERE workflow_id = %s AND project_id IS NULL",
                        (project_id, workflow_id),
                    )
                    existing = connection.exec_driver_sql(
                        "SELECT * FROM workflow_tasks WHERE id = %s", (existing["id"],)
                    ).mappings().fetchone()
                return self._task_dict(existing)
            self._ensure_workflow(
                connection, workflow_id, task_type, project_id, topic_id, actor_id
            )
            task_id = str(uuid.uuid4())
            connection.exec_driver_sql(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, 'pool', NULL)
                """,
                (task_id, workflow_id, task_type, role_code, topic_id, project_id),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {"role_code": role_code, "bootstrap": True},
            )
            return self._require_task(connection, task_id)

    def ensure_project_task(
        self,
        project_id: str,
        topic_id: int | None,
        task_type: str = "brief",
        role_code: str = "creative_planner",
        *,
        actor: Principal | dict[str, Any],
        writer_operation_id: str | None = None,
    ) -> dict[str, Any]:
        workflow_id = f"project:{project_id}"
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            self._ensure_system_actor(connection, actor_id)
            self._ensure_workflow(
                connection,
                workflow_id,
                task_type,
                project_id,
                topic_id,
                actor_id,
            )
            connection.exec_driver_sql(
                "SELECT id FROM workflow_instances WHERE id = %s FOR UPDATE",
                (workflow_id,),
            ).mappings().fetchone()
            existing = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = %s AND task_type = %s AND project_id = %s
                ORDER BY created_at DESC, id DESC LIMIT 1
                """,
                (workflow_id, task_type, project_id),
            ).mappings().fetchone()
            if existing is not None:
                if existing["topic_id"] != topic_id:
                    raise ValueError("project workflow task belongs to another topic")
                if existing["role_code"] != role_code:
                    raise ValueError("project workflow task role does not match")
                return self._task_dict(existing)
            role = connection.exec_driver_sql(
                "SELECT 1 FROM roles WHERE code = %s  "
                "AND is_enabled = 1 AND is_deleted = 0",
                (role_code,),
            ).mappings().fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            task_id = str(uuid.uuid4())
            connection.exec_driver_sql(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, status, due_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, 'pool', NULL)
                """,
                (
                    task_id,
                    workflow_id,
                    task_type,
                    role_code,
                    topic_id,
                    project_id,
                ),
            )
            self._record_event(
                connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {
                    "role_code": role_code,
                    "project_bootstrap": True,
                    **(
                        {"writer_operation_id": writer_operation_id}
                        if writer_operation_id
                        else {}
                    ),
                },
            )
            return self._require_task(connection, task_id)

    def link_task_job(self, task_id: str, job_id: str) -> None:
        with self.identity_repository.transaction() as connection:
            self._lock_task_project_write(connection, task_id)
            connection.exec_driver_sql(
                "INSERT INTO workflow_task_jobs (task_id, job_id) VALUES (%s, %s)",
                (task_id, job_id),
            )

    def bind_workflow_project(
        self,
        task_id: str,
        project_id: str,
        creator: Principal | dict[str, Any],
        *,
        expected_task_type: str | None = None,
        expected_topic_id: int | None = None,
        require_creator_assignment: bool = False,
    ) -> dict[str, Any]:
        creator_id = self._actor_id(creator)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            task = self._require_task(connection, task_id)
            if task["project_id"] not in {None, project_id}:
                raise ValueError("workflow task is already linked to another project")
            live_creator = self._live_actor(connection, creator_id)
            if expected_task_type is not None and task["task_type"] != expected_task_type:
                raise ValueError("workflow node does not match project creation")
            if expected_topic_id is not None and task["topic_id"] != expected_topic_id:
                raise ValueError("workflow task belongs to another topic")
            if (
                require_creator_assignment
                and not live_creator["is_system_admin"]
                and task["assignee_user_id"] != creator_id
            ):
                raise PermissionError("only the task assignee may create this project")
            if (
                (expected_task_type is not None or require_creator_assignment)
                and task["status"] not in {"assigned", "in_progress", "returned"}
            ):
                raise ValueError("workflow task is not open for project creation")
            self._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
            connection.exec_driver_sql(
                "UPDATE workflow_instances SET project_id = %s, resource_type = 'project', "
                "resource_id = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                (project_id, project_id, task["workflow_id"]),
            )
            connection.exec_driver_sql(
                "UPDATE workflow_tasks SET project_id = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE workflow_id = %s AND project_id IS NULL",
                (project_id, task["workflow_id"]),
            )
            members = {creator_id: task["role_code"]}
            reviewers = connection.exec_driver_sql(
                """
                SELECT DISTINCT s.reviewed_by
                FROM workflow_submissions s
                JOIN workflow_tasks t ON t.id = s.task_id
                WHERE t.workflow_id = %s AND s.reviewed_by IS NOT NULL
                """,
                (task["workflow_id"],),
            ).mappings().fetchall()
            for reviewer in reviewers:
                members.setdefault(str(reviewer["reviewed_by"]), "project_owner")
            for user_id, project_role in members.items():
                existing = connection.exec_driver_sql(
                    "SELECT * FROM project_members WHERE project_id = %s "
                    "AND user_id = %s AND project_role = %s",
                    (project_id, user_id, project_role),
                ).mappings().fetchone()
                if existing is not None:
                    continue
                membership_id = str(uuid.uuid4())
                connection.exec_driver_sql(
                    """
                    INSERT INTO project_members
                        (id, project_id, user_id, project_role, status, added_by)
                    VALUES (%s, %s, %s, %s, 'active', %s)
                    """,
                    (
                        membership_id,
                        project_id,
                        user_id,
                        project_role,
                        SYSTEM_ACTOR.user_id,
                    ),
                )
                member = connection.exec_driver_sql(
                    "SELECT * FROM project_members WHERE id = %s",
                    (membership_id,),
                ).mappings().fetchone()
                assert member is not None
                self._record_project_member_event(
                    connection,
                    project_id,
                    membership_id,
                    "project_member.add",
                    SYSTEM_ACTOR.user_id,
                    None,
                    dict(member),
                )
            return self._require_task(connection, task_id)

    def list_task_job_ids(self, task_id: str) -> list[str]:
        with self.identity_repository.connect() as connection:
            self._require_task(connection, task_id)
            rows = connection.exec_driver_sql(
                "SELECT job_id FROM workflow_task_jobs "
                "WHERE task_id = %s ORDER BY created_at, job_id",
                (task_id,),
            ).mappings().fetchall()
        return [str(row["job_id"]) for row in rows]

    def get_submission(
        self,
        submission_id: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        with self.identity_repository.connection_scope(connection) as active_connection:
            row = active_connection.exec_driver_sql(
                "SELECT * FROM workflow_submissions WHERE id = %s",
                (submission_id,),
            ).mappings().fetchone()
        return self._submission_dict(row) if row is not None else None

    def list_task_submissions(self, task_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            self._require_task(connection, task_id)
            rows = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_submissions
                WHERE task_id = %s ORDER BY version, created_at, id
                """,
                (task_id,),
            ).mappings().fetchall()
        return [self._submission_dict(row) for row in rows]

    def get_latest_resource_submission(
        self,
        resource_type: str,
        resource_id: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any] | None:
        with self.identity_repository.connection_scope(connection) as active_connection:
            row = active_connection.exec_driver_sql(
                """
                SELECT * FROM workflow_submissions
                WHERE resource_type = %s AND resource_id = %s
                ORDER BY version DESC, created_at DESC, id DESC LIMIT 1
                """,
                (resource_type, resource_id),
            ).mappings().fetchone()
        return self._submission_dict(row) if row is not None else None

    def list_approved_submissions(
        self, resource_type: str
    ) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_submissions
                WHERE resource_type = %s AND status = 'approved'
                ORDER BY reviewed_at DESC, id DESC
                """,
                (resource_type,),
            ).mappings().fetchall()
        return [self._submission_dict(row) for row in rows]

    def update_submission_resource_id(
        self,
        submission_id: str,
        resource_id: str,
        *,
        connection: Connection | None = None,
    ) -> dict[str, Any]:
        with self.identity_repository.connection_scope(connection) as active_connection:
            submission = self.get_submission(
                submission_id, connection=active_connection
            )
            if submission is None:
                raise ValueError("only a draft registry entry may change resource")
            self._lock_task_project_write(
                active_connection, str(submission["task_id"])
            )
            cursor = active_connection.exec_driver_sql(
                "UPDATE workflow_submissions SET resource_id = %s, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = %s AND status = 'draft'",
                (resource_id, submission_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("only a draft registry entry may change resource")
            updated = self.get_submission(submission_id, connection=active_connection)
        assert updated is not None
        return updated

    def record_mirror_error(
        self, task_id: str, submission: dict[str, Any], message: str
    ) -> None:
        with self.identity_repository.transaction() as connection:
            self._lock_task_project_write(connection, task_id)
            self._ensure_system_actor(connection, SYSTEM_ACTOR.user_id)
            self._record_event(
                connection,
                task_id,
                "creative.mirror_error",
                SYSTEM_ACTOR.user_id,
                None,
                None,
                {"submission_id": submission["id"], "error": message},
            )

    def record_legacy_force(
        self,
        task_id: str,
        actor: Principal,
        resource_type: str,
        resource_id: str,
    ) -> None:
        with self.identity_repository.transaction() as connection:
            task = self._lock_task_project_write(connection, task_id)
            self._record_event(
                connection,
                task_id,
                "creative.legacy_force_lock",
                actor.user_id,
                task["status"],
                task["status"],
                {"resource_type": resource_type, "resource_id": resource_id},
            )

    def supersede_resource_approvals(
        self,
        resource_type: str,
        resource_id: str,
        actor: Principal,
    ) -> None:
        with self.identity_repository.transaction() as connection:
            rows = connection.exec_driver_sql(
                "SELECT * FROM workflow_submissions WHERE resource_type = %s "
                "AND resource_id = %s AND status = 'approved'",
                (resource_type, resource_id),
            ).mappings().fetchall()
            project_ids = set()
            for row in rows:
                task = self._require_task(connection, str(row["task_id"]))
                if task.get("project_id") is not None:
                    project_ids.add(str(task["project_id"]))
            for project_id in sorted(project_ids):
                self._lock_project_write(connection, project_id)
            for row in rows:
                before = self._submission_dict(row)
                connection.exec_driver_sql(
                    "UPDATE workflow_submissions SET status = 'superseded', updated_at = %s WHERE id = %s",
                    (time.time(), before["id"]),
                )
                after = self.get_submission(before["id"], connection=connection)
                self._record_event(
                    connection,
                    before["task_id"],
                    "submission.supersede",
                    actor.user_id,
                    "approved",
                    "superseded",
                    {"before": before, "after": after},
                )

    def create_next_task(
        self,
        predecessor_task_id: str,
        task_type: str,
        role_code: str,
        *,
        actor: Principal | dict[str, Any],
        connection: Connection | None = None,
        writer_operation_id: str | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        transaction = (
            nullcontext(connection)
            if connection is not None
            else self.identity_repository.transaction()
        )
        with transaction as active_connection:
            predecessor = self._require_task(
                active_connection, predecessor_task_id
            )
            if predecessor.get("project_id") is not None:
                self._lock_project_write(
                    active_connection, str(predecessor["project_id"])
                )
                predecessor = self._require_task(
                    active_connection, predecessor_task_id
                )
            existing = active_connection.exec_driver_sql(
                """
                SELECT * FROM workflow_tasks
                WHERE workflow_id = %s AND task_type = %s
                  AND predecessor_task_id = %s
                """,
                (
                    predecessor["workflow_id"],
                    task_type,
                    predecessor_task_id,
                ),
            ).mappings().fetchone()
            if existing is not None:
                return self._task_dict(existing)
            self._ensure_system_actor(active_connection, actor_id)
            role = active_connection.exec_driver_sql(
                "SELECT 1 FROM roles WHERE code = %s  AND is_enabled = 1",
                (role_code,),
            ).mappings().fetchone()
            if role is None:
                raise ValueError(f"unknown or disabled role: {role_code}")
            task_id = str(uuid.uuid4())
            active_connection.exec_driver_sql(
                """
                INSERT INTO workflow_tasks
                    (id, workflow_id, task_type, role_code, topic_id, project_id,
                     assignee_id, assigned_by, predecessor_task_id, status, due_at)
                VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, 'pool', NULL)
                """,
                (
                    task_id,
                    predecessor["workflow_id"],
                    task_type,
                    role_code,
                    predecessor["topic_id"],
                    predecessor["project_id"],
                    predecessor_task_id,
                ),
            )
            self._record_event(
                active_connection,
                task_id,
                "create",
                actor_id,
                None,
                "pool",
                {
                    "role_code": role_code,
                    "predecessor_task_id": predecessor_task_id,
                    **(
                        {"writer_operation_id": writer_operation_id}
                        if writer_operation_id
                        else {}
                    ),
                },
            )
            task = self._require_task(active_connection, task_id)
        return task

    def transition_task_in_transaction(
        self,
        task_id: str,
        action: str,
        actor: Principal | dict[str, Any],
        *,
        connection: Connection,
        assignee_user_id: str | None = None,
        details: dict[str, Any] | None = None,
        event_type: str | None = None,
        authorization_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        task = self._lock_task_project_write(connection, task_id)
        live_actor = self._live_actor(connection, actor_id)
        transition_actor = dict(live_actor)
        transition_actor["can_edit_project_task"] = (
            self._can_edit_project_task(connection, task, live_actor)
        )
        transition_actor.update(authorization_context or {})
        to_status = self.state_machine.transition(task, action, transition_actor)
        if assignee_user_id is None:
            cursor = connection.exec_driver_sql(
                """
                UPDATE workflow_tasks SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = %s
                """,
                (to_status, task_id, task["status"]),
            )
        else:
            self._live_actor(connection, assignee_user_id)
            cursor = connection.exec_driver_sql(
                """
                UPDATE workflow_tasks
                SET status = %s, assignee_id = %s, assigned_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = %s
                """,
                (to_status, assignee_user_id, actor_id, task_id, task["status"]),
            )
        if cursor.rowcount != 1:
            raise ValueError("task state changed concurrently")
        transitioned = self._require_task(connection, task_id)
        event_details = dict(details or {})
        event_details["task_before"] = task
        event_details["task_after"] = transitioned
        self._record_event(
            connection,
            task_id,
            event_type or action,
            actor_id,
            task["status"],
            to_status,
            event_details,
        )
        return transitioned

    def record_task_event_in_transaction(
        self,
        task_id: str,
        event_type: str,
        actor: Principal | dict[str, Any],
        *,
        connection: Connection,
        details: dict[str, Any],
    ) -> None:
        actor_id = self._actor_id(actor)
        self._live_actor(connection, actor_id)
        task = self._lock_task_project_write(connection, task_id)
        event_details = dict(details)
        event_details["task_before"] = task
        event_details["task_after"] = task
        self._record_event(
            connection,
            task_id,
            event_type,
            actor_id,
            task["status"],
            task["status"],
            event_details,
        )

    def list_tasks(
        self, actor: Principal | dict[str, Any]
    ) -> list[dict[str, Any]]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            if live_actor["is_system_admin"]:
                rows = connection.exec_driver_sql(
                    "SELECT * FROM workflow_tasks ORDER BY created_at, id"
                ).mappings().fetchall()
            else:
                role_codes = live_actor["role_codes"]
                is_global_project_owner = "project_owner" in role_codes
                managed_projects = self._managed_project_ids(connection, actor_id)
                assigned_projects = self._assigned_project_ids(connection, actor_id)
                rows = connection.exec_driver_sql(
                    "SELECT * FROM workflow_tasks ORDER BY created_at, id"
                ).mappings().fetchall()
                established_projects = {
                    str(row["project_id"])
                    for row in connection.exec_driver_sql(
                        "SELECT DISTINCT project_id FROM project_members "
                        "WHERE status = 'active'"
                    ).mappings()
                }
                active_stage_keys = {
                    (str(row["project_id"]), str(row["task_type"]))
                    for row in rows
                    if row["project_id"] is not None
                    and row["status"] in ACTIVE_TASK_STATUSES
                    and row["assignee_id"] is not None
                }
                can_claim_established_writing = (
                    ESTABLISHED_WRITING_PERMISSION in live_actor["permissions"]
                )
                rows = [
                    row
                    for row in rows
                    if (
                        is_global_project_owner and row["project_id"] is not None
                    )
                    or (
                        row["project_id"] is not None
                        and row["project_id"] in assigned_projects
                    )
                    or row["assignee_id"] == actor_id
                    or (
                        row["status"] == "pool"
                        and (
                            row["role_code"] in role_codes
                            or row["task_type"] in live_actor["claimable_task_types"]
                        )
                        and (
                            row["project_id"] is None
                            or (
                                row["role_code"] == "project_owner"
                                and row["project_id"] in managed_projects
                            )
                            or row["project_id"] in assigned_projects
                            or (
                                can_claim_established_writing
                                and str(row["project_id"]) in established_projects
                                and row["task_type"] in ESTABLISHED_WRITING_TASK_TYPES
                                and (
                                    str(row["project_id"]),
                                    str(row["task_type"]),
                                )
                                not in active_stage_keys
                            )
                        )
                    )
                    or (
                        row["project_id"] is not None
                        and row["project_id"] in managed_projects
                    )
                ]
        return [self._task_dict(row) for row in rows]

    def get_visible_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any] | None:
        return next(
            (task for task in self.list_tasks(actor) if task["id"] == task_id), None
        )

    def list_my_submissions(
        self, actor: Principal | dict[str, Any]
    ) -> list[dict[str, Any]]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT s.*, t.task_type, t.project_id, t.role_code
                FROM workflow_submissions s
                JOIN workflow_tasks t ON s.task_id = t.id
                WHERE s.created_by = %s
                ORDER BY s.created_at DESC
                """,
                (actor_id,),
            ).mappings().fetchall()
        return [self._submission_dict(row) for row in rows]

    def can_claim_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                task = self._require_task(connection, task_id)
                live_actor = self._live_actor(connection, actor_id)
            except (KeyError, PermissionError):
                return False
            if task["status"] != "pool":
                return False
            if live_actor["is_system_admin"]:
                return True
            can_edit_project_task = self._can_edit_project_task(
                connection, task, live_actor
            )
            if (
                not can_edit_project_task
                and task["role_code"] not in live_actor["role_codes"]
                and task["task_type"] not in live_actor["claimable_task_types"]
            ):
                return False
            if (
                task["project_id"] is not None
                and task["task_type"] in ESTABLISHED_WRITING_TASK_TYPES
                and self._writing_stage_has_active_task(connection, task)
            ):
                return False
            return (
                task["project_id"] is None
                or self._is_active_project_member(
                    connection, task["project_id"], actor_id
                )
                or self._can_claim_established_writing_stage(
                    connection, task, live_actor
                )
            )

    def can_assign_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                task = self._require_task(connection, task_id)
                live_actor = self._live_actor(connection, actor_id)
                self._assert_task_manager(connection, task, live_actor)
            except (KeyError, PermissionError):
                return False
        return task["status"] == "pool"

    def claim_task(
        self,
        task_id: str,
        actor: Principal | dict[str, Any],
        *,
        writer_operation_id: str | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._lock_task_project_write(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            if (
                task["project_id"] is not None
                and task["task_type"] in ESTABLISHED_WRITING_TASK_TYPES
                and not live_actor["is_system_admin"]
            ):
                self._lock_established_project(connection, task["project_id"])
                if self._writing_stage_has_active_task(connection, task):
                    raise PermissionError("writing stage is already being handled")
            can_claim_established_writing = (
                task["project_id"] is not None
                and self._can_claim_established_writing_stage(
                    connection, task, live_actor
                )
            )
            if (
                task["role_code"] == "project_owner"
                and not live_actor["is_system_admin"]
            ):
                self._assert_task_manager(connection, task, live_actor)
            elif (
                task["project_id"] is not None
                and not live_actor["is_system_admin"]
                and not self._is_active_project_member(
                    connection, task["project_id"], actor_id
                )
                and not can_claim_established_writing
            ):
                raise PermissionError("actor must be an active project member")
            transition_actor = dict(live_actor)
            transition_actor["can_edit_project_task"] = (
                self._can_edit_project_task(connection, task, live_actor)
            )
            to_status = self.state_machine.transition(
                task, "claim", transition_actor
            )
            cursor = connection.exec_driver_sql(
                """
                UPDATE workflow_tasks
                SET assignee_id = %s, assigned_by = %s, status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = 'pool' AND assignee_id IS NULL
                """,
                (actor_id, actor_id, to_status, task_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("task has already been claimed")
            if can_claim_established_writing:
                self._ensure_writer_membership_for_claim(
                    connection,
                    task["project_id"],
                    actor_id,
                    writer_operation_id=writer_operation_id,
                )
            self._record_event(
                connection,
                task_id,
                "claim",
                actor_id,
                task["status"],
                to_status,
                {
                    "assignee_user_id": actor_id,
                    **(
                        {"writer_operation_id": writer_operation_id}
                        if writer_operation_id
                        else {}
                    ),
                },
            )
            claimed = self._require_task(connection, task_id)
        return claimed

    def assign_task(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        return self._assign(task_id, assignee_user_id, actor, "assign")

    def reassign_task(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
    ) -> dict[str, Any]:
        return self._assign(task_id, assignee_user_id, actor, "reassign")

    def start_task(
        self,
        task_id: str,
        actor: Principal | dict[str, Any],
        *,
        writer_operation_id: str | None = None,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._lock_task_project_write(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            transition_actor = dict(live_actor)
            transition_actor["can_edit_project_task"] = (
                self._can_edit_project_task(connection, task, live_actor)
            )
            to_status = self.state_machine.transition(
                task, "start", transition_actor
            )
            connection.exec_driver_sql(
                """
                UPDATE workflow_tasks SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = %s
                """,
                (to_status, task_id, task["status"]),
            )
            self._record_event(
                connection,
                task_id,
                "start",
                actor_id,
                task["status"],
                to_status,
                (
                    {"writer_operation_id": writer_operation_id}
                    if writer_operation_id
                    else None
                ),
            )
            started = self._require_task(connection, task_id)
        return started

    def cancel_task(
        self, task_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._lock_task_project_write(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            self._assert_task_manager(connection, task, live_actor)
            to_status = self.state_machine.transition(task, "cancel", live_actor)
            connection.exec_driver_sql(
                """
                UPDATE workflow_tasks SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND status = %s
                """,
                (to_status, task_id, task["status"]),
            )
            self._record_event(
                connection,
                task_id,
                "cancel",
                actor_id,
                task["status"],
                to_status,
            )
            cancelled = self._require_task(connection, task_id)
        return cancelled

    def list_role_assignment_candidates(
        self,
        role_code: str,
        task_type: str,
        actor: Principal | dict[str, Any],
        *,
        required_permission: str = "workflow.assign",
    ) -> list[dict[str, Any]]:
        normalized_role = role_code.strip()
        normalized_task = task_type.strip()
        if not normalized_role or not normalized_task:
            raise ValueError("role_code and task_type are required")
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            if (
                not live_actor["is_system_admin"]
                and required_permission not in live_actor["permissions"]
            ):
                raise PermissionError(
                    f"{required_permission} permission is required"
                )
            rows = connection.exec_driver_sql(
                """
                SELECT DISTINCT u.id, u.username, u.display_name
                FROM users AS u
                JOIN user_roles AS ur ON ur.user_id = u.id
                JOIN roles AS r ON r.id = ur.role_id
                LEFT JOIN role_claimable_task_types AS rct ON rct.role_id = r.id
                WHERE u.status = 'active' AND u.is_deleted = 0
                  AND r.is_enabled = 1
                  AND (r.code = %s OR rct.task_type = %s)
                ORDER BY u.display_name , u.username , u.id
                """,
                (normalized_role, normalized_task),
            ).mappings().fetchall()
            candidates = []
            for row in rows:
                candidate = self._live_actor(connection, str(row["id"]))
                if (
                    not candidate["is_system_admin"]
                    and normalized_role not in candidate["role_codes"]
                    and normalized_task not in candidate["claimable_task_types"]
                ):
                    continue
                candidates.append(
                    {
                        "user_id": str(row["id"]),
                        "username": str(row["username"]),
                        "display_name": str(row["display_name"]),
                        "role_codes": sorted(candidate["role_codes"]),
                        "claimable_task_types": sorted(candidate["claimable_task_types"]),
                    }
                )
        return candidates

    def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_task_events
                WHERE task_id = %s ORDER BY created_at, id
                """,
                (task_id,),
            ).mappings().fetchall()
        return [self._event_dict(row) for row in rows]

    def add_project_member(
        self,
        project_id: str,
        user_id: str,
        project_role: str,
        actor: Principal | dict[str, Any],
        *,
        status: str = "active",
        allow_creator_bootstrap: bool = False,
    ) -> dict[str, Any]:
        if status not in {"active", "inactive"}:
            raise ValueError("project member status must be active or inactive")
        if not project_role.strip():
            raise ValueError("project role is required")
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            live_actor = self._live_actor(connection, actor_id)
            has_members = connection.exec_driver_sql(
                "SELECT 1 FROM project_members WHERE project_id = %s LIMIT 1",
                (project_id,),
            ).mappings().fetchone()
            is_global_owner_bootstrap = (
                has_members is None
                and actor_id == user_id
                and project_role in OWNER_PROJECT_ROLES
                and "project_owner" in live_actor["role_codes"]
            )
            is_creator_bootstrap = (
                allow_creator_bootstrap
                and actor_id == user_id
                and project_role == PROJECT_CREATOR_ROLE
                and "project.create" in live_actor["permissions"]
            )
            if not (is_global_owner_bootstrap or is_creator_bootstrap):
                self._assert_project_manager(connection, project_id, live_actor)
            user = connection.exec_driver_sql(
                """
                SELECT 1 FROM users
                WHERE id = %s AND status = 'active' AND is_deleted = 0
                """,
                (user_id,),
            ).mappings().fetchone()
            if user is None:
                raise ValueError("project member user does not exist or is inactive")
            existing = connection.exec_driver_sql(
                """
                SELECT * FROM project_members
                WHERE project_id = %s AND user_id = %s AND project_role = %s
                """,
                (project_id, user_id, project_role),
            ).mappings().fetchone()
            membership_id = existing["id"] if existing else str(uuid.uuid4())
            connection.exec_driver_sql(
                """
                INSERT INTO project_members
                    (id, project_id, user_id, project_role, status, added_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(project_id, user_id, project_role) DO UPDATE SET
                    status = excluded.status,
                    added_by = excluded.added_by,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    membership_id,
                    project_id,
                    user_id,
                    project_role,
                    status,
                    actor_id,
                ),
            )
            member = connection.exec_driver_sql(
                "SELECT * FROM project_members WHERE id = %s", (membership_id,)
            ).mappings().fetchone()
            assert member is not None
            self._record_project_member_event(
                connection,
                project_id,
                membership_id,
                "project_member.update" if existing else "project_member.add",
                actor_id,
                dict(existing) if existing else None,
                dict(member),
            )
        return dict(member)

    def ensure_project_creator(
        self, project_id: str, creator_user_id: str, actor: Principal | dict[str, Any]
    ) -> dict[str, Any] | None:
        """Create a missing creator membership without reviving an inactive one."""
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            live_actor = self._live_actor(connection, actor_id)
            if actor_id != creator_user_id or not (
                live_actor["is_system_admin"]
                or "project.create" in live_actor["permissions"]
                or "*" in live_actor["permissions"]
            ):
                return None
            membership_id = str(uuid.uuid4())
            inserted = connection.exec_driver_sql(
                """
                INSERT INTO project_members
                    (id, project_id, user_id, project_role, status, added_by)
                VALUES (%s, %s, %s, %s, 'active', %s)
                ON CONFLICT(project_id, user_id, project_role) DO NOTHING
                RETURNING *
                """,
                (
                    membership_id,
                    project_id,
                    creator_user_id,
                    PROJECT_CREATOR_ROLE,
                    actor_id,
                ),
            ).mappings().fetchone()
            member = connection.exec_driver_sql(
                "SELECT * FROM project_members WHERE project_id = %s AND user_id = %s "
                "AND project_role = %s",
                (project_id, creator_user_id, PROJECT_CREATOR_ROLE),
            ).mappings().fetchone()
            assert member is not None
            if inserted is not None:
                self._record_project_member_event(
                    connection,
                    project_id,
                    membership_id,
                    "project_member.add",
                    actor_id,
                    None,
                    dict(member),
                )
            return dict(member)

    def migrate_creator_membership(self, project_id: str, created_by: str) -> bool:
        """Only rename the creator's local project_owner membership."""
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            creator = connection.exec_driver_sql(
                "SELECT * FROM project_members WHERE project_id = %s AND user_id = %s "
                "AND project_role = %s",
                (project_id, created_by, PROJECT_CREATOR_ROLE),
            ).mappings().fetchone()
            legacy = connection.exec_driver_sql(
                "SELECT * FROM project_members WHERE project_id = %s AND user_id = %s "
                "AND project_role = 'project_owner'",
                (project_id, created_by),
            ).mappings().fetchone()
            if legacy is None:
                return False
            if creator is None:
                connection.exec_driver_sql(
                    "UPDATE project_members SET project_role = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (PROJECT_CREATOR_ROLE, legacy["id"]),
                )
            else:
                connection.exec_driver_sql("DELETE FROM project_members WHERE id = %s", (legacy["id"],))
        return True

    def list_project_members(self, project_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT * FROM project_members
                WHERE project_id = %s ORDER BY created_at, id
                """,
                (project_id,),
            ).mappings().fetchall()
        return [dict(row) for row in rows]

    def list_project_member_events(self, project_id: str) -> list[dict[str, Any]]:
        with self.identity_repository.connect() as connection:
            rows = connection.exec_driver_sql(
                """
                SELECT * FROM workflow_project_member_events
                WHERE project_id = %s ORDER BY created_at, id
                """,
                (project_id,),
            ).mappings().fetchall()
        return [self._project_member_event_dict(row) for row in rows]

    def assert_can_manage_project(
        self, project_id: str, actor: Principal | dict[str, Any]
    ) -> None:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            self._assert_project_manager(connection, project_id, live_actor)

    def list_managed_project_ids(self, actor: Principal | dict[str, Any]) -> set[str]:
        """Return the active projects the current project owner may manage."""

        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            live_actor = self._live_actor(connection, actor_id)
            if live_actor["is_system_admin"]:
                return set()
            if "project_owner" not in live_actor["role_codes"]:
                return set()
            return self._managed_project_ids(connection, actor_id)

    def can_review_task(
        self,
        task_id: str,
        actor: Principal | dict[str, Any],
        permission: str,
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                live_actor = self._live_actor(connection, actor_id)
                task = self._require_task(connection, task_id)
                if live_actor["is_system_admin"]:
                    return True
                if permission not in live_actor["permissions"]:
                    return False
                if live_actor["permission_scopes"].get(permission) not in {
                    "managed_projects",
                    "all",
                }:
                    return False
                project_id = task.get("project_id")
                if project_id is None:
                    return bool(
                        task.get("topic_id") is not None
                        and "project.manage" in live_actor["permissions"]
                    )
                self._assert_managed_project_membership(
                    connection, project_id, live_actor
                )
            except (KeyError, PermissionError):
                return False
        return True

    def remove_project_member(
        self,
        project_id: str,
        membership_id: str,
        actor: Principal | dict[str, Any],
    ) -> None:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            self._lock_project_write(connection, str(project_id))
            live_actor = self._live_actor(connection, actor_id)
            self._assert_project_manager(connection, project_id, live_actor)
            members = connection.exec_driver_sql(
                """
                SELECT * FROM project_members
                WHERE project_id = %s AND user_id = %s ORDER BY id
                """,
                (project_id, membership_id),
            ).mappings().fetchall()
            if not members:
                member = connection.exec_driver_sql(
                    """
                    SELECT * FROM project_members WHERE project_id = %s AND id = %s
                    """,
                    (project_id, membership_id),
                ).mappings().fetchone()
                members = [member] if member is not None else []
            if not members:
                raise KeyError(membership_id)
            removed_user_id = members[0]["user_id"]
            deactivates_creator = any(
                member["project_role"] == PROJECT_CREATOR_ROLE
                for member in members
            )
            if deactivates_creator:
                if removed_user_id == actor_id:
                    raise PermissionError("project creator cannot deactivate themselves")
                members = connection.exec_driver_sql(
                    """
                    SELECT * FROM project_members
                    WHERE project_id = %s AND user_id = %s ORDER BY id
                    """,
                    (project_id, removed_user_id),
                ).mappings().fetchall()
            removes_active_owner = any(
                member["status"] == "active"
                and member["project_role"] in OWNER_PROJECT_ROLES
                for member in members
            )
            if removes_active_owner and not (
                live_actor["is_system_admin"]
                or "project_owner" in live_actor["role_codes"]
            ):
                other_roles = connection.exec_driver_sql(
                    """
                    SELECT project_role FROM project_members
                    WHERE project_id = %s AND user_id <> %s AND status = 'active'
                    """,
                    (project_id, removed_user_id),
                ).mappings().fetchall()
                if not any(
                    row["project_role"] in OWNER_PROJECT_ROLES
                    for row in other_roles
                ):
                    raise ValueError(
                        "current project owner cannot be removed until another owner is assigned"
                    )
            for member in members:
                if deactivates_creator:
                    if member["status"] == "inactive":
                        continue
                    connection.exec_driver_sql(
                        "UPDATE project_members SET status = 'inactive', updated_at = CURRENT_TIMESTAMP "
                        "WHERE id = %s",
                        (member["id"],),
                    )
                    disabled = connection.exec_driver_sql(
                        "SELECT * FROM project_members WHERE id = %s", (member["id"],)
                    ).mappings().fetchone()
                    self._record_project_member_event(
                        connection,
                        project_id,
                        member["id"],
                        "project_member.disable",
                        actor_id,
                        dict(member),
                        dict(disabled) if disabled is not None else None,
                    )
                    continue
                connection.exec_driver_sql(
                    "DELETE FROM project_members WHERE id = %s", (member["id"],)
                )
                self._record_project_member_event(
                    connection,
                    project_id,
                    member["id"],
                    "project_member.remove",
                    actor_id,
                    dict(member),
                    None,
                )

    def _assign(
        self,
        task_id: str,
        assignee_user_id: str,
        actor: Principal | dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        actor_id = self._actor_id(actor)
        with self.identity_repository.transaction() as connection:
            task = self._lock_task_project_write(connection, task_id)
            live_actor = self._live_actor(connection, actor_id)
            self._assert_task_manager(connection, task, live_actor)
            to_status = self.state_machine.transition(task, action, live_actor)
            assignee = self._live_actor(connection, assignee_user_id)
            if (
                not assignee["is_system_admin"]
                and task["role_code"] not in assignee["role_codes"]
                and task["task_type"] not in assignee["claimable_task_types"]
            ):
                raise ValueError("assignee is not live-eligible for this task type")
            if task["project_id"] is not None:
                membership = connection.exec_driver_sql(
                    """
                    SELECT 1 FROM project_members
                    WHERE project_id = %s AND user_id = %s AND status = 'active'
                    """,
                    (task["project_id"], assignee_user_id),
                ).mappings().fetchone()
                if membership is None:
                    raise ValueError("assignee must be an active project member")
            if action == "assign":
                cursor = connection.exec_driver_sql(
                    """
                    UPDATE workflow_tasks
                    SET assignee_id = %s, assigned_by = %s, status = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = 'pool' AND assignee_id IS NULL
                    """,
                    (assignee_user_id, actor_id, to_status, task_id),
                )
            else:
                cursor = connection.exec_driver_sql(
                    """
                    UPDATE workflow_tasks
                    SET assignee_id = %s, assigned_by = %s, status = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND status = %s
                    """,
                    (
                        assignee_user_id,
                        actor_id,
                        to_status,
                        task_id,
                        task["status"],
                    ),
                )
            if cursor.rowcount != 1:
                raise ValueError("task assignment changed concurrently")
            self._record_event(
                connection,
                task_id,
                action,
                actor_id,
                task["status"],
                to_status,
                {
                    "before_assignee_user_id": task["assignee_user_id"],
                    "assignee_user_id": assignee_user_id,
                },
            )
            assigned = self._require_task(connection, task_id)
        return assigned

    def _assert_task_manager(
        self,
        connection: Connection,
        task: dict[str, Any],
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"]:
            return
        project_id = task.get("project_id")
        if project_id is None:
            raise PermissionError("only a system administrator may manage unscoped tasks")
        self._assert_project_manager(connection, project_id, actor)

    @staticmethod
    def _assert_project_manager(
        connection: Connection,
        project_id: str,
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"]:
            return
        if "project_owner" in actor["role_codes"]:
            return
        WorkflowRepository._assert_managed_project_membership(
            connection, project_id, actor
        )

    @staticmethod
    def _assert_managed_project_membership(
        connection: Connection,
        project_id: str,
        actor: dict[str, Any],
    ) -> None:
        if actor["is_system_admin"] or "project_owner" in actor["role_codes"]:
            return
        member = connection.exec_driver_sql(
            """
            SELECT 1 FROM project_members
            WHERE project_id = %s AND user_id = %s AND status = 'active'
              AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
            """,
            (project_id, actor["user_id"]),
        ).mappings().fetchone()
        if member is None:
            raise PermissionError("actor does not manage this project")

    @staticmethod
    def _managed_project_ids(
        connection: Connection, actor_id: str
    ) -> set[str]:
        return {
            row["project_id"]
            for row in connection.exec_driver_sql(
                """
                SELECT DISTINCT project_id FROM project_members
                WHERE user_id = %s AND status = 'active'
                  AND project_role IN ('owner', 'project_owner', 'manager', 'project_manager')
                """,
                (actor_id,),
            ).mappings()
        }

    @staticmethod
    def _assigned_project_ids(
        connection: Connection, actor_id: str
    ) -> set[str]:
        return {
            row["project_id"]
            for row in connection.exec_driver_sql(
                """
                SELECT DISTINCT project_id FROM project_members
                WHERE user_id = %s AND status = 'active'
                """,
                (actor_id,),
            ).mappings()
        }

    @staticmethod
    def _is_active_project_member(
        connection: Connection, project_id: str, actor_id: str
    ) -> bool:
        return (
            connection.exec_driver_sql(
                """
                SELECT 1 FROM project_members
                WHERE project_id = %s AND user_id = %s AND status = 'active'
                LIMIT 1
                """,
                (project_id, actor_id),
            ).mappings().fetchone()
            is not None
        )

    def is_active_project_member(
        self, project_id: str, actor: Principal | dict[str, Any]
    ) -> bool:
        actor_id = self._actor_id(actor)
        with self.identity_repository.connect() as connection:
            try:
                self._live_actor(connection, actor_id)
            except PermissionError:
                return False
            return self._is_active_project_member(
                connection, project_id, actor_id
            )

    @classmethod
    def _can_edit_project_task(
        cls,
        connection: Connection,
        task: dict[str, Any],
        actor: dict[str, Any],
    ) -> bool:
        project_id = task.get("project_id")
        return bool(
            project_id is not None
            and cls._is_active_project_member(
                connection, str(project_id), str(actor["user_id"])
            )
        )

    @staticmethod
    def _writing_stage_has_active_task(
        connection: Connection, task: dict[str, Any]
    ) -> bool:
        """Return whether another writer already owns this project stage."""

        project_id = task.get("project_id")
        task_type = task.get("task_type")
        if project_id is None or task_type not in ESTABLISHED_WRITING_TASK_TYPES:
            return False
        return (
            connection.exec_driver_sql(
                """
                SELECT 1 FROM workflow_tasks
                WHERE project_id = %s AND task_type = %s AND id <> %s
                  AND status IN ('assigned', 'in_progress', 'returned')
                  AND assignee_id IS NOT NULL
                LIMIT 1
                """,
                (project_id, task_type, task["id"]),
            ).mappings().fetchone()
            is not None
        )

    @staticmethod
    def _can_claim_established_writing_stage(
        connection: Connection,
        task: dict[str, Any],
        actor: dict[str, Any],
    ) -> bool:
        project_id = task.get("project_id")
        if (
            project_id is None
            or task.get("task_type") not in ESTABLISHED_WRITING_TASK_TYPES
            or ESTABLISHED_WRITING_PERMISSION not in actor["permissions"]
        ):
            return False
        return (
            connection.exec_driver_sql(
                """
                SELECT 1 FROM project_members
                WHERE project_id = %s AND status = 'active'
                LIMIT 1
                """,
                (project_id,),
            ).mappings().fetchone()
            is not None
        )

    @staticmethod
    def _lock_established_project(connection: Connection, project_id: str) -> None:
        """Serialize stage claims by locking this established project's members."""

        connection.exec_driver_sql(
            """
            SELECT id FROM project_members
            WHERE project_id = %s AND status = 'active'
            ORDER BY id
            FOR UPDATE
            """,
            (project_id,),
        ).mappings().fetchall()

    def _ensure_writer_membership_for_claim(
        self,
        connection: Connection,
        project_id: str,
        actor_id: str,
        *,
        writer_operation_id: str | None = None,
    ) -> None:
        existing = connection.exec_driver_sql(
            """
            SELECT * FROM project_members
            WHERE project_id = %s AND user_id = %s
              AND project_role = 'screenwriter'
            FOR UPDATE
            """,
            (project_id, actor_id),
        ).mappings().fetchone()
        if existing is not None and existing["status"] == "active":
            return

        membership_id = str(existing["id"]) if existing else str(uuid.uuid4())
        connection.exec_driver_sql(
            """
            INSERT INTO project_members
                (id, project_id, user_id, project_role, status, added_by)
            VALUES (%s, %s, %s, 'screenwriter', 'active', %s)
            ON CONFLICT(project_id, user_id, project_role) DO UPDATE SET
                status = 'active',
                added_by = excluded.added_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (membership_id, project_id, actor_id, actor_id),
        )
        member = connection.exec_driver_sql(
            "SELECT * FROM project_members WHERE id = %s", (membership_id,)
        ).mappings().fetchone()
        assert member is not None
        self._record_project_member_event(
            connection,
            project_id,
            membership_id,
            "project_member.update" if existing else "project_member.add",
            actor_id,
            dict(existing) if existing else None,
            dict(member),
            writer_operation_id=writer_operation_id,
        )

    @staticmethod
    def _live_actor(connection: Connection, user_id: str) -> dict[str, Any]:
        user = connection.exec_driver_sql(
            """
            SELECT id FROM users
            WHERE id = %s AND status = 'active' AND is_deleted = 0
            """,
            (user_id,),
        ).mappings().fetchone()
        if user is None:
            raise PermissionError("actor or assignee is inactive or unknown")
        roles = {
            row["code"]
            for row in connection.exec_driver_sql(
                """
                SELECT r.code FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                WHERE ur.user_id = %s
                """,
                (user_id,),
            ).mappings()
        }
        permission_scopes: dict[str, str] = {}
        permission_rows = connection.exec_driver_sql(
            """
            SELECT rp.permission_code, rp.data_scope FROM user_roles AS ur
            JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
            JOIN role_permissions AS rp ON rp.role_id = r.id
            WHERE ur.user_id = %s
            """,
            (user_id,),
        ).mappings()
        for row in permission_rows:
            current_scope = permission_scopes.get(row["permission_code"])
            if current_scope is None or (
                DATA_SCOPE_ORDER[row["data_scope"]]
                > DATA_SCOPE_ORDER[current_scope]
            ):
                permission_scopes[row["permission_code"]] = row["data_scope"]
        if "system_admin" in roles:
            permission_scopes = {"*": "all"}
        claimable_task_types = {
            row["task_type"]
            for row in connection.exec_driver_sql(
                """
                SELECT rct.task_type FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id AND r.is_enabled = 1
                JOIN role_claimable_task_types AS rct ON rct.role_id = r.id
                WHERE ur.user_id = %s
                """,
                (user_id,),
            ).mappings()
        }
        return {
            "user_id": user_id,
            "role_codes": roles,
            "permissions": set(permission_scopes),
            "permission_scopes": permission_scopes,
            "is_system_admin": "system_admin" in roles,
            "claimable_task_types": claimable_task_types,
        }

    @staticmethod
    def _actor_id(actor: Principal | dict[str, Any]) -> str:
        if actor is None:
            raise ValueError("a non-null actor is required")
        if isinstance(actor, dict):
            actor_id = actor.get("user_id") or actor.get("id")
        else:
            actor_id = actor.user_id
        if not actor_id:
            raise PermissionError("an authenticated actor is required")
        return str(actor_id)

    @staticmethod
    def _ensure_system_actor(
        connection: Connection, actor_id: str
    ) -> None:
        if actor_id != SYSTEM_ACTOR.user_id:
            return
        connection.exec_driver_sql(
            """
            INSERT INTO users
                (id, username, normalized_username, display_name, password_hash,
                 status, is_deleted)
            VALUES (%s, %s, %s, %s, %s, 'disabled', 1)
            ON CONFLICT(id) DO NOTHING
            """,
            (
                SYSTEM_ACTOR.user_id,
                SYSTEM_ACTOR.username,
                SYSTEM_ACTOR.username,
                SYSTEM_ACTOR.display_name,
                "system-managed-account",
            ),
        )

    @staticmethod
    def _ensure_workflow(
        connection: Connection,
        workflow_id: str,
        task_type: str,
        project_id: str | None,
        topic_id: int | None,
        actor_id: str | None,
    ) -> None:
        resource_type = "project" if project_id else "topic" if topic_id is not None else "task"
        resource_id = project_id or (str(topic_id) if topic_id is not None else workflow_id)
        connection.exec_driver_sql(
            """
            INSERT INTO workflow_instances
                (id, workflow_type, resource_type, resource_id, project_id, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO NOTHING
            """,
            (workflow_id, task_type, resource_type, resource_id, project_id, actor_id),
        )

    @classmethod
    def _get_task(
        cls, connection: Connection, task_id: str
    ) -> dict[str, Any] | None:
        row = connection.exec_driver_sql(
            "SELECT * FROM workflow_tasks WHERE id = %s", (task_id,)
        ).mappings().fetchone()
        return cls._task_dict(row) if row is not None else None

    @classmethod
    def _require_task(
        cls, connection: Connection, task_id: str
    ) -> dict[str, Any]:
        task = cls._get_task(connection, task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    @staticmethod
    def _task_dict(row: Any) -> dict[str, Any]:
        task = dict(row)
        task["assignee_user_id"] = task.pop("assignee_id")
        task["assigned_by_user_id"] = task.pop("assigned_by")
        task["local_project_id"] = WorkflowRepository.local_project_id(
            task.get("project_id")
        )
        return task

    @staticmethod
    def local_project_id(project_id: Any) -> str | None:
        """Decode a creative scoped ID for local URLs without weakening DB scope."""

        if project_id is None:
            return None
        value = str(project_id)
        match = re.fullmatch(
            r"creative:[0-9a-fA-F-]{36}:project:([1-9][0-9]*)", value
        )
        return match.group(1) if match is not None else value

    @staticmethod
    def _submission_dict(row: Any) -> dict[str, Any]:
        submission = dict(row)
        snapshot = submission["content_snapshot_json"]
        submission["content_snapshot"] = (
            json.loads(snapshot) if isinstance(snapshot, str) else snapshot
        )
        return submission

    @staticmethod
    def _record_event(
        connection: Connection,
        task_id: str,
        event_type: str,
        actor_id: str | None,
        from_status: str | None,
        to_status: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.exec_driver_sql(
            """
            INSERT INTO workflow_task_events
                (id, task_id, event_type, actor_id, from_status, to_status, details_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                event_type,
                actor_id,
                from_status,
                to_status,
                json.dumps(
                    details or {},
                    default=WorkflowRepository._json_default,
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            ),
        )

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    @staticmethod
    def _record_project_member_event(
        connection: Connection,
        project_id: str,
        membership_id: str,
        event_type: str,
        actor_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        *,
        writer_operation_id: str | None = None,
    ) -> None:
        connection.exec_driver_sql(
            """
            INSERT INTO workflow_project_member_events
                (id, project_id, membership_id, event_type, actor_id,
                 before_json, after_json, writer_operation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                membership_id,
                event_type,
                actor_id,
                json.dumps(before, ensure_ascii=True, sort_keys=True)
                if before is not None
                else None,
                json.dumps(after, ensure_ascii=True, sort_keys=True)
                if after is not None
                else None,
                writer_operation_id,
            ),
        )

    @staticmethod
    def _event_dict(row: Any) -> dict[str, Any]:
        event = dict(row)
        event["actor_user_id"] = event.pop("actor_id")
        event["before_status"] = event.pop("from_status")
        event["after_status"] = event.pop("to_status")
        details_json = event.pop("details_json")
        event["details"] = (
            json.loads(details_json)
            if isinstance(details_json, str)
            else details_json
        )
        return event

    @staticmethod
    def _project_member_event_dict(row: Any) -> dict[str, Any]:
        event = dict(row)
        event["actor_user_id"] = event.pop("actor_id")
        before_json = event.pop("before_json")
        after_json = event.pop("after_json")
        event["before"] = (
            json.loads(before_json) if isinstance(before_json, str) else before_json
        )
        event["after"] = (
            json.loads(after_json) if isinstance(after_json, str) else after_json
        )
        return event
