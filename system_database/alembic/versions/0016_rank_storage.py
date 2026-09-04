# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0016_rank_storage"
down_revision = "0015_llm_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rank_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("job_id", sa.Text()),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=False),
        sa.Column("source_checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "parser_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column(
            "validation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "platform IN ('dy', 'hg')",
            name="ck_rank_snapshots_platform",
        ),
        sa.CheckConstraint(
            "record_count >= 0",
            name="ck_rank_snapshots_record_count",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rank_snapshots"),
        sa.UniqueConstraint(
            "platform",
            "source_checksum",
            name="uq_rank_snapshots_checksum",
        ),
        sa.UniqueConstraint(
            "platform",
            "source_kind",
            "source_ref",
            name="uq_rank_snapshots_source_ref",
        ),
    )
    op.create_index(
        "ix_rank_snapshots_platform_date_captured",
        "rank_snapshots",
        ["platform", "snapshot_date", "captured_at"],
        unique=False,
    )
    op.create_table(
        "rank_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "section_name",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column(
            "ranking_name",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "series_key",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.Text()),
        sa.Column("status", sa.Text()),
        sa.Column("subject", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("hot_comment", sa.Text()),
        sa.Column("score", sa.Numeric(precision=20, scale=4)),
        sa.Column("share_count", sa.BigInteger()),
        sa.Column("like_count", sa.BigInteger()),
        sa.Column("favorite_count", sa.BigInteger()),
        sa.Column("heat", sa.Numeric(precision=20, scale=4)),
        sa.Column("comment_count", sa.BigInteger()),
        sa.Column("recommend_index", sa.Numeric(precision=20, scale=4)),
        sa.Column("rating_count", sa.BigInteger()),
        sa.Column("expectation_index", sa.Numeric(precision=20, scale=4)),
        sa.Column("search_index", sa.Numeric(precision=20, scale=4)),
        sa.Column("max_heat", sa.Numeric(precision=20, scale=4)),
        sa.Column(
            "source_record_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "validation_warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint("rank > 0", name="ck_rank_entries_rank_positive"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["rank_snapshots.id"],
            name="fk_rank_entries_snapshot_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rank_entries"),
        sa.UniqueConstraint(
            "snapshot_id",
            "section_name",
            "ranking_name",
            "rank",
            name="uq_rank_entries_board_rank",
        ),
    )
    op.create_index(
        "ix_rank_entries_snapshot_id",
        "rank_entries",
        ["snapshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_rank_entries_board",
        "rank_entries",
        ["snapshot_id", "section_name", "ranking_name"],
        unique=False,
    )
    op.create_index(
        "ix_rank_entries_series_key",
        "rank_entries",
        ["series_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_rank_entries_series_key", table_name="rank_entries")
    op.drop_index("ix_rank_entries_board", table_name="rank_entries")
    op.drop_index("ix_rank_entries_snapshot_id", table_name="rank_entries")
    op.drop_table("rank_entries")
    op.drop_index(
        "ix_rank_snapshots_platform_date_captured",
        table_name="rank_snapshots",
    )
    op.drop_table("rank_snapshots")
