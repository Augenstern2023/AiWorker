# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Remove the unused local schedule center data."""

from __future__ import annotations

from importlib import import_module

from alembic import op
import sqlalchemy as sa


revision = "0008_remove_schedule_governance"
down_revision = "0007_role_page_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    route = "/admin/schedules"
    op.execute(
        sa.text("DELETE FROM role_page_access WHERE route = :route").bindparams(
            route=route
        )
    )
    op.execute(
        sa.text("DELETE FROM user_page_access WHERE route = :route").bindparams(
            route=route
        )
    )
    op.execute(
        sa.text("DELETE FROM navigation_items WHERE id = :item_id").bindparams(
            item_id="navigation:admin:schedules"
        )
    )
    op.drop_table("schedule_alerts")
    op.drop_table("schedule_runs")
    op.drop_table("schedule_definitions")


def downgrade() -> None:
    schedule_governance = import_module(
        "system_database.alembic.versions.0002_schedule_governance"
    )
    schedule_governance.upgrade()
    op.execute(
        sa.text(
            """
            INSERT INTO navigation_items
                (id, parent_id, label, icon, route, required_permission, sort_order,
                 is_enabled, is_system, hide_for_system_admin)
            VALUES
                (:id, :parent_id, :label, :icon, :route, :required_permission,
                 :sort_order, 1, 1, 0)
            ON CONFLICT (id) DO NOTHING
            """
        ).bindparams(
            id="navigation:admin:schedules",
            parent_id="navigation:admin",
            label="调度中心",
            icon="bi-clock-history",
            route="/admin/schedules",
            required_permission="*",
            sort_order=80,
        )
    )
