# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Store encrypted LLM providers, models, and the global default."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0015_llm_settings"
down_revision = "0014_project_creator_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("preset", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("wire_api", sa.Text(), nullable=False),
        sa.Column("api_key_encrypted", sa.Text()),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_by_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "preset IN ('openai', 'deepseek', 'minimax', 'sub2api', 'custom')",
            name="ck_llm_providers_preset",
        ),
        sa.CheckConstraint(
            "wire_api IN ('responses', 'chat_completions')",
            name="ck_llm_providers_wire_api",
        ),
        sa.UniqueConstraint("normalized_name", name="uq_llm_providers_normalized_name"),
    )
    op.create_table(
        "llm_models",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("provider_id", sa.Text(), sa.ForeignKey("llm_providers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source IN ('manual', 'synced', 'imported')",
            name="ck_llm_models_source",
        ),
        sa.UniqueConstraint("provider_id", "model_id", name="uq_llm_models_provider_model"),
    )
    op.create_table(
        "llm_settings",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("default_model_id", sa.Text(), sa.ForeignKey("llm_models.id", ondelete="RESTRICT")),
        sa.Column("legacy_imported_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by_user_id", sa.Text(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 'global'", name="ck_llm_settings_global_id"),
    )


def downgrade() -> None:
    op.drop_table("llm_settings")
    op.drop_table("llm_models")
    op.drop_table("llm_providers")
