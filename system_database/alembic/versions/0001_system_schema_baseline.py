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

from system_database.schema import BASELINE_TABLE_NAMES, SYSTEM_METADATA


revision = "0001_system_schema_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    SYSTEM_METADATA.create_all(
        bind=op.get_bind(),
        tables=[SYSTEM_METADATA.tables[name] for name in BASELINE_TABLE_NAMES],
    )


def downgrade() -> None:
    SYSTEM_METADATA.drop_all(
        bind=op.get_bind(),
        tables=[SYSTEM_METADATA.tables[name] for name in BASELINE_TABLE_NAMES],
    )
