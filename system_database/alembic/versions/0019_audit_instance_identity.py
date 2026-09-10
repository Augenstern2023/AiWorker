# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Record the service instance and creative store that emitted an audit event."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_audit_instance_identity"
down_revision = "0018_member_writer_operation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("service_instance_id", sa.Text()))
    op.add_column("audit_logs", sa.Column("creative_store_id", sa.Text()))


def downgrade() -> None:
    op.drop_column("audit_logs", "creative_store_id")
    op.drop_column("audit_logs", "service_instance_id")
