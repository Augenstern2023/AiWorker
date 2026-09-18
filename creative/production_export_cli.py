# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/creative\production_export_cli.py
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
"""Trusted local operator export to stdout; not an audited delivery HTTP API.

The operator tool reads live creative data, so it talks to the same PostgreSQL
store the runtime does: ``--database-url`` overrides, otherwise the URL comes
from ``AIWORKER_SYSTEM_DATABASE_URL``. There is deliberately no SQLite path --
a path option here could only ever read the frozen legacy file and hand it back
as if it were current. The printed package, the arguments and the failure modes
are unchanged apart from that one option.
"""
import argparse
import json
import sys

from creative.postgres_repository import PostgresCreativeRepository
from creative.production_export import export_production_package
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
    parser = argparse.ArgumentParser(description='导出完整锁定生产包至标准输出（本地操作员工具）')
    parser.add_argument(
        '--database-url',
        default=None,
        help='创意库 PostgreSQL URL；默认取 AIWORKER_SYSTEM_DATABASE_URL',
    )
    parser.add_argument('--project-id', type=int, required=True)
    args = parser.parse_args()
    database, repository = _repository(args.database_url)
    try:
        package = export_production_package(repository, args.project_id)
    finally:
        database.dispose()
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(package, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
