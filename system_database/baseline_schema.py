# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)


BASELINE_METADATA = MetaData()

users = Table(
    "users",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("normalized_username", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("is_deleted", Integer, nullable=False, server_default=text("0")),
    Column("created_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("deleted_at", Text),
    Column("deleted_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("last_login_at", Text),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    CheckConstraint("is_deleted IN (0, 1)", name="ck_users_is_deleted"),
)

roles = Table(
    "roles",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("code", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=text("''")),
    Column("is_system", Integer, nullable=False, server_default=text("0")),
    Column("is_enabled", Integer, nullable=False, server_default=text("1")),
    Column("is_deleted", Integer, nullable=False, server_default=text("0")),
    Column("created_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("deleted_at", Text),
    Column("deleted_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("is_system IN (0, 1)", name="ck_roles_is_system"),
    CheckConstraint("is_enabled IN (0, 1)", name="ck_roles_is_enabled"),
    CheckConstraint("is_deleted IN (0, 1)", name="ck_roles_is_deleted"),
)

permissions = Table(
    "permissions",
    BASELINE_METADATA,
    Column("code", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

user_roles = Table(
    "user_roles",
    BASELINE_METADATA,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Text, ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

role_permissions = Table(
    "role_permissions",
    BASELINE_METADATA,
    Column("role_id", Text, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_code",
        Text,
        ForeignKey("permissions.code", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("data_scope", Text, nullable=False),
    CheckConstraint(
        "data_scope IN ('task', 'project', 'managed_projects', 'all')",
        name="ck_role_permissions_data_scope",
    ),
)

role_claimable_task_types = Table(
    "role_claimable_task_types",
    BASELINE_METADATA,
    Column("role_id", Text, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("task_type", Text, primary_key=True),
)

permission_dependencies = Table(
    "permission_dependencies",
    BASELINE_METADATA,
    Column(
        "permission_code",
        Text,
        ForeignKey("permissions.code", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "depends_on_code",
        Text,
        ForeignKey("permissions.code", ondelete="RESTRICT"),
        primary_key=True,
    ),
    CheckConstraint(
        "permission_code <> depends_on_code", name="ck_permission_dependencies_distinct"
    ),
)

sessions = Table(
    "sessions",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("csrf_token_hash", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("revoked_at", Text),
    Column("user_agent", Text),
    Column("ip_address", Text),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

project_members = Table(
    "project_members",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("user_id", Text, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("project_role", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("added_by", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    UniqueConstraint(
        "project_id", "user_id", "project_role", name="uq_project_members_role"
    ),
)

workflow_instances = Table(
    "workflow_instances",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("workflow_type", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text, nullable=False),
    Column("project_id", Text),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("created_by", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

workflow_tasks = Table(
    "workflow_tasks",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "workflow_id",
        Text,
        ForeignKey("workflow_instances.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("task_type", Text, nullable=False),
    Column("role_code", Text, nullable=False),
    Column("topic_id", Integer),
    Column("project_id", Text),
    Column("assignee_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("assigned_by", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column(
        "predecessor_task_id",
        Text,
        ForeignKey("workflow_tasks.id", ondelete="SET NULL"),
    ),
    Column("status", Text, nullable=False, server_default=text("'pending'")),
    Column("due_at", Text),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

workflow_task_jobs = Table(
    "workflow_task_jobs",
    BASELINE_METADATA,
    Column(
        "task_id",
        Text,
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("job_id", Text, primary_key=True),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

workflow_task_events = Table(
    "workflow_task_events",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "task_id",
        Text,
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("event_type", Text, nullable=False),
    Column("actor_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("from_status", Text),
    Column("to_status", Text),
    Column("details_json", Text, nullable=False, server_default=text("'{}'")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

workflow_submissions = Table(
    "workflow_submissions",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("task_id", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("parent_submission_id", Text),
    Column("status", Text, nullable=False),
    Column("content_snapshot_json", Text, nullable=False),
    Column("content_fingerprint", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("submitted_by", Text),
    Column("submitted_at", Float),
    Column("reviewed_by", Text),
    Column("reviewed_at", Float),
    Column("review_comment", Text, nullable=False, server_default=text("''")),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    CheckConstraint(
        "status IN ('draft', 'submitted', 'approved', 'returned', 'superseded')",
        name="ck_workflow_submissions_status",
    ),
    UniqueConstraint(
        "resource_type", "resource_id", "version", name="uq_workflow_submissions_version"
    ),
)

approval_records = Table(
    "approval_records",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("workflow_id", Text, ForeignKey("workflow_instances.id", ondelete="SET NULL")),
    Column("task_id", Text, ForeignKey("workflow_tasks.id", ondelete="SET NULL")),
    Column("version_id", Text, nullable=False),
    Column("stage", Text, nullable=False),
    Column("decision", Text, nullable=False),
    Column("reviewer_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("comment", Text),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

audit_logs = Table(
    "audit_logs",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("actor_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("actor_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column(
        "actor_role_snapshot_json", Text, nullable=False, server_default=text("'[]'")
    ),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("target_type", Text),
    Column("resource_id", Text),
    Column("target_id", Text),
    Column("project_id", Text),
    Column("reason", Text),
    Column("details_json", Text, nullable=False, server_default=text("'{}'")),
    Column("before_summary_json", Text),
    Column("after_summary_json", Text),
    Column("request_id", Text),
    Column("ip_address", Text),
    Column("user_agent", Text),
    Column("result", Text, nullable=False, server_default=text("'success'")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

workflow_project_member_events = Table(
    "workflow_project_member_events",
    BASELINE_METADATA,
    Column("id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("membership_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("actor_id", Text, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("before_json", Text),
    Column("after_json", Text),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

BASELINE_TABLE_NAMES = frozenset(
    {
        "approval_records",
        "audit_logs",
        "permission_dependencies",
        "permissions",
        "project_members",
        "role_claimable_task_types",
        "role_permissions",
        "roles",
        "sessions",
        "user_roles",
        "users",
        "workflow_instances",
        "workflow_project_member_events",
        "workflow_submissions",
        "workflow_task_events",
        "workflow_task_jobs",
        "workflow_tasks",
    }
)

Index("uq_users_normalized_username", func.lower(users.c.normalized_username), unique=True)
Index("uq_roles_code", func.lower(roles.c.code), unique=True)
Index("idx_sessions_user_id", sessions.c.user_id)
Index("idx_project_members_project_id", project_members.c.project_id)
Index("idx_workflow_tasks_pool", workflow_tasks.c.role_code, workflow_tasks.c.status)
Index("idx_workflow_submissions_task", workflow_submissions.c.task_id, workflow_submissions.c.status)
Index("idx_audit_logs_created_at", audit_logs.c.created_at)
Index("idx_audit_logs_action", audit_logs.c.action)
Index("idx_audit_logs_actor_user_id", audit_logs.c.actor_user_id)
Index(
    "idx_workflow_project_member_events_project",
    workflow_project_member_events.c.project_id,
    workflow_project_member_events.c.created_at,
)
Index(
    "uq_workflow_task_transition",
    workflow_tasks.c.workflow_id,
    workflow_tasks.c.task_type,
    workflow_tasks.c.predecessor_task_id,
    unique=True,
    postgresql_where=workflow_tasks.c.predecessor_task_id.is_not(None),
)
