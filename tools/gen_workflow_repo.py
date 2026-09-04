# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tools\gen_workflow_repo.py
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

# Generator: writes workflow/repository.py with SQLAlchemy Core
import pathlib

OUT = pathlib.Path("D:/Crawler/AiWorker/workflow/repository.py")

L = []

def p(s=""):
    L.append(s)

p('# -*- coding: utf-8 -*-')
p('# (c) 2025. Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1')
p('from __future__ import annotations')
p('import json, time, uuid')
p('from contextlib import nullcontext')
p('from typing import Any')
p('from sqlalchemy import Connection, and_, func, or_, select, insert, update, delete, text')
p('from sqlalchemy.dialects.postgresql import insert as pg_insert')
p('from identity.repository import IdentityRepository')
p('from identity.schema import DATA_SCOPE_ORDER, Principal')
p('from system_database.schema import project_members, role_claimable_task_types, role_permissions, roles, user_roles, users, workflow_instances, workflow_project_member_events, workflow_submissions, workflow_task_events, workflow_task_jobs, workflow_tasks')
p('from .state_machine import WorkflowStateMachine')
p('')
p('O = frozenset({"owner","project_owner"})')
p('M = O | {"manager","project_manager"}')
p('SA = Principal(user_id="workflow:system",username="workflow-system",display_name="Workflow System",role_codes=("system_admin",),permissions=frozenset({"*"}),data_scope="all",session_id="workflow:system")')
p('')
p('class WorkflowRepository:')
p(' def __init__(s,r,sm=None):')
p('  s.r=r if isinstance(r,IdentityRepository) else IdentityRepository(r)')
p('  s.sm=sm or WorkflowStateMachine()')
p('')
p(' def _aid(s,a):')
p('  if a is None: raise ValueError("actor required")')
p('  v=a.get("user_id")or a.get("id")if isinstance(a,dict)else a.user_id')
p('  if not v: raise PermissionError("authenticated actor required")')
p('  return str(v)')

OUT.write_text("\n".join(L), encoding="utf-8")
print("Generator ready")
