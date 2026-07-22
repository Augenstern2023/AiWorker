# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


_PASSWORD_HASHER = PasswordHasher()


class PasswordService:
    """Validate and hash local-account passwords with one Argon2id hasher."""

    def __init__(self) -> None:
        self._hasher = _PASSWORD_HASHER

    def hash(self, password: str) -> str:
        self._validate(password)
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        if not 10 <= len(password) <= 128:
            return False
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    @staticmethod
    def _validate(password: str) -> None:
        if not 10 <= len(password) <= 128:
            raise ValueError("密码长度必须为 10 到 128 个字符")
        if not any(character.isalpha() for character in password):
            raise ValueError("密码必须包含至少一个字母")
        if not any(character.isdigit() for character in password):
            raise ValueError("密码必须包含至少一个数字")
        if not any(not character.isalnum() for character in password):
            raise ValueError("密码必须包含至少一个非字母数字字符")
