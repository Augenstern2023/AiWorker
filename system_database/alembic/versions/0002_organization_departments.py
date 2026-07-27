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

from system_database.schema import departments, user_departments


revision = "0002_organization_departments"
down_revision = "0001_system_schema_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    departments.create(bind=op.get_bind())
    user_departments.create(bind=op.get_bind())


def downgrade() -> None:
    user_departments.drop(bind=op.get_bind())
    departments.drop(bind=op.get_bind())
