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


revision = "0006_user_page_access"
down_revision = "0005_user_contact_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_page_access",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "route"),
    )
    op.create_index("idx_user_page_access_route", "user_page_access", ["route"])


def downgrade() -> None:
    op.drop_index("idx_user_page_access_route", table_name="user_page_access")
    op.drop_table("user_page_access")
