# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Add video-parameter recommendations and ZIP export metadata."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0026_video_params_zip_exports"
down_revision = "0025_creative_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE creative_production_exports "
            "ADD COLUMN IF NOT EXISTS artifact_format TEXT NOT NULL DEFAULT 'json'"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE creative_production_exports "
            "ADD COLUMN IF NOT EXISTS media_type TEXT NOT NULL "
            "DEFAULT 'application/json'"
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS creative_video_parameter_recommendations (
                project_id BIGINT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'idle',
                job_id TEXT NOT NULL DEFAULT '',
                source_fingerprint TEXT NOT NULL DEFAULT '',
                source_episode_numbers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                source_complete BOOLEAN NOT NULL DEFAULT false,
                recommendation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                confirmed_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                analysis_mode TEXT NOT NULL DEFAULT '',
                analysis_error TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                generated_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL,
                CONSTRAINT fk_creative_video_parameter_recommendations_project_id
                    FOREIGN KEY(project_id) REFERENCES creative_projects(id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_creative_video_parameter_recommendations_status
                    CHECK (status IN ('idle', 'queued', 'running', 'ready', 'failed'))
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_table("creative_video_parameter_recommendations")
    op.drop_column("creative_production_exports", "media_type")
    op.drop_column("creative_production_exports", "artifact_format")
