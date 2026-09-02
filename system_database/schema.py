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
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB


SYSTEM_METADATA = MetaData()

users = Table(
    "users",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("username", Text, nullable=False),
    Column("normalized_username", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("password_hash", Text, nullable=False),
    Column("email", Text),
    Column("phone", Text),
    Column("gender", Text, nullable=False, server_default=text("'unspecified'")),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("is_deleted", Integer, nullable=False, server_default=text("0")),
    Column("created_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("deleted_at", Text),
    Column("deleted_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("last_login_at", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
    CheckConstraint(
        "gender IN ('unspecified', 'male', 'female')", name="ck_users_gender"
    ),
    CheckConstraint("is_deleted IN (0, 1)", name="ck_users_is_deleted"),
)

Index(
    "uq_users_active_email",
    users.c.email,
    unique=True,
    postgresql_where=text("is_deleted = 0 AND email IS NOT NULL"),
)
Index(
    "uq_users_active_phone",
    users.c.phone,
    unique=True,
    postgresql_where=text("is_deleted = 0 AND phone IS NOT NULL"),
)

roles = Table(
    "roles",
    SYSTEM_METADATA,
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
    SYSTEM_METADATA,
    Column("code", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

navigation_items = Table(
    "navigation_items",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "parent_id",
        Text,
        ForeignKey("navigation_items.id", ondelete="RESTRICT"),
    ),
    Column("label", Text, nullable=False),
    Column("icon", Text, nullable=False, server_default=text("''")),
    Column("route", Text, nullable=False),
    Column(
        "required_permission",
        Text,
        ForeignKey("permissions.code", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sort_order", Integer, nullable=False, server_default=text("0")),
    Column("is_enabled", Integer, nullable=False, server_default=text("1")),
    Column("is_system", Integer, nullable=False, server_default=text("1")),
    Column(
        "hide_for_system_admin", Integer, nullable=False, server_default=text("0")
    ),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("is_enabled IN (0, 1)", name="ck_navigation_items_is_enabled"),
    CheckConstraint("is_system IN (0, 1)", name="ck_navigation_items_is_system"),
    CheckConstraint(
        "hide_for_system_admin IN (0, 1)",
        name="ck_navigation_items_hide_for_system_admin",
    ),
)

user_roles = Table(
    "user_roles",
    SYSTEM_METADATA,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Text, ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

user_page_access = Table(
    "user_page_access",
    SYSTEM_METADATA,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("route", Text, primary_key=True),
    Column("is_allowed", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

role_page_access = Table(
    "role_page_access",
    SYSTEM_METADATA,
    Column("role_id", Text, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("route", Text, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

role_permissions = Table(
    "role_permissions",
    SYSTEM_METADATA,
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
    SYSTEM_METADATA,
    Column("role_id", Text, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("task_type", Text, primary_key=True),
)

permission_dependencies = Table(
    "permission_dependencies",
    SYSTEM_METADATA,
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
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("token_hash", Text, nullable=False, unique=True),
    Column("csrf_token_hash", Text, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("revoked_at", DateTime(timezone=True)),
    Column("user_agent", Text),
    Column("ip_address", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

project_members = Table(
    "project_members",
    SYSTEM_METADATA,
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

departments = Table(
    "departments",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("parent_id", Text, ForeignKey("departments.id", ondelete="RESTRICT")),
    Column("sort_order", Integer, nullable=False, server_default=text("0")),
    Column("status", Text, nullable=False, server_default=text("'active'")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    Column("updated_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
    CheckConstraint("status IN ('active', 'disabled')", name="ck_departments_status"),
)

user_departments = Table(
    "user_departments",
    SYSTEM_METADATA,
    Column("user_id", Text, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "department_id",
        Text,
        ForeignKey("departments.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("is_primary", Boolean, nullable=False, server_default=text("false")),
    Column("created_at", Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")),
)

llm_providers = Table(
    "llm_providers",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("normalized_name", Text, nullable=False, unique=True),
    Column("preset", Text, nullable=False),
    Column("base_url", Text, nullable=False),
    Column("wire_api", Text, nullable=False),
    Column("api_key_encrypted", Text),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "preset IN ('openai', 'deepseek', 'minimax', 'sub2api', 'custom')",
        name="ck_llm_providers_preset",
    ),
    CheckConstraint(
        "wire_api IN ('responses', 'chat_completions')",
        name="ck_llm_providers_wire_api",
    ),
)

llm_models = Table(
    "llm_models",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "provider_id",
        Text,
        ForeignKey("llm_providers.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("model_id", Text, nullable=False),
    Column("display_name", Text, nullable=False, server_default=text("''")),
    Column("source", Text, nullable=False, server_default=text("'manual'")),
    Column("is_enabled", Boolean, nullable=False, server_default=text("true")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("provider_id", "model_id", name="uq_llm_models_provider_model"),
    CheckConstraint(
        "source IN ('manual', 'synced', 'imported')",
        name="ck_llm_models_source",
    ),
)

llm_settings = Table(
    "llm_settings",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column(
        "default_model_id",
        Text,
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
    ),
    Column("legacy_imported_at", DateTime(timezone=True)),
    Column("updated_by_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("id = 'global'", name="ck_llm_settings_global_id"),
)

rank_snapshots = Table(
    "rank_snapshots",
    SYSTEM_METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("platform", String(16), nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False),
    Column("snapshot_date", Date, nullable=False),
    Column("job_id", Text),
    Column("source_kind", String(32), nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("source_checksum", String(64), nullable=False),
    Column("parser_version", Integer, nullable=False, server_default=text("1")),
    Column("record_count", Integer, nullable=False),
    Column(
        "validation_json",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column(
        "imported_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "platform IN ('dy', 'hg')",
        name="ck_rank_snapshots_platform",
    ),
    CheckConstraint(
        "record_count >= 0",
        name="ck_rank_snapshots_record_count",
    ),
    UniqueConstraint(
        "platform",
        "source_kind",
        "source_ref",
        name="uq_rank_snapshots_source_ref",
    ),
    UniqueConstraint(
        "platform",
        "source_checksum",
        name="uq_rank_snapshots_checksum",
    ),
)

Index(
    "ix_rank_snapshots_platform_date_captured",
    rank_snapshots.c.platform,
    rank_snapshots.c.snapshot_date,
    rank_snapshots.c.captured_at,
)

rank_entries = Table(
    "rank_entries",
    SYSTEM_METADATA,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column(
        "snapshot_id",
        BigInteger,
        ForeignKey(
            "rank_snapshots.id",
            ondelete="CASCADE",
            name="fk_rank_entries_snapshot_id",
        ),
        nullable=False,
    ),
    Column("section_name", Text, nullable=False, server_default=text("''")),
    Column("ranking_name", Text, nullable=False, server_default=text("''")),
    Column("rank", Integer, nullable=False),
    Column("series_key", Text, nullable=False, server_default=text("''")),
    Column("title", Text, nullable=False),
    Column("category", Text),
    Column("status", Text),
    Column("subject", Text),
    Column("description", Text),
    Column("hot_comment", Text),
    Column("score", Numeric(20, 4)),
    Column("share_count", BigInteger),
    Column("like_count", BigInteger),
    Column("favorite_count", BigInteger),
    Column("heat", Numeric(20, 4)),
    Column("comment_count", BigInteger),
    Column("recommend_index", Numeric(20, 4)),
    Column("rating_count", BigInteger),
    Column("expectation_index", Numeric(20, 4)),
    Column("search_index", Numeric(20, 4)),
    Column("max_heat", Numeric(20, 4)),
    Column("source_record_json", JSONB, nullable=False),
    Column(
        "validation_warnings_json",
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    ),
    CheckConstraint("rank > 0", name="ck_rank_entries_rank_positive"),
    UniqueConstraint(
        "snapshot_id",
        "section_name",
        "ranking_name",
        "rank",
        name="uq_rank_entries_board_rank",
    ),
)

Index("ix_rank_entries_snapshot_id", rank_entries.c.snapshot_id)
Index(
    "ix_rank_entries_board",
    rank_entries.c.snapshot_id,
    rank_entries.c.section_name,
    rank_entries.c.ranking_name,
)
Index("ix_rank_entries_series_key", rank_entries.c.series_key)

workflow_instances = Table(
    "workflow_instances",
    SYSTEM_METADATA,
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
    SYSTEM_METADATA,
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
    Column("due_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

workflow_task_jobs = Table(
    "workflow_task_jobs",
    SYSTEM_METADATA,
    Column(
        "task_id",
        Text,
        ForeignKey("workflow_tasks.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("job_id", Text, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

workflow_task_events = Table(
    "workflow_task_events",
    SYSTEM_METADATA,
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
    Column("details_json", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

workflow_submissions = Table(
    "workflow_submissions",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("task_id", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("resource_id", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("parent_submission_id", Text),
    Column("status", Text, nullable=False),
    Column("content_snapshot_json", JSONB, nullable=False),
    Column("content_fingerprint", Text, nullable=False),
    Column("created_by", Text, nullable=False),
    Column("submitted_by", Text),
    Column("submitted_at", DateTime(timezone=True)),
    Column("reviewed_by", Text),
    Column("reviewed_at", DateTime(timezone=True)),
    Column("review_comment", Text, nullable=False, server_default=text("''")),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
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
    SYSTEM_METADATA,
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
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("actor_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("actor_user_id", Text, ForeignKey("users.id", ondelete="SET NULL")),
    Column("actor_display_name_snapshot", Text),
    Column(
        "actor_role_snapshot_json", JSONB, nullable=False, server_default=text("'[]'::jsonb")
    ),
    Column("action", Text, nullable=False),
    Column("resource_type", Text, nullable=False),
    Column("target_type", Text),
    Column("resource_id", Text),
    Column("target_id", Text),
    Column("project_id", Text),
    Column("reason", Text),
    Column("details_json", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("before_summary_json", JSONB),
    Column("after_summary_json", JSONB),
    Column("request_id", Text),
    Column("ip_address", Text),
    Column("user_agent", Text),
    Column("result", Text, nullable=False, server_default=text("'success'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

workflow_project_member_events = Table(
    "workflow_project_member_events",
    SYSTEM_METADATA,
    Column("id", Text, primary_key=True),
    Column("project_id", Text, nullable=False),
    Column("membership_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("actor_id", Text, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
    Column("before_json", JSONB),
    Column("after_json", JSONB),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
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
Index("idx_user_page_access_route", user_page_access.c.route)
Index(
    "idx_navigation_items_parent_sort",
    navigation_items.c.parent_id,
    navigation_items.c.sort_order,
)
Index("idx_project_members_project_id", project_members.c.project_id)
Index(
    "uq_departments_parent_name",
    func.coalesce(departments.c.parent_id, literal_column("''")),
    departments.c.name,
    unique=True,
)
Index("idx_departments_parent_sort", departments.c.parent_id, departments.c.sort_order)
Index("idx_user_departments_department_id", user_departments.c.department_id)
Index(
    "uq_user_departments_primary",
    user_departments.c.user_id,
    unique=True,
    postgresql_where=user_departments.c.is_primary.is_(True),
)
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
