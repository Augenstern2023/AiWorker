# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/creative\production_status_cli.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

# Copyright (c) 2025 relakkes@gmail.com
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
"""Local operator entrypoint; does not expose filesystem paths over HTTP.

The bound project tree is still a filesystem path; the status document is now
persisted in the migrated creative ``production_status`` table, so the store is
addressed by ``--database-url`` (default ``AIWORKER_SYSTEM_DATABASE_URL``)
instead of a SQLite file. Output and arguments are otherwise unchanged.
"""
import argparse
import json

from creative.postgres_repository import PostgresCreativeRepository
from creative.production_status import sync_production_status
from system_database import SystemDatabase, SystemDatabaseSettings


def _repository(database_url: str | None) -> tuple[SystemDatabase, PostgresCreativeRepository]:
    settings = (
        SystemDatabaseSettings(url=database_url)
        if database_url
        else SystemDatabaseSettings.from_environment()
    )
    database = SystemDatabase(settings)
    return database, PostgresCreativeRepository(database)


def main():
    parser = argparse.ArgumentParser(description='回读 auto_short_play 状态至 AiWorker 独立生产状态库')
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--project-id', required=True, type=int)
    parser.add_argument('--fingerprint', required=True)
    parser.add_argument(
        '--database-url',
        default=None,
        help='创意库 PostgreSQL URL；默认取 AIWORKER_SYSTEM_DATABASE_URL',
    )
    args = parser.parse_args()
    database, repository = _repository(args.database_url)
    try:
        result = sync_production_status(
            repository, args.project_root, args.project_id, args.fingerprint,
        )
    finally:
        database.dispose()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
