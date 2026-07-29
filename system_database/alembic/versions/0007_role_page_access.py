# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com

"""Add role page defaults and account deny overrides."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from system_database.schema import role_page_access


revision = "0007_role_page_access"
down_revision = "0006_user_page_access"
branch_labels = None
depends_on = None


_DEFAULT_ROUTES = {
    "selection_operator": (
        "/selection/dashboard", "/selection/ranks", "/selection/hits",
        "/selection/topics", "/selection/dramas",
    ),
    "creative_planner": ("/planning/projects",),
    "screenwriter": ("/writing/projects",),
    "project_owner": (
        "/admin/roles", "/admin/audit", "/admin/data", "/admin/drama-player",
        "/selection/dashboard", "/selection/ranks", "/selection/hits",
        "/selection/topics", "/selection/dramas",
    ),
}


def upgrade() -> None:
    op.add_column(
        "user_page_access",
        sa.Column("is_allowed", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    role_page_access.create(bind=op.get_bind())
    for role_code, routes in _DEFAULT_ROUTES.items():
        for route in routes:
            op.execute(
                sa.text(
                    """
                    INSERT INTO role_page_access (role_id, route)
                    SELECT id, :route FROM roles WHERE lower(code) = :role_code
                    ON CONFLICT (role_id, route) DO NOTHING
                    """
                ).bindparams(route=route, role_code=role_code)
            )


def downgrade() -> None:
    role_page_access.drop(bind=op.get_bind())
    op.drop_column("user_page_access", "is_allowed")
