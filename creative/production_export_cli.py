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
"""Trusted local operator export to stdout; not an audited delivery HTTP API."""
import argparse
import json
import sys

from creative.production_export import export_production_package


def main():
    parser = argparse.ArgumentParser(description='导出完整锁定生产包至标准输出（本地操作员工具）')
    parser.add_argument('--db', default='.runtime/creative/creative.db')
    parser.add_argument('--project-id', type=int, required=True)
    args = parser.parse_args()
    package = export_production_package(args.db, args.project_id)
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(package, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
