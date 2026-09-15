# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Store model pricing used by prompt A/B budget estimates."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0021_llm_model_pricing"
down_revision = "0020_user_presence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "llm_models",
        sa.Column("input_price_per_million", sa.Float(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column("output_price_per_million", sa.Float(), nullable=True),
    )
    op.add_column(
        "llm_models",
        sa.Column(
            "price_currency",
            sa.Text(),
            nullable=False,
            server_default="CNY",
        ),
    )
    op.create_check_constraint(
        "ck_llm_models_input_price",
        "llm_models",
        "input_price_per_million IS NULL OR input_price_per_million >= 0",
    )
    op.create_check_constraint(
        "ck_llm_models_output_price",
        "llm_models",
        "output_price_per_million IS NULL OR output_price_per_million >= 0",
    )
    op.create_check_constraint(
        "ck_llm_models_price_currency",
        "llm_models",
        "price_currency IN ('CNY', 'USD')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_llm_models_price_currency", "llm_models", type_="check")
    op.drop_constraint("ck_llm_models_output_price", "llm_models", type_="check")
    op.drop_constraint("ck_llm_models_input_price", "llm_models", type_="check")
    op.drop_column("llm_models", "price_currency")
    op.drop_column("llm_models", "output_price_per_million")
    op.drop_column("llm_models", "input_price_per_million")
