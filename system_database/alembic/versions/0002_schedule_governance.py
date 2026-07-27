# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Add persisted local schedule governance tables."""

from __future__ import annotations

from alembic import op

from system_database.schema import scheduling_alerts, scheduling_definitions, scheduling_runs


revision = "0002_schedule_governance"
down_revision = "0001_system_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    scheduling_definitions.create(bind=bind)
    scheduling_runs.create(bind=bind)
    scheduling_alerts.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    scheduling_alerts.drop(bind=bind)
    scheduling_runs.drop(bind=bind)
    scheduling_definitions.drop(bind=bind)
