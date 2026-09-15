# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Remove retired team catalogs and grants without deleting identity records.

Downgrade restores historical built-in catalogs only. Account page overrides,
role page grants, custom role permissions and custom dependency edges cannot
be reconstructed after deletion and are intentionally not recreated.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0022_remove_team_management"
down_revision = "0021_llm_local_api_keys"
branch_labels = None
depends_on = None


_TEAM_PERMISSIONS = (
    ("page.team_management", "访问团队管理"),
    ("identity.user.read", "查看用户"),
    ("identity.user.create", "创建用户"),
    ("identity.user.manage_created", "管理已创建用户"),
    ("identity.role.read", "查看角色"),
    ("identity.role.create", "创建角色"),
    ("identity.role.manage_created", "管理已创建角色"),
)
_TEAM_ROUTES = ("/review/users", "/review/roles")


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM user_page_access WHERE route IN :routes").bindparams(
            sa.bindparam("routes", _TEAM_ROUTES, expanding=True)
        )
    )
    op.execute(
        sa.text("DELETE FROM role_page_access WHERE route IN :routes").bindparams(
            sa.bindparam("routes", _TEAM_ROUTES, expanding=True)
        )
    )
    # Self-referential navigation and permission FKs use RESTRICT.
    op.execute(
        sa.text("DELETE FROM navigation_items WHERE id IN :ids").bindparams(
            sa.bindparam(
                "ids",
                ("navigation:team:users", "navigation:team:roles"),
                expanding=True,
            )
        )
    )
    op.execute(
        sa.text("DELETE FROM navigation_items WHERE id = :id").bindparams(
            id="navigation:team"
        )
    )
    codes = tuple(code for code, _ in _TEAM_PERMISSIONS)
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code IN :codes"
        ).bindparams(sa.bindparam("codes", codes, expanding=True))
    )
    op.execute(
        sa.text(
            "DELETE FROM permission_dependencies "
            "WHERE permission_code IN :codes OR depends_on_code IN :codes"
        ).bindparams(sa.bindparam("codes", codes, expanding=True))
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE code IN :codes").bindparams(
            sa.bindparam("codes", codes, expanding=True)
        )
    )


def downgrade() -> None:
    for code, label in _TEAM_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, label) VALUES (:code, :label) "
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, label=label)
        )
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_code, data_scope) "
                "SELECT id, :code, :scope FROM roles "
                "WHERE lower(code) = :role_code AND is_system = 1 "
                "ON CONFLICT (role_id, permission_code) DO NOTHING"
            ).bindparams(
                code=code,
                scope="managed_projects",
                role_code="project_owner",
            )
        )

    for identifier, parent, label, icon, route, order, hide in (
        ("navigation:team", None, "团队管理", "bi-people", "/review/users", 50, 1),
        (
            "navigation:team:users",
            "navigation:team",
            "团队账号",
            "",
            "/review/users",
            10,
            0,
        ),
        (
            "navigation:team:roles",
            "navigation:team",
            "角色授权",
            "",
            "/review/roles",
            20,
            0,
        ),
    ):
        op.execute(
            sa.text(
                "INSERT INTO navigation_items "
                "(id, parent_id, label, icon, route, required_permission, sort_order, "
                "is_enabled, is_system, hide_for_system_admin) "
                "VALUES (:id, :parent, :label, :icon, :route, :permission, "
                ":sort_order, 1, 1, :hide) "
                "ON CONFLICT (id) DO NOTHING"
            ).bindparams(
                id=identifier,
                parent=parent,
                label=label,
                icon=icon,
                route=route,
                permission="page.team_management",
                sort_order=order,
                hide=hide,
            )
        )
