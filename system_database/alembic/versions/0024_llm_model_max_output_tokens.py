# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Store an optional output-token limit for each LLM model."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0024_llm_model_max_output_tokens"
down_revision = "0023_llm_model_pricing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_llm_models_max_output_tokens",
        "llm_models",
        "max_output_tokens IS NULL OR max_output_tokens >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_llm_models_max_output_tokens", "llm_models", type_="check"
    )
    op.drop_column("llm_models", "max_output_tokens")
