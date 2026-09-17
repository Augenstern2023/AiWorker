# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Create the creative domain schema in the shared system database.

The revision chains from ``0024_llm_model_max_output_tokens`` because that is
the revision the deployed database already reports; the creative baseline was
originally drafted as ``0023`` before the LLM pricing revisions landed.

Only creative-domain foreign keys are created here. References to ``users``,
``workflow_tasks`` and ``workflow_submissions`` stay application-enforced until
the cross-domain reference audit permits them.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from creative.database_schema import CREATIVE_METADATA


revision = "0025_creative_schema_baseline"
down_revision = "0024_llm_model_max_output_tokens"
branch_labels = None
depends_on = None


# The legacy SQLite store guards these rows with BEFORE UPDATE/DELETE triggers.
# PostgreSQL reproduces the same refusal, including the original message text.
_IMMUTABILITY_FUNCTION = "creative_selection_snapshot_immutable"
_SELECTION_SNAPSHOT_TABLE = "selection_stage_snapshots"

_CREATE_IMMUTABILITY_FUNCTION = sa.text(
    """
    CREATE OR REPLACE FUNCTION creative_selection_snapshot_immutable()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'selection snapshots are immutable';
    END;
    $$ LANGUAGE plpgsql
    """
)

_CREATE_UPDATE_TRIGGER = sa.text(
    """
    CREATE TRIGGER trg_selection_snapshot_no_update
    BEFORE UPDATE ON selection_stage_snapshots
    FOR EACH ROW EXECUTE FUNCTION creative_selection_snapshot_immutable()
    """
)

_CREATE_DELETE_TRIGGER = sa.text(
    """
    CREATE TRIGGER trg_selection_snapshot_no_delete
    BEFORE DELETE ON selection_stage_snapshots
    FOR EACH ROW EXECUTE FUNCTION creative_selection_snapshot_immutable()
    """
)


def upgrade() -> None:
    # create_all emits tables and indexes in foreign-key dependency order.
    CREATIVE_METADATA.create_all(bind=op.get_bind())

    op.execute(_CREATE_IMMUTABILITY_FUNCTION)
    op.execute(_CREATE_UPDATE_TRIGGER)
    op.execute(_CREATE_DELETE_TRIGGER)


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_selection_snapshot_no_update "
            "ON selection_stage_snapshots"
        )
    )
    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS trg_selection_snapshot_no_delete "
            "ON selection_stage_snapshots"
        )
    )
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS creative_selection_snapshot_immutable()")
    )

    CREATIVE_METADATA.drop_all(bind=op.get_bind())
