# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseHealth:
    reachable: bool
    database_url_redacted: str
    current_revision: str | None = None
    head_revision: str | None = None
    schema_current: bool = False
