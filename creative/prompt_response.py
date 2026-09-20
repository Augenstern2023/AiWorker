# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

"""Apply prompt privacy to every creative JSON response, including mutations."""

from functools import wraps
import inspect
import json
from typing import Any

from fastapi import Depends, Response
from fastapi.routing import APIRoute

from creative.project_prompt_service import ProjectPromptService


def prompt_projection_route(service: ProjectPromptService, principal_dependency: Any) -> type[APIRoute]:
    class PromptProjectionRoute(APIRoute):
        def __init__(self, path: str, endpoint: Any, **kwargs: Any) -> None:
            if not getattr(endpoint, "_projects_prompt_snapshots", False):
                original = endpoint
                signature = inspect.signature(original, eval_str=True)

                @wraps(original)
                async def public_endpoint(*args: Any, **values: Any) -> Any:
                    actor = values.pop("_prompt_projection_actor", None) or values.get("principal")
                    result = await original(*args, **values)
                    actor_id = str(getattr(actor, "user_id", "") or "")
                    project_id = values.get("project_id")
                    if isinstance(result, Response):
                        if "application/json" in result.headers.get("content-type", ""):
                            payload = service.public_projection(json.loads(result.body), actor_id, project_id)
                            result.body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                            result.headers["content-length"] = str(len(result.body))
                        return result
                    return service.public_projection(result, actor_id, project_id)

                # Older mutation endpoints rely on router authorization and do
                # not receive a principal argument; resolve their actor without
                # changing their permission policy or implementation signature.
                if "principal" not in signature.parameters:
                    signature = signature.replace(parameters=[
                        *signature.parameters.values(),
                        inspect.Parameter(
                            "_prompt_projection_actor", inspect.Parameter.KEYWORD_ONLY,
                            default=Depends(principal_dependency), annotation=Any,
                        ),
                    ])
                public_endpoint.__signature__ = signature
                public_endpoint._projects_prompt_snapshots = True
                endpoint = public_endpoint
            super().__init__(path, endpoint, **kwargs)

    return PromptProjectionRoute
