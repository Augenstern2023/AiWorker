# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Public prompt-marketplace interface backed exclusively by PostgreSQL."""

from creative.postgres_prompt_marketplace import (
    MARKETPLACE_LICENSE_URLS,
    MARKETPLACE_LICENSES,
    MARKETPLACE_SOURCE_TYPES,
    PromptMarketplaceService,
)


__all__ = [
    "MARKETPLACE_LICENSE_URLS",
    "MARKETPLACE_LICENSES",
    "MARKETPLACE_SOURCE_TYPES",
    "PromptMarketplaceService",
]
