# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

"""Import-boundary coverage for the PostgreSQL Core audit repository."""

from audit.repository import AuditRepository


def test_audit_repository_imports_with_supported_sqlalchemy_symbols() -> None:
    assert AuditRepository.__name__ == "AuditRepository"
