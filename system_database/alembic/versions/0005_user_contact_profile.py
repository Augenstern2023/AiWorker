# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_user_contact_profile"
down_revision = "0004_runtime_timestamptz_jsonb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("phone", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "gender",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unspecified'"),
        ),
    )
    op.create_check_constraint(
        "ck_users_gender", "users", "gender IN ('unspecified', 'male', 'female')"
    )
    op.create_index(
        "uq_users_active_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text("is_deleted = 0 AND email IS NOT NULL"),
    )
    op.create_index(
        "uq_users_active_phone",
        "users",
        ["phone"],
        unique=True,
        postgresql_where=sa.text("is_deleted = 0 AND phone IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_users_active_phone", table_name="users")
    op.drop_index("uq_users_active_email", table_name="users")
    op.drop_constraint("ck_users_gender", "users", type_="check")
    op.drop_column("users", "gender")
    op.drop_column("users", "phone")
    op.drop_column("users", "email")
