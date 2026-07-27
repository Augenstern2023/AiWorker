# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class SystemDatabaseSettings:
    url: str

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None
    ) -> "SystemDatabaseSettings":
        value = (environ or os.environ).get("AIWORKER_SYSTEM_DATABASE_URL", "").strip()
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError(
                "AIWORKER_SYSTEM_DATABASE_URL must use postgresql+psycopg://"
            )
        return cls(url=value)

    @property
    def redacted_url(self) -> str:
        return make_url(self.url).render_as_string(hide_password=True)
