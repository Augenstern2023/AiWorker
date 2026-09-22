# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

import importlib
import logging


def test_invalid_semif_environment_values_warn_and_fall_back(
    monkeypatch, caplog
) -> None:
    from config import bilibili_config

    with monkeypatch.context() as environment:
        environment.setenv("SCREENPLAY_REVIEW_ENGINE", "invented")
        environment.setenv("SEMIF_TIMEOUT_SECONDS", "not-a-number")
        environment.setenv("SEMIF_MAX_RETRIES", "not-a-number")
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=bilibili_config.__name__):
            profile = importlib.reload(bilibili_config)

        assert profile.SCREENPLAY_REVIEW_ENGINE == "legacy"
        assert profile.SEMIF_TIMEOUT_SECONDS == 120
        assert profile.SEMIF_MAX_RETRIES == 2
        assert "SCREENPLAY_REVIEW_ENGINE" in caplog.text
        assert "SEMIF_TIMEOUT_SECONDS" in caplog.text
        assert "SEMIF_MAX_RETRIES" in caplog.text

    importlib.reload(bilibili_config)
