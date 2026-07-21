# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/main.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from agents.storage_agent import StorageAgent


class ExportAgent:
    def __init__(self, storage: StorageAgent):
        self.storage = storage

    def list_job_artifacts(self, job_id: str) -> list[dict]:
        return self.storage.list_artifacts(job_id)
