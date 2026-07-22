# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/WebGUI.py
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

import sys
import os
import time
import json
import asyncio
import logging
import mimetypes
import re
import threading
import subprocess
import webbrowser
import queue
import shutil
import uuid
from pathlib import Path
from typing import List, Dict, Optional, Any
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

import uvicorn
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from audit.api import build_audit_router
from audit.service import AuditService
from analysis.analytics_api import register_analytics_routes
from analysis.asr import (
    ASRError,
    OpenAICompatibleASRProvider,
    SenseVoiceASRProvider,
    default_sensevoice_script_path,
)
from analysis.llm import LLMError, create_llm_provider_from_config
from analysis.today_rank_api import register_today_rank_routes
from creative.api import register_creative_routes
from creative.delivery import DeliveryService, build_delivery_router
from creative.repository import CreativeRepository
from identity.api import build_identity_router
from identity.password_service import PasswordService
from identity.dependencies import current_principal, require_permission
from identity.permission_service import PermissionService
from identity.repository import IdentityRepository
from identity.schema import Principal
from identity.session_service import SessionService
from agents.douyin_analysis_agent import AnalyzeOptions as DouyinAnalyzeOptions
from agents.douyin_analysis_agent import analyze_rank_file as analyze_douyin_rank_file
from agents.hongguo_analysis_agent import AnalyzeOptions, analyze_rank_file
from agents.hongguo_drama import HongguoDramaClient, HongguoDramaError, load_settings as load_hongguo_drama_settings, public_settings as public_hongguo_drama_settings, save_settings as save_hongguo_drama_settings
from agents.douyin_drama_fallback import DouyinDramaFallbackClient, DouyinFallbackError
from core.job import CrawlJob
from rag.api import register_rag_routes
from runtime.job_manager import JobConflictError, JobManager, JobNotFoundError
from web.navigation_service import NavigationService
from web.page_router import build_page_router
from workflow.api import build_workflow_router
from workflow.repository import WorkflowRepository
from workflow.transition_service import TransitionService


WORKSPACE_ROOT = Path(__file__).resolve().parent
WEB_LOG_LEVEL = os.getenv("AIWORKER_WEB_LOG_LEVEL", "INFO").strip().upper()
WEB_LOG_MAX_BYTES = 5 * 1024 * 1024
WEB_LOG_BACKUP_COUNT = 5
WEB_LOG_PATH = WORKSPACE_ROOT / "logs" / "webgui.log"
SYSTEM_DB_PATH = WORKSPACE_ROOT / ".runtime" / "system" / "system.db"
UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_PATHS = frozenset({"/api/auth/login", "/api/auth/setup"})
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
BACKUP_FILENAME_PATTERN = re.compile(r"^data_backup_[A-Za-z0-9_-]+\.json$")


def resolve_backup_path(filename: str, backup_dir: str | Path = "backups") -> Path:
    """Resolve one generated config backup without allowing directory escape."""
    if Path(filename).name != filename or not BACKUP_FILENAME_PATTERN.fullmatch(filename):
        raise ValueError("invalid backup filename")
    root = Path(backup_dir).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise ValueError("backup path escapes backup directory")
    return candidate


def _api_permission(method: str, path: str) -> str | None:
    """Return the coarse permission for legacy routes without route dependencies."""
    if path.startswith(("/api/auth/", "/api/admin/", "/api/workflow/", "/api/jobs")):
        return None
    if path.startswith("/api/creative/"):
        return None
    if path.startswith(("/api/rank/", "/api/hongguo/analyze-rank", "/api/douyin/analyze-rank")):
        return "rank.read"
    if path.startswith("/api/analytics/"):
        return "analytics.read" if method == "GET" else "*"
    if path.startswith("/api/creative/topics"):
        if method == "GET":
            return "topic.read"
        return "topic.generate" if path.endswith("/generate") else "topic.edit"
    creative_permissions = {
        "/api/creative/breakdowns": "breakdown.read" if method == "GET" else "breakdown.edit",
        "/api/creative/projects": "project.read.assigned" if method == "GET" else "project.create",
        "/api/creative/blueprints": "brief.read" if method == "GET" else "brief.edit",
        "/api/creative/characters": "character.read" if method == "GET" else "character.edit",
        "/api/creative/relationships": "relationship.read" if method == "GET" else "relationship.edit",
        "/api/creative/episodes": "outline.read" if method == "GET" else "outline.edit",
        "/api/creative/workflow": None,
    }
    for prefix, permission in creative_permissions.items():
        if path.startswith(prefix):
            return permission
    if path.startswith("/api/hongguo/drama"):
        if path.startswith("/api/hongguo/drama/settings") or path.endswith("/test"):
            return "*"
        return "drama.download" if "job" in path or "download" in path else "drama.search"
    if path.startswith(("/api/file/analyze", "/api/audio/analyze", "/api/qrcode/")):
        return "job.create.selection"
    if path == "/api/files" and method == "DELETE":
        return "*"
    if path.startswith(("/api/files", "/api/file/", "/api/media/")):
        return "data.media.preview"
    if path.startswith(("/api/run", "/api/batch/", "/api/stop")):
        return "job.create.selection"
    if path.startswith("/api/logs"):
        return "job.read.own"
    return "*"


def _api_audit_action(method: str, path: str) -> str:
    suffix = path.rsplit("/", 1)[-1]
    if path.startswith("/api/workflow/tasks/") and suffix in {
        "claim", "assign", "reassign", "start", "cancel"
    }:
        return f"workflow.{suffix}"
    if path.startswith("/api/workflow/submissions/") and suffix in {
        "submit", "approve", "return", "fork"
    }:
        return f"workflow.{suffix}"
    return f"api.{method.lower()}"


def configure_backend_logging(log_path: Path = WEB_LOG_PATH, log_level: str = WEB_LOG_LEVEL) -> logging.Logger:
    """Configure the Web GUI's rotating server log without changing global loggers."""
    logger = logging.getLogger("aiworker.web")
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level if isinstance(level, int) else logging.INFO)
    logger.propagate = False
    resolved_log_path = log_path.resolve()
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)

    for handler in logger.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename).resolve() == resolved_log_path:
            handler.setLevel(logger.level)
            return logger

    handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=WEB_LOG_MAX_BYTES,
        backupCount=WEB_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setLevel(logger.level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


backend_logger = configure_backend_logging()


def configure_cors(target_app: FastAPI, origins: str | None = None) -> list[str]:
    """Enable credentialed CORS only for explicitly configured origins."""
    configured = os.getenv("AIWORKER_ALLOWED_ORIGINS", "") if origins is None else origins
    allowed_origins = list(dict.fromkeys(
        origin.strip() for origin in configured.split(",") if origin.strip()
    ))
    if "*" in allowed_origins:
        raise ValueError("AIWORKER_ALLOWED_ORIGINS must contain explicit origins")
    if allowed_origins:
        target_app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    return allowed_origins


def install_security_middleware(target_app: FastAPI, session_service=None) -> None:
    """Install authenticated-write CSRF and outer request-ID middleware."""
    if session_service is not None:
        target_app.state.identity_session_service = session_service
        if not isinstance(
            getattr(target_app.state, "identity_permission_service", None),
            PermissionService,
        ):
            target_app.state.identity_permission_service = PermissionService(
                session_service.repository, session_service
            )

    async def enforce_csrf(request: Request, call_next):
        principal = None
        if (
            request.url.path.startswith("/api/")
            and request.url.path not in CSRF_EXEMPT_PATHS
        ):
            service = getattr(request.app.state, "identity_session_service", None)
            cookie_name = getattr(service, "cookie_name", "aiworker_session")
            raw_token = request.cookies.get(cookie_name)
            context = service.resolve_session(raw_token) if service is not None else None
            if context is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "登录已失效，请重新登录"},
                )
            principal = context.principal
            permission_service = getattr(
                request.app.state, "identity_permission_service", None
            )
            if isinstance(permission_service, PermissionService):
                principal = permission_service.principal_for_session(raw_token)
            if principal is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "登录已失效，请重新登录"},
                )
            request.state.principal = principal
            required = _api_permission(request.method.upper(), request.url.path)
            if (
                required is not None
                and not principal.is_system_admin
                and required not in principal.permissions
            ):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "没有执行此操作的权限"},
                )
            if (
                request.method.upper() in UNSAFE_HTTP_METHODS
                and not service.verify_csrf(
                    raw_token, request.headers.get("X-CSRF-Token")
                )
            ):
                return JSONResponse(status_code=403, content={"detail": "CSRF 校验失败"})
        response = await call_next(request)
        if (
            principal is not None
            and request.method.upper() in UNSAFE_HTTP_METHODS
            and request.url.path not in CSRF_EXEMPT_PATHS
            and not request.url.path.startswith("/api/workflow/")
        ):
            audit = getattr(request.app.state, "audit_service", None)
            if isinstance(audit, AuditService):
                target_id = request.url.path.rstrip("/").rsplit("/", 1)[-1]
                path_parts = request.url.path.strip("/").split("/")
                if (
                    len(path_parts) >= 5
                    and path_parts[:3] == ["api", "workflow", "tasks"]
                ):
                    target_id = path_parts[3]
                elif (
                    len(path_parts) >= 5
                    and path_parts[:3] == ["api", "workflow", "submissions"]
                ):
                    target_id = path_parts[3]
                project_id = request.path_params.get("project_id")
                task_id = request.path_params.get("task_id")
                workflow = getattr(request.app.state, "workflow_repository", None)
                if project_id is None and task_id and isinstance(workflow, WorkflowRepository):
                    task = workflow.get_task(str(task_id))
                    project_id = task.get("project_id") if task else None
                audit.record(
                    actor=principal,
                    action=_api_audit_action(request.method.upper(), request.url.path),
                    target_type="api_route",
                    target_id=str(target_id),
                    project_id=str(project_id) if project_id is not None else None,
                    before=None,
                    after={"status_code": response.status_code},
                    reason=None,
                    result="success" if response.status_code < 400 else "failure",
                    request=request,
                )
        return response

    async def attach_request_id(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # Starlette executes the last registered middleware first. Register the
    # request ID last so logging and every inner route can rely on it.
    target_app.middleware("http")(enforce_csrf)
    target_app.middleware("http")(attach_request_id)

# Try to import optional dependencies
try:
    import dict_to_小鸡 as excel_converter
except ImportError:
    excel_converter = None

# Global State
class AppState:
    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.log_queue = queue.Queue()
        self.is_running = False
        self.current_project = ""
        self.batch_queue = []  # List of (project_name, ids)
        self.batch_total = 0
        self.batch_current = 0
        self.stop_requested = False
        self.config_file = "data.json"

    def log(self, message: str, level: str = "info"):
        timestamp = time.strftime("%H:%M:%S")
        # 尝试解码乱码
        try:
             # 如果是bytes，尝试解码
            if isinstance(message, bytes):
                # 尝试用 GBK 解码（Windows控制台常见编码）
                try:
                    message = message.decode('gbk')
                except:
                    # 如果 GBK 失败，尝试 UTF-8，最后用 replace
                    message = message.decode('utf-8', errors='replace')
        except:
            pass

        self.log_queue.put(f"[{timestamp}] [{level.upper()}] {message}")

        # 限制队列大小，只保留最近500条，防止前端卡顿
        while self.log_queue.qsize() > 500:
            try:
                self.log_queue.get_nowait()
            except queue.Empty:
                break

    def clear_logs(self):
        self.log_queue = queue.Queue()

state = AppState()
job_manager = JobManager(cwd=Path(__file__).resolve().parent)

# FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    backend_logger.info("Web GUI started")
    yield
    backend_logger.info("Web GUI shutting down")
    for job in job_manager.list_jobs():
        if job["status"] in {"pending", "running", "stopping"}:
            try:
                job_manager.stop(job["job_id"])
            except Exception:
                pass
    if state.process:
        state.process.terminate()

app = FastAPI(lifespan=lifespan)

identity_repository = IdentityRepository(SYSTEM_DB_PATH)
identity_repository.seed_defaults()
identity_password_service = PasswordService()
identity_session_service = SessionService(identity_repository)
identity_permission_service = PermissionService(
    identity_repository, identity_session_service
)
audit_service = AuditService(identity_repository)
workflow_repository = WorkflowRepository(identity_repository)
workflow_transition_service = TransitionService(workflow_repository)
creative_repository = CreativeRepository(
    WORKSPACE_ROOT / ".runtime" / "creative" / "creative.db"
)
delivery_service = DeliveryService(
    root=WORKSPACE_ROOT,
    creative_repository=creative_repository,
    workflow_repository=workflow_repository,
    audit_service=audit_service,
)
app.state.identity_repository = identity_repository
app.state.identity_password_service = identity_password_service
app.state.identity_session_service = identity_session_service
app.state.identity_permission_service = identity_permission_service
app.state.audit_service = audit_service
app.state.workflow_repository = workflow_repository
app.state.workflow_transition_service = workflow_transition_service
templates = Jinja2Templates(directory=str(WORKSPACE_ROOT / "templates"))


@app.middleware("http")
async def log_backend_requests(request, call_next):
    started_at = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        backend_logger.exception(
            "Unhandled request error: %s %s request_id=%s",
            request.method,
            request.url.path,
            getattr(request.state, "request_id", None),
        )
        raise

    duration_ms = (time.perf_counter() - started_at) * 1000
    backend_logger.info(
        "%s %s status=%s duration_ms=%.1f request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
        getattr(request.state, "request_id", None),
    )
    return response

install_security_middleware(app, identity_session_service)
configure_cors(app)
app.include_router(
    build_identity_router(
        identity_repository,
        identity_session_service,
        audit_service,
        password_service=identity_password_service,
        permission_service=identity_permission_service,
    )
)
app.include_router(build_audit_router(audit_service))
app.include_router(build_delivery_router(delivery_service))
app.include_router(
    build_workflow_router(workflow_repository, workflow_transition_service)
)
app.include_router(build_page_router(templates, NavigationService()))
register_rag_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
register_analytics_routes(app, root=Path(__file__).resolve().parent, runtime_dir=Path(__file__).resolve().parent / ".runtime")
register_today_rank_routes(app, root=Path(__file__).resolve().parent)
register_creative_routes(
    app,
    root=Path(__file__).resolve().parent,
    runtime_dir=Path(__file__).resolve().parent / ".runtime",
    job_manager=job_manager,
)

# Models
class CrawlerConfig(BaseModel):
    platform: str
    login_type: str
    crawler_type: str
    keywords: str
    start_page: int
    headless: bool
    save_option: str
    get_comment: bool
    get_sub_comment: bool
    specified_ids: str = ""
    creator_ids: str = ""
    cookie: str = ""

    # Douyin specific
    dy_channel: str = "video"
    dy_sort: str = "latest"
    dy_publish_time: str = "1_week"
    dy_rank_limit: int = 50
    dy_rank_comment_limit: int = 10
    dy_rank_login_wait_seconds: float = 0.0

    # Project specific
    projects: Dict[str, bool] = {}

    # Auto Run
    autoRun: bool = False

class BatchTask(BaseModel):
    project_names: List[str]


class FileDeleteRequest(BaseModel):
    paths: List[str]


class HongguoDramaSettingsRequest(BaseModel):
    config_url: Optional[str] = None
    activation_code: Optional[str] = None


class HongguoDramaSearchRequest(BaseModel):
    title: str


class HongguoDramaEpisodesRequest(BaseModel):
    book_id: str


class HongguoDramaJobRequest(BaseModel):
    title: str
    book_id: str
    episode: int
    workflow_task_id: str | None = None


class HongguoDramaDownloadJobRequest(BaseModel):
    title: str
    book_id: str
    episodes: List[int] = []
    all_episodes: bool = False
    workflow_task_id: str | None = None


class DouyinFallbackDramaJobRequest(BaseModel):
    title: str
    aweme_id: str = ""
    aweme_ids: List[str] = []
    fallback_reason: str = ""
    workflow_task_id: str | None = None


class ConfigContent(BaseModel):
    content: str

class BackupAction(BaseModel):
    filename: str

class ConfigFormPayload(BaseModel):
    root: Dict[str, Any]
    items: List[Dict[str, Any]] = []

class AudioAnalyzeRequest(BaseModel):
    file_path: str

class HongguoRankAnalyzeRequest(BaseModel):
    file_path: str
    top_n: int = 10
    episode_limit: int = 1
    fetch_details: bool = False
    fetch_play_urls: bool = False
    download_media: bool = False
    with_asr: bool = False
    with_llm: bool = False
    keep_video: bool = True
    media_limit_mb: int = 300

class DouyinRankAnalyzeRequest(BaseModel):
    file_path: str
    top_n: int = 10
    with_llm: bool = True
    per_category: bool = True
    download_media: bool = False
    with_asr: bool = False

class MediaDownloadRequest(BaseModel):
    file_path: str
    media_type: str  # "video" or "music"

class MediaItem(BaseModel):
    aweme_id: str
    title: str
    url: str
    filename: str

class MediaListResponse(BaseModel):
    videos: List[MediaItem]
    musics: List[MediaItem]


class JobCreateRequest(BaseModel):
    platform: str
    login_type: str = "qrcode"
    crawler_type: str = "search"
    keywords: str = ""
    start_page: int = 1
    headless: bool = False
    save_option: str = "json"
    get_comment: bool = True
    get_sub_comment: bool = False
    specified_ids: str = ""
    creator_ids: str = ""
    cookie: str = ""
    project_map: Dict[str, List[str]] = {}
    project_name: str = ""
    extra: Dict[str, Any] = {}


def _require_permission(principal: Principal, code: str) -> None:
    if not principal.is_system_admin and code not in principal.permissions:
        raise HTTPException(403, "没有执行此操作的权限")


_GENERIC_SELECTION_JOB_TYPES = {
    "hg": frozenset({"rank"}),
    "dy": frozenset({"search", "detail", "creator", "rank"}),
    "xhs": frozenset({"search", "detail", "creator"}),
    "ks": frozenset({"search", "detail", "creator"}),
    "bili": frozenset({"search", "detail", "creator"}),
    "wb": frozenset({"search", "detail", "creator"}),
    "tieba": frozenset({"search", "detail", "creator"}),
    "zhihu": frozenset({"search", "detail", "creator"}),
}
_CLIENT_JOB_OWNERSHIP_FIELDS = frozenset(
    {"created_by_user_id", "workflow_task_id", "project_id"}
)


def _validate_generic_selection_job(platform: str, crawler_type: str) -> None:
    allowed_types = _GENERIC_SELECTION_JOB_TYPES.get(platform)
    if allowed_types is None or crawler_type not in allowed_types:
        raise HTTPException(400, "该机器任务类型必须使用专用创建接口")


def _job_task_context(
    workflow_task_id: str | None, principal: Principal
) -> tuple[str | None, str | None]:
    if not workflow_task_id:
        return None, None
    task = workflow_repository.get_visible_task(workflow_task_id, principal)
    if task is None:
        raise HTTPException(403, "没有访问此工作流任务的权限")
    if not principal.is_system_admin and task.get("assignee_user_id") != principal.user_id:
        raise HTTPException(403, "只能为自己负责的工作流任务创建机器任务")
    return workflow_task_id, task.get("project_id")


def _public_created_job(job_state) -> dict[str, Any]:
    return job_manager.get_job(job_state.job_id)


def _record_job_audit(
    request: Request | None,
    principal: Principal,
    action: str,
    job: dict[str, Any],
    *,
    before: Any = None,
) -> None:
    audit_service.record(
        actor=principal,
        action=action,
        target_type="job",
        target_id=job.get("job_id"),
        project_id=(str(job["project_id"]) if job.get("project_id") is not None else None),
        before=before,
        after=job,
        reason=None,
        result="success",
        request=request,
    )


def _job_creation_permission(job: dict[str, Any]) -> str:
    payload = job.get("job") if isinstance(job.get("job"), dict) else job
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    if payload.get("platform") == "creative":
        action = str(extra.get("action") or "")
        if action in {"outlines", "beats"}:
            return "job.create.writing"
        if action in {"blueprints", "characters"}:
            return "job.create.creative"
    return "job.create.selection"


def _can_read_job(job: dict[str, Any], principal: Principal) -> bool:
    if principal.is_system_admin:
        return True
    creator_id = job.get("created_by_user_id")
    project_id = job.get("project_id")
    if creator_id == principal.user_id and "job.read.own" in principal.permissions:
        return True
    if project_id is None or "job.read.project" not in principal.permissions:
        return False
    return any(
        member["user_id"] == principal.user_id and member["status"] == "active"
        for member in workflow_repository.list_project_members(str(project_id))
    )


def _assert_can_read_job(job: dict[str, Any], principal: Principal) -> None:
    if not _can_read_job(job, principal):
        raise HTTPException(403, "没有访问此机器任务的权限")


def _assert_creator_has_live_job_scope(
    job: dict[str, Any], principal: Principal
) -> None:
    if job.get("created_by_user_id") != principal.user_id:
        raise HTTPException(403, "只有机器任务创建人可以执行此操作")
    required_permission = _job_creation_permission(job)
    if (
        "job.cancel.own" not in principal.permissions
        or required_permission not in principal.permissions
    ):
        raise HTTPException(403, "创建人当前不再具有此机器任务的操作权限")

    task_id = job.get("workflow_task_id")
    project_id = job.get("project_id")
    if task_id is not None:
        task = workflow_repository.get_visible_task(str(task_id), principal)
        if task is None or task.get("assignee_user_id") != principal.user_id:
            raise HTTPException(403, "关联工作流任务已不再分配给机器任务创建人")
        task_project_id = task.get("project_id")
        if (task_project_id is None) != (project_id is None) or (
            task_project_id is not None
            and str(task_project_id) != str(project_id)
        ):
            raise HTTPException(403, "机器任务的项目范围与工作流任务不一致")

    if project_id is not None:
        active_member = any(
            member["user_id"] == principal.user_id
            and member["status"] == "active"
            for member in workflow_repository.list_project_members(str(project_id))
        )
        if not active_member:
            raise HTTPException(403, "机器任务创建人已不再是项目成员")


def _assert_can_stop_job(job: dict[str, Any], principal: Principal) -> None:
    if principal.is_system_admin:
        return
    creator_id = job.get("created_by_user_id")
    project_id = job.get("project_id")
    if creator_id is None and project_id is None:
        raise HTTPException(403, "历史机器任务只能由系统管理员控制")
    if creator_id == principal.user_id:
        _assert_creator_has_live_job_scope(job, principal)
        return
    if project_id is not None:
        try:
            workflow_repository.assert_can_manage_project(str(project_id), principal)
            return
        except PermissionError:
            pass
    raise HTTPException(403, "没有控制此机器任务的权限")


def _assert_can_mutate_owned_job(
    job: dict[str, Any], principal: Principal
) -> None:
    if principal.is_system_admin:
        return
    if job.get("created_by_user_id") is None:
        raise HTTPException(403, "历史机器任务只能由系统管理员控制")
    _assert_creator_has_live_job_scope(job, principal)

DATA_ROOT = (WORKSPACE_ROOT / "data").resolve()
CONFIG_ITEM_FIELDS = [
    "project",
    "评论不少于",
    "搜索关键词",
    "屏蔽关键词",
    "有关视频判定",
    "黑名单视频判定",
    "作品天数",
    "白名单地区",
    "黑名单地区",
]
CONTENT_EXTENSIONS = {".json", ".md", ".csv", ".xlsx", ".xls"}
MEDIA_EXTENSIONS = {".wav", ".mp3", ".m4a", ".m4s", ".aac", ".flac", ".ogg", ".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_DATA_EXTENSIONS = CONTENT_EXTENSIONS | MEDIA_EXTENSIONS | {".txt", ".log"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".m4s", ".aac", ".flac", ".ogg"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
TEXT_PREVIEW_EXTENSIONS = {".md", ".txt", ".json"}
TEXT_PREVIEW_MAX_BYTES = 5 * 1024 * 1024


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_data_file_path(path_value: str) -> Path:
    raw_path = Path(path_value)
    candidate = raw_path if raw_path.is_absolute() else WORKSPACE_ROOT / raw_path
    resolved = candidate.resolve()
    if not _is_relative_to(resolved, DATA_ROOT):
        raise HTTPException(400, "Path is outside data directory")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, "File not found")
    return resolved


def display_data_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(WORKSPACE_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def classify_data_file(path: Path) -> Dict[str, str]:
    suffix = path.suffix.lower()
    name = path.name.lower()
    if suffix in AUDIO_EXTENSIONS:
        return {"category": "媒体文件", "legacy_category": "Media", "media_type": "audio"}
    if suffix in {".mp4", ".mov", ".mkv", ".webm"}:
        return {"category": "媒体文件", "legacy_category": "Media", "media_type": "video"}
    if suffix in CONTENT_EXTENSIONS:
        legacy = "unknown"
        if "comments" in name:
            legacy = "Comments"
        elif "contents" in name:
            legacy = "Contents"
        return {"category": "内容数据", "legacy_category": legacy, "media_type": ""}
    if suffix in {".log", ".txt"}:
        return {"category": "任务文件", "legacy_category": "Task", "media_type": ""}
    return {"category": "未识别", "legacy_category": "unknown", "media_type": ""}


def describe_data_file(path: Path, classification: Dict[str, str]) -> Dict[str, Any]:
    try:
        parts = path.resolve().relative_to(DATA_ROOT).parts
    except ValueError:
        parts = path.parts
    drama_name = "其他数据"
    episode_label = ""
    if "dramas" in parts:
        drama_index = parts.index("dramas")
        if drama_index + 1 < len(parts):
            drama_name = parts[drama_index + 1]
        for part in parts[drama_index + 2:]:
            episode_match = re.fullmatch(r"episode_(\d+)", part, re.IGNORECASE)
            item_match = re.match(r"item_(\d+)_", part, re.IGNORECASE)
            if episode_match:
                episode_label = f"第 {int(episode_match.group(1))} 集"
                break
            if item_match:
                episode_label = f"视频 {int(item_match.group(1))}"
                break

    suffix = path.suffix.lower()
    lower_name = path.name.lower()
    if classification.get("media_type") == "video":
        file_group, preview_kind = "视频", "video"
    elif classification.get("media_type") == "audio":
        file_group, preview_kind = "音频", "audio"
    elif lower_name == "transcript.json" or "transcript" in lower_name:
        file_group, preview_kind = "转写数据", "json"
    elif lower_name in {"source.json", "manifest.json"} or lower_name.endswith("_manifest.json"):
        file_group, preview_kind = "元数据", "json"
    elif suffix in {".md", ".txt"}:
        file_group, preview_kind = "剧本与文稿", "text"
    elif suffix == ".json":
        file_group, preview_kind = "元数据", "json"
    else:
        file_group, preview_kind = "其他文件", "none"
    return {
        "drama_name": drama_name,
        "file_group": file_group,
        "episode_label": episode_label,
        "preview_kind": preview_kind,
    }


def filter_matches_file(filter_value: str, file_info: Dict[str, Any]) -> bool:
    if filter_value in ("", "all", None):
        return True
    value = str(filter_value).lower()
    return value in {
        str(file_info.get("category", "")).lower(),
        str(file_info.get("legacy_category", "")).lower(),
        str(file_info.get("type", "")).lower(),
        str(file_info.get("media_type", "")).lower(),
        str(file_info.get("file_group", "")).lower(),
    }


def normalize_config_root(data: Any) -> Dict[str, Any]:
    if isinstance(data, list) and data:
        return data[0] if isinstance(data[0], dict) else {}
    return data if isinstance(data, dict) else {}


def read_config_data() -> Any:
    if not os.path.exists(state.config_file):
        return {}
    with open(state.config_file, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def config_output_as_list(existing_data: Any) -> bool:
    return isinstance(existing_data, list)


def clean_config_item(item: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for key in CONFIG_ITEM_FIELDS:
        value = item.get(key)
        if key in {"评论不少于", "作品天数"}:
            try:
                cleaned[key] = int(value)
            except (TypeError, ValueError):
                cleaned[key] = 0
        else:
            cleaned[key] = "" if value is None else str(value)
    return cleaned


def create_asr_provider():
    provider = getattr(config, "BILI_ASR_PROVIDER", "openai")
    if provider == "openai":
        return OpenAICompatibleASRProvider(
            base_url=getattr(config, "ASR_BASE_URL", ""),
            api_key=getattr(config, "ASR_API_KEY", ""),
            model=getattr(config, "ASR_MODEL", "whisper-1"),
        )
    if provider == "sensevoice":
        return SenseVoiceASRProvider(
            python_path=getattr(config, "SENSEVOICE_PYTHON", ""),
            model_dir=getattr(config, "SENSEVOICE_MODEL_DIR", ""),
            ffmpeg_dir=getattr(config, "SENSEVOICE_FFMPEG_DIR", ""),
            device=getattr(config, "SENSEVOICE_DEVICE", "cuda:0"),
            script_path=getattr(config, "SENSEVOICE_SCRIPT_PATH", default_sensevoice_script_path()),
        )
    raise ASRError(f"Unsupported ASR provider: {provider}")


def create_llm_provider():
    return create_llm_provider_from_config(config)

# --- Helper Functions ---

def read_output(process):
    for line in process.stdout:
        state.log(line.strip(), "info")
    state.is_running = False
    state.process = None
    state.log("message", "success")

def run_crawler_thread(command: List[str], config: CrawlerConfig = None):
    try:
        state.is_running = True
        state.log(f"执行命令: {' '.join(command)}", "info")

        # 在 Windows 上，设置环境变量 PYTHONIOENCODING=utf-8 可以强制 Python 输出 UTF-8
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        state.process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
            encoding='utf-8', # 明确指定子进程输出编码为 utf-8
            errors='replace',
            env=env # 传递环境变量
        )

        # Read output in a separate thread to not block
        # Wait for completion in a separate thread to handle autoRun
        # Capture the process object locally to avoid race conditions when state.process is cleared
        process = state.process

        def monitor_process():
            # Use local process reference
            read_output(process)

            # process is already finished here because read_output returns when stdout closes
            exit_code = process.wait()

            if exit_code == 0 and config and config.autoRun:
                state.log("检测到开启了[自动托管]，准备执行后续任务...", "info")
                # 触发自动任务
                threading.Thread(target=auto_run_sequence, args=(config,), daemon=True).start()

        threading.Thread(target=monitor_process, daemon=True).start()

    except Exception as e:
        state.is_running = False
        state.log(f"启动失败: {str(e)}", "error")

def close_edge_browser():
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            state.log("message", "info")
    except Exception as e:
        state.log(f"[自动托管] 关闭Edge浏览器失败: {e}", "warning")

def auto_run_sequence(config: CrawlerConfig):
    """自动执行后续任务：Contents -> Comments -> Excel -> Upload"""
    try:
        # 1. 查找最新的 Contents 文件
        data_dir = Path("./data")
        contents_files = sorted(
            [f for f in data_dir.rglob("*.json") if "contents" in f.name.lower()],
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )

        if not contents_files:
            state.log("message", "error")
            return

        latest_file = contents_files[0]
        state.log(f"[自动托管] 找到最新文件: {latest_file.name}", "info")

        # 2. 分析文件中的项目
        # 简单起见，我们直接读取文件内容，然后根据 config.projects 过滤
        # 或者如果 config.projects 为空，则处理所有项目

        projects_to_process = []
        if config.projects:
            projects_to_process = [p for p, v in config.projects.items() if v]

        # 如果没有明确指定，尝试从文件中读取所有项目
        if not projects_to_process:
             try:
                with open(latest_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    projects_to_process = list(data.keys())
                elif isinstance(data, list):
                    projects_to_process = ["Default"]
             except:
                 pass

        if not projects_to_process:
            state.log("[自动托管] 未识别到需要处理的项目", "error")
            return

        state.log(f"[自动托管] 即将处理项目: {', '.join(projects_to_process)}", "info")

        # 3. 构造 BatchRequest 并复用 batch_worker 逻辑
        # 我们需要先提取 IDs
        try:
            with open(latest_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            queue_items = []
            for proj in projects_to_process:
                ids = []
                if isinstance(data, dict):
                    items = data.get(proj, [])
                else:
                    items = data # Default

                for item in items:
                    oid = item.get("aweme_id") or item.get("note_id") or item.get("id")
                    if oid:
                        ids.append(oid)

                if ids:
                    queue_items.append((proj, ids))

            if not queue_items:
                 state.log("[自动托管] 未提取到有效ID", "error")
                 return

            state.batch_queue = queue_items
            state.stop_requested = False

            # 启动批量爬取评论
            # 修改：每爬取一个项目，就立即导出和上传

            # batch_worker 是同步阻塞的，所以直接调用
            # 我们不再使用 batch_worker 的批量队列模式，而是手动循环
            # state.log("[自动托管] 开始批量爬取评论...", "info")
            # batch_worker() # 这会阻塞直到所有评论爬取完成

            # 手动循环
            total = len(queue_items)
            for i, (project_name, ids) in enumerate(queue_items):
                current_idx = i + 1
                state.current_project = project_name
                state.log(f"--- [自动托管] 开始处理项目 [{current_idx}/{total}]: {project_name} ---", "info")

                # 1. 爬取评论
                command = build_comment_command(ids, project_name)

                try:
                    env = os.environ.copy()
                    env["PYTHONIOENCODING"] = "utf-8"

                    state.process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        universal_newlines=True,
                        encoding='utf-8',
                        errors='replace',
                        env=env
                    )
                    state.is_running = True

                    for line in state.process.stdout:
                        state.log(line.strip(), "info")

                    exit_code = state.process.wait()
                    state.is_running = False
                    state.process = None

                    if exit_code != 0:
                        state.log(f"项目 {project_name} 爬取失败 (Code: {exit_code})", "error")
                        continue # 失败则跳过导出上传

                    state.log(f"项目 {project_name} 爬取完成", "success")

                    # 2. 导出并上传 (立即执行)
                    if excel_converter:
                        # 重新扫描最新的 Comments 文件
                        # 等待一下文件系统
                        time.sleep(2)
                        current_comments_files = sorted(
                            [f for f in data_dir.rglob("*.json") if "comments" in f.name.lower()],
                            key=lambda x: x.stat().st_mtime,
                            reverse=True
                        )

                        if current_comments_files:
                            latest_c_file = current_comments_files[0]
                            # 简单检查时间 (2分钟内)
                            if time.time() - latest_c_file.stat().st_mtime < 120:
                                state.log(f"正在导出项目 {project_name} (使用文件: {latest_c_file.name})", "info")
                                try:
                                    with open(latest_c_file, "r", encoding="utf-8") as f:
                                        data = json.load(f)

                                    items = data.get(project_name) if isinstance(data, dict) else data
                                    if items:
                                        temp_file = f"temp_{project_name}.json"
                                        with open(temp_file, "w", encoding="utf-8") as tf:
                                            json.dump({project_name: items}, tf, ensure_ascii=False, indent=4)

                                        excel_path = excel_converter.convert_json_to_excel(temp_file)
                                        state.log(f"导出成功: {excel_path}", "success")

                                        # 上传
                                        upload_res = None
                                        max_retries = 10
                                        for attempt in range(max_retries):
                                            upload_res = upload_excel_to_interface(excel_path, project_override=project_name)
                                            if upload_res and "error" not in upload_res:
                                                state.log(f"项目 {project_name} 上传成功", "success")
                                                break
                                            else:
                                                err = upload_res.get("error") if upload_res else "未知错误"
                                                state.log(f"项目 {project_name} 上传失败 (第 {attempt+1}/{max_retries} 次): {err}", "error")
                                                if attempt < max_retries - 1:
                                                    state.log("等待 5 秒后重试...", "info")
                                                    time.sleep(5)

                                        if not upload_res or "error" in upload_res:
                                             state.log(f"project {project_name} upload failed after retries", "error")

                                        if os.path.exists(temp_file):
                                            os.remove(temp_file)
                                    else:
                                        state.log(f"project {project_name} has no data in file", "warning")
                                except Exception as e:
                                    state.log(f"导出/上传出错: {str(e)}", "error")
                            else:
                                state.log("recent comments file not found", "warning")
                        else:
                            state.log("未找到评论文件，跳过导出", "warning")
                    else:
                        state.log("缺少导出模块，无法导出Excel", "error")

                except Exception as e:
                    state.is_running = False
                    state.log(f"项目 {project_name} 执行出错: {str(e)}", "error")

                # 项目间稍微停顿
                if current_idx < total and not state.stop_requested:
                    state.log("等待 3 秒后开始下一个项目...", "info")
                    time.sleep(3)

                if state.stop_requested:
                    state.log("用户请求停止", "warning")
                    break

            state.log("message", "success")
            close_edge_browser()
            state.current_project = ""
            state.batch_total = 0
            state.batch_current = 0

            # 删除旧的 Step 4 代码
            return

        except Exception as e:
            state.log(f"[自动托管] 流程出错: {str(e)}", "error")
            close_edge_browser()

    except Exception as e:
        state.log(f"[自动托管] 全局错误: {str(e)}", "error")
        close_edge_browser()

def upload_excel_to_interface(file_path, project_override=""):
    """WebGUI 版本的上传逻辑"""
    try:
        import requests
        # 读取配置
        if os.path.exists(state.config_file):
            with open(state.config_file, "r", encoding="utf-8") as f:
                data_config = json.load(f)
                if isinstance(data_config, list): data_config = data_config[0]

                host = data_config.get("host")
                if not host: return {"error": "未配置host"}

                # 获取上传配置
                upload_config = data_config.get("upload_config", {})
                project = project_override if project_override else upload_config.get("project")

                # 查找 username
                username = upload_config.get("username")
                items = data_config.get("items", [])
                if project:
                    for item in items:
                        if item.get("project") == project:
                            item_username = item.get("username")
                            if item_username: username = item_username
                            break

                if not username or not project:
                    return {"error": "缺少username或project配置"}

                url = f"{host.rstrip('/')}/upload/douyin"
                payload = {
                    "username": username,
                    "project": project,
                    "task": upload_config.get("task", 1)
                }

                with open(file_path, "rb") as f:
                    response = requests.post(url, data=payload, files={"file": f}, timeout=300)

                if response.status_code == 200:
                    return response.json().get("data", {})
                else:
                    return {"error": f"HTTP {response.status_code}"}
    except Exception as e:
        return {"error": str(e)}
    return {"error": "配置读取失败"}

def batch_worker():
    """Worker thread for batch processing"""
    state.batch_total = len(state.batch_queue)
    state.batch_current = 0

    while state.batch_queue and not state.stop_requested:
        project_name, ids = state.batch_queue.pop(0)
        state.batch_current += 1
        state.current_project = project_name

        state.log(f"--- 开始处理项目 [{state.batch_current}/{state.batch_total}]: {project_name} ---", "info")

        command = build_comment_command(ids, project_name)

        try:
            # 同样为批量任务设置环境变量
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            state.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                 errors='replace',
                 env=env
            )
            state.is_running = True

            for line in state.process.stdout:
                state.log(line.strip(), "info")

            exit_code = state.process.wait()
            state.is_running = False
            state.process = None

            if exit_code == 0:
                state.log(f"项目 {project_name} 完成", "success")
            else:
                state.log(f"项目 {project_name} 失败 (Code: {exit_code})", "error")

        except Exception as e:
            state.is_running = False
            state.log(f"项目 {project_name} 执行出错: {str(e)}", "error")

        if state.batch_queue and not state.stop_requested:
            state.log("等待 3 秒后继续...", "info")
            time.sleep(3)

    state.log("批量任务全部结束", "success")
    state.current_project = ""
    state.batch_total = 0
    state.batch_current = 0
    state.stop_requested = False


def build_command(config: CrawlerConfig, project_map_json: str = "{}"):
    command = ["uv", "run", "main.py"]
    command.extend(["--platform", config.platform.split()[0]])
    command.extend(["--lt", config.login_type.split()[0]])
    command.extend(["--type", config.crawler_type.split()[0]])
    command.extend(["--start", str(config.start_page)])

    if config.keywords:
        command.extend(["--keywords", config.keywords])

    if project_map_json != "{}":
        command.extend(["--project_map", project_map_json])

    command.extend(["--get_comment", "yes" if config.get_comment else "no"])

    # Logic from original GUI
    if config.crawler_type.startswith("detail"):
        command.extend(["--get_comment", "yes"])
        command.extend(["--get_sub_comment", "yes"])
    else:
        command.extend(["--get_sub_comment", "yes" if config.get_sub_comment else "no"])

    command.extend(["--headless", "yes" if config.headless else "no"])
    command.extend(["--save_data_option", config.save_option.split()[0]])

    # 处理Cookie - 如果是二维码登录方式，尝试加载已保存的登录状态
    cookie = config.cookie or ""
    state.log(f"cookie length: {len(cookie) if cookie else 0}", "info")

    if config.login_type == "qrcode":
        state.log("loading qrcode login state", "info")
        try:
            state_file = Path(QRCODE_LOGIN_STATE_FILE)
            state.log(f"[调试] 查找登录状态文件: {state_file.absolute()}", "info")
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    state_data = json.load(f)
                timestamp = state_data.get("timestamp", 0)
                state.log(f"[调试] 找到登录状态文件，时间戳: {timestamp}", "info")
                # 检查是否过期（7天）
                if time.time() - timestamp <= 7*30 * 24 * 3600:
                    saved_cookies = state_data.get("cookies", [])
                    state.log(f"[调试] 保存的cookies数量: {len(saved_cookies)}", "info")
                    if saved_cookies:
                        # 将cookies转换为字符串格式
                        saved_cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in saved_cookies])
                        state.log(f"[调试] 转换后的cookie字符串长度: {len(saved_cookie_str)}", "info")
                        if saved_cookie_str:
                            if cookie:
                                cookie = cookie + "; " + saved_cookie_str
                            else:
                                cookie = saved_cookie_str
                            state.log(f"[信息] 已加载保存的二维码登录状态，cookie长度: {len(cookie)}", "info")
                else:
                    state.log("[警告] 二维码登录状态已过期，请重新扫码", "warning")
            else:
                state.log("[警告] 未找到二维码登录状态，请先扫码登录", "warning")
        except Exception as e:
            state.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")

    state.log(f"[调试] 最终cookie长度: {len(cookie) if cookie else 0}", "info")

    if cookie:
        command.extend(["--cookies", cookie])

    if config.specified_ids:
        # 检查指定ID列表长度，如果太长则写入临时文件
        if len(config.specified_ids) > 3000:
            temp_ids_file = f"temp_specified_ids_{int(time.time())}.txt"
            with open(temp_ids_file, "w", encoding="utf-8") as f:
                f.write(config.specified_ids)
            command.extend(["--specified_id_file", temp_ids_file])
            state.log(f"[信息] 指定ID列表较长，已写入临时文件: {temp_ids_file}", "info")
        else:
            command.extend(["--specified_id", config.specified_ids])

    if config.creator_ids:
        # 检查创作者ID列表长度，如果太长则写入临时文件
        if len(config.creator_ids) > 3000:
            temp_ids_file = f"temp_creator_ids_{int(time.time())}.txt"
            with open(temp_ids_file, "w", encoding="utf-8") as f:
                f.write(config.creator_ids)
            command.extend(["--creator_id_file", temp_ids_file])
            state.log(f"[信息] 创作者ID列表较长，已写入临时文件: {temp_ids_file}", "info")
        else:
            command.extend(["--creator_id", config.creator_ids])

    # Douyin specifics
    if config.platform.startswith("dy"):
        channel_map = {
            "general": "aweme_general", "video": "aweme_video_web",
            "user": "aweme_user_web", "live": "aweme_live"
        }
        val = config.dy_channel.split()[0]
        if val in channel_map:
            command.extend(["--dy_channel", channel_map[val]])

        sort_map = {"general": "0", "most_like": "1", "latest": "2"}
        val = config.dy_sort.split()[0]
        if val in sort_map:
            command.extend(["--dy_sort", sort_map[val]])

        time_map = {"unlimited": "0", "1_day": "1", "1_week": "7", "6_months": "180"}
        val = config.dy_publish_time.split()[0]
        if val in time_map:
            command.extend(["--dy_publish_time", time_map[val]])

    # Auto close
    command.extend(["--auto_close", "yes"])

    return command


def _split_ids(value: str) -> List[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _get_project_keywords(item: Dict[str, Any]) -> str:
    for key in ("keywords", "keyword", "搜索关键词", "检索关键词"):
        value = item.get(key)
        if value:
            return str(value)
    for key, value in item.items():
        key_text = str(key).lower()
        if "keyword" in key_text or "关键词" in key_text:
            return str(value)
    return ""


def _douyin_extra(config: CrawlerConfig) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    if config.platform.startswith("dy"):
        channel_map = {
            "general": "aweme_general",
            "video": "aweme_video_web",
            "user": "aweme_user_web",
            "live": "aweme_live",
        }
        sort_map = {"general": 0, "most_like": 1, "latest": 2}
        time_map = {"unlimited": 0, "1_day": 1, "1_week": 7, "6_months": 180}
        extra["SEARCH_CHANNEL"] = channel_map.get(config.dy_channel.split()[0], config.dy_channel)
        extra["SEARCH_SORT_TYPE"] = sort_map.get(config.dy_sort.split()[0], 2)
        extra["PUBLISH_TIME_TYPE"] = time_map.get(config.dy_publish_time.split()[0], 180)
    return extra


def _read_config_root() -> Dict[str, Any]:
    try:
        if not os.path.exists(state.config_file):
            return {}
        with open(state.config_file, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, list) and data:
            return data[0] if isinstance(data[0], dict) else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _config_first_text(source: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        text = str(value or "").strip()
        if text and text.upper() not in {"INVALID_DIAG", "<REDACTED>", "REDACTED"}:
            return text
    return ""


def create_job_from_config(config: CrawlerConfig, project_map_json: str = "{}") -> CrawlJob:
    try:
        project_map = json.loads(project_map_json) if project_map_json else {}
    except Exception:
        project_map = {}

    get_comment = config.get_comment
    get_sub_comment = config.get_sub_comment
    if config.crawler_type.startswith("detail"):
        get_comment = True
        get_sub_comment = True

    platform = config.platform.split()[0]
    extra = _douyin_extra(config)
    if platform == "dy" and config.crawler_type.split()[0] == "rank":
        extra.update(
            {
                "DOUYIN_RANK_LIMIT": config.dy_rank_limit,
                "DOUYIN_RANK_COMMENT_LIMIT": config.dy_rank_comment_limit,
                "DOUYIN_RANK_LOGIN_WAIT_SECONDS": config.dy_rank_login_wait_seconds,
                "DOUYIN_AUTO_ANALYZE_TOP10": True,
                "DOUYIN_ANALYSIS_TOP_N": 10,
                "DOUYIN_ANALYSIS_PER_CATEGORY": True,
                "DOUYIN_ANALYSIS_WITH_LLM": True,
            }
        )

    return CrawlJob(
        job_id=job_manager.generate_job_id(platform),
        platform=platform,
        crawler_type=config.crawler_type.split()[0],
        keywords=_split_ids(config.keywords),
        login_type=config.login_type.split()[0],
        save_data_option=config.save_option.split()[0],
        start_page=config.start_page,
        get_comments=get_comment,
        get_sub_comments=get_sub_comment,
        headless=config.headless,
        auto_close=True,
        project_keyword_map=project_map,
        specified_ids=_split_ids(config.specified_ids),
        creator_ids=_split_ids(config.creator_ids),
        cookies=config.cookie or "",
        extra=extra,
    )


def _apply_project_selection(config: CrawlerConfig) -> str:
    project_map_json = "{}"
    project_items = []
    try:
        if os.path.exists(state.config_file):
            with open(state.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    project_items = data[0].get("items", [])
                elif isinstance(data, dict):
                    project_items = data.get("items", [])
    except Exception:
        project_items = []

    if project_items and config.projects:
        selected_keywords = set()
        keyword_to_projects: Dict[str, List[str]] = {}
        for item in project_items:
            project_name = item.get("project")
            if not config.projects.get(project_name):
                continue
            keyword_text = _get_project_keywords(item)
            for keyword in [item.strip() for item in keyword_text.split(",") if item.strip()]:
                selected_keywords.add(keyword)
                keyword_to_projects.setdefault(keyword, []).append(project_name)
        if selected_keywords:
            config.keywords = ",".join(selected_keywords)
            project_map_json = json.dumps(keyword_to_projects, ensure_ascii=False)
    return project_map_json


async def run_crawler_as_job(
    config: CrawlerConfig,
    principal: Principal | None = None,
    request: Request | None = None,
):
    project_map_json = _apply_project_selection(config)
    platform = config.platform.split()[0]
    crawler_type = config.crawler_type.split()[0]
    if platform not in {"hg"} and crawler_type != "rank" and not config.keywords and not config.specified_ids and not config.creator_ids:
        return JSONResponse({"status": "error", "message": "missing keywords or ids"}, status_code=400)
    try:
        job = create_job_from_config(config, project_map_json)
        job_state = job_manager.create_job(
            job,
            created_by_user_id=(
                principal.user_id if isinstance(principal, Principal) else None
            ),
        )
        public_job = _public_created_job(job_state)
        if isinstance(principal, Principal):
            _record_job_audit(request, principal, "job.create", public_job)
        return {
            "status": "success",
            "message": "crawler started",
            "job": public_job,
        }
    except JobConflictError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

def build_comment_command(ids: List[str], project_name: str):
    command = ["uv", "run", "main.py"]
    command.extend(["--platform", "dy"])
    command.extend(["--lt", "qrcode"])
    command.extend(["--type", "detail"])

    # 检查ID列表长度，如果太长则写入临时文件
    ids_str = ",".join(ids)
    if len(ids_str) > 3000:  # Windows命令行长度限制约为32767，预留足够余量
        # 将ID写入临时文件
        temp_ids_file = f"temp_ids_{project_name}_{int(time.time())}.txt"
        with open(temp_ids_file, "w", encoding="utf-8") as f:
            f.write(ids_str)
        command.extend(["--specified_id_file", temp_ids_file])
        state.log(f"[信息] ID列表较长({len(ids)}个)，已写入临时文件: {temp_ids_file}", "info")
    else:
        command.extend(["--specified_id", ids_str])

    if project_name:
        command.extend(["--project", project_name])
    command.extend(["--get_comment", "yes"])
    command.extend(["--get_sub_comment", "yes"])
    command.extend(["--headless", "no"])
    command.extend(["--auto_close", "yes"])

    # 加载保存的二维码登录状态
    try:
        state_file = Path(QRCODE_LOGIN_STATE_FILE)
        if state_file.exists():
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
            timestamp = state_data.get("timestamp", 0)
            # 检查是否过期（7天）
            if time.time() - timestamp <= 7 * 24 * 3600:
                saved_cookies = state_data.get("cookies", [])
                if saved_cookies:
                    # 将cookies转换为字符串格式
                    saved_cookie_str = "; ".join([f"{c['name']}={c['value']}" for c in saved_cookies])
                    if saved_cookie_str:
                        command.extend(["--cookies", saved_cookie_str])
                        state.log("loaded saved qrcode login state", "info")
            else:
                state.log("qrcode login state expired", "warning")
        else:
            state.log("qrcode login state not found", "warning")
    except Exception as e:
        state.log(f"[警告] 加载二维码登录状态失败: {e}", "warning")

    return command

# --- API Routes ---

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/api/config")
async def get_config():
    if os.path.exists(state.config_file):
        try:
            return normalize_config_root(read_config_data())
        except:
            return {}
    return {}


@app.get("/api/hongguo/drama/settings")
async def get_hongguo_drama_settings():
    return public_hongguo_drama_settings(load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime"))


@app.put("/api/hongguo/drama/settings")
async def update_hongguo_drama_settings(payload: HongguoDramaSettingsRequest):
    settings = save_hongguo_drama_settings(
        WORKSPACE_ROOT / ".runtime",
        config_url=payload.config_url,
        activation_code=payload.activation_code,
    )
    return {"status": "success", **public_hongguo_drama_settings(settings)}


@app.post("/api/hongguo/drama/test")
async def test_hongguo_drama_interface(payload: HongguoDramaSettingsRequest):
    settings = load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime")
    if payload.config_url is not None:
        settings["config_url"] = payload.config_url.strip() or settings["config_url"]
    if payload.activation_code is not None:
        settings["activation_code"] = payload.activation_code.strip()
    try:
        interfaces = await asyncio.to_thread(HongguoDramaClient(settings).refresh_interfaces)
        return {
            "status": "success",
            "message": "配置中心和红果接口配置可用",
            "interfaces": interfaces,
        }
    except HongguoDramaError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)


@app.post("/api/hongguo/drama/search")
async def search_hongguo_drama(payload: HongguoDramaSearchRequest):
    title = payload.title.strip()
    if not title:
        raise HTTPException(400, "请输入剧名")
    fallback_reason = ""
    try:
        client = HongguoDramaClient(load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime"))
        candidates = await asyncio.to_thread(client.search, title)
        if candidates:
            return {
                "status": "success",
                "source": "hongguo",
                "candidates": [{key: value for key, value in item.items() if key != "raw"} for item in candidates],
            }
        fallback_reason = "红果未找到匹配剧目"
    except HongguoDramaError as exc:
        fallback_reason = f"红果接口不可用：{exc}"

    try:
        candidates = await DouyinDramaFallbackClient().search(title)
        return {
            "status": "success",
            "source": "douyin_fallback",
            "fallback_reason": fallback_reason,
            "candidates": candidates,
        }
    except DouyinFallbackError as exc:
        return JSONResponse(
            {
                "status": "error",
                "source": "douyin_fallback",
                "fallback_reason": fallback_reason,
                "message": f"{fallback_reason}；抖音降级检索失败：{exc}",
            },
            status_code=502,
        )


@app.post("/api/hongguo/drama/suggest")
async def suggest_hongguo_drama(payload: HongguoDramaSearchRequest):
    """Return lightweight Hongguo title suggestions while the user is typing.

    Suggestions intentionally do not trigger the Douyin fallback.  A browser/CDP
    search is expensive and should only run after the user submits a complete
    title through the main search action.
    """
    title = payload.title.strip()
    if not title:
        return {"status": "success", "source": "hongguo", "candidates": []}
    try:
        client = HongguoDramaClient(load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime"))
        candidates = await asyncio.to_thread(client.search, title)
        return {
            "status": "success",
            "source": "hongguo",
            "candidates": [
                {key: value for key, value in item.items() if key != "raw"}
                for item in candidates[:8]
            ],
        }
    except HongguoDramaError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)


def downloaded_hongguo_episode_statuses(book_id: str) -> dict[int, dict[str, str]]:
    statuses: dict[int, dict[str, str]] = {}
    for job in job_manager.list_jobs():
        if job.get("crawler_type") != "drama_download":
            continue
        extra = (job.get("job") or {}).get("extra") or {}
        if str(extra.get("book_id") or "").strip() != book_id:
            continue
        manifest_paths = []
        for artifact in job.get("artifacts") or []:
            artifact_path = Path(str(artifact.get("path") or ""))
            if artifact_path.name == "manifest.json":
                manifest_paths.append(artifact_path if artifact_path.is_absolute() else WORKSPACE_ROOT / artifact_path)
        if not manifest_paths:
            job_dir = WORKSPACE_ROOT / "data" / "hg" / "jobs" / str(job.get("job_id") or "")
            if job_dir.exists():
                manifest_paths.extend(job_dir.rglob("manifest.json"))
        for manifest_path in manifest_paths:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            for item in manifest.get("items") or []:
                if not isinstance(item, dict) or item.get("status") != "succeeded":
                    continue
                try:
                    episode = int(item.get("episode"))
                except (TypeError, ValueError):
                    continue
                statuses[episode] = {
                    "status": "downloaded",
                    "job_id": str(job.get("job_id") or ""),
                    "path": str(item.get("path") or ""),
                }
    return statuses


@app.post("/api/hongguo/drama/episodes")
async def get_hongguo_drama_episodes(payload: HongguoDramaEpisodesRequest):
    try:
        client = HongguoDramaClient(load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime"))
        episodes = await asyncio.to_thread(client.episodes, payload.book_id)
        downloaded = await asyncio.to_thread(downloaded_hongguo_episode_statuses, payload.book_id.strip())
        public_episodes = []
        for item in episodes:
            episode = {key: value for key, value in item.items() if key not in {"raw", "subtitle_url"}}
            try:
                status = downloaded.get(int(item.get("episode")))
            except (TypeError, ValueError):
                status = None
            if status:
                episode["download_status"] = status
            public_episodes.append(episode)
        return {
            "status": "success",
            "episodes": public_episodes,
        }
    except HongguoDramaError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=502)


@app.post("/api/hongguo/drama/download/search")
async def search_hongguo_drama_for_download(payload: HongguoDramaSearchRequest):
    """Search Hongguo first, then return manually selectable Douyin candidates."""
    if not payload.title.strip():
        raise HTTPException(400, "请输入剧名")
    fallback_reason = ""
    try:
        client = HongguoDramaClient(load_hongguo_drama_settings(WORKSPACE_ROOT / ".runtime"))
        # Do not leave the modal spinning through several slow provider pages;
        # a timed-out Hongguo search immediately proceeds to the Douyin fallback.
        candidates = await asyncio.wait_for(
            asyncio.to_thread(client.search_exact, payload.title.strip()),
            timeout=15,
        )
        if candidates:
            return {"status": "success", "source": "hongguo", "candidates": [{key: value for key, value in item.items() if key != "raw"} for item in candidates]}
        fallback_reason = "红果接口未返回剧名完全匹配结果"
    except asyncio.TimeoutError:
        fallback_reason = "红果接口检索超时"
    except HongguoDramaError as exc:
        fallback_reason = f"红果接口检索失败：{exc}"
    backend_logger.info("Hongguo download search falling back to Douyin: title=%s reason=%s", payload.title.strip(), fallback_reason)
    try:
        candidates = await DouyinDramaFallbackClient().search(payload.title.strip())
        backend_logger.info("Douyin fallback search returned %s candidates: title=%s", len(candidates), payload.title.strip())
        return {"status": "success", "source": "douyin_fallback", "fallback_reason": fallback_reason, "candidates": candidates}
    except DouyinFallbackError as exc:
        backend_logger.warning("Douyin fallback search failed: title=%s error=%s", payload.title.strip(), exc)
        return JSONResponse(
            {
                "status": "error",
                "source": "douyin_fallback",
                "fallback_reason": fallback_reason,
                "message": f"{fallback_reason}；抖音检索失败：{exc}",
            },
            status_code=502,
        )


@app.post("/api/hongguo/drama/jobs")
async def create_hongguo_drama_job(
    payload: HongguoDramaJobRequest,
    principal: Principal = Depends(current_principal),
):
    actor = principal if isinstance(principal, Principal) else None
    if actor is not None:
        _require_permission(actor, "job.create.selection")
    task_id, project_id = (
        _job_task_context(payload.workflow_task_id, actor)
        if actor is not None
        else (None, None)
    )
    if not payload.title.strip() or not payload.book_id.strip() or payload.episode < 1:
        raise HTTPException(400, "剧名、book_id 和集数必须有效")
    job = CrawlJob(
        job_id=job_manager.generate_job_id("hg"),
        platform="hg",
        crawler_type="drama_analysis",
        extra={
            "title": payload.title.strip(),
            "book_id": payload.book_id.strip(),
            "episode": payload.episode,
        },
    )
    try:
        job_state = job_manager.create_job(
            job,
            created_by_user_id=actor.user_id if actor is not None else None,
            workflow_task_id=task_id,
            project_id=project_id,
        )
        public_job = _public_created_job(job_state)
        if task_id:
            workflow_repository.link_task_job(task_id, job_state.job_id)
        if actor is not None:
            _record_job_audit(None, actor, "job.create", public_job)
        return {"status": "success", "job": public_job}
    except JobConflictError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=409)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/api/hongguo/drama/download/jobs")
async def create_hongguo_drama_download_job(
    payload: HongguoDramaDownloadJobRequest,
    principal: Principal = Depends(current_principal),
):
    actor = principal if isinstance(principal, Principal) else None
    if actor is not None:
        _require_permission(actor, "job.create.selection")
    task_id, project_id = (
        _job_task_context(payload.workflow_task_id, actor)
        if actor is not None
        else (None, None)
    )
    episodes = sorted({int(item) for item in payload.episodes if int(item) > 0})
    if not payload.title.strip() or not payload.book_id.strip() or (not payload.all_episodes and not episodes):
        raise HTTPException(400, "剧名、book_id 和至少一个集数必须有效")
    job_id = job_manager.generate_job_id("hg")
    job = CrawlJob(
        job_id=job_id,
        platform="hg",
        crawler_type="drama_download",
        extra={
            "title": payload.title.strip(),
            "book_id": payload.book_id.strip(),
            "episodes": episodes,
            "all_episodes": payload.all_episodes,
            "pause_path": str(job_manager.jobs_dir / f"{job_id}.pause"),
        },
    )
    try:
        job_state = job_manager.create_job(
            job,
            created_by_user_id=actor.user_id if actor is not None else None,
            workflow_task_id=task_id,
            project_id=project_id,
        )
        public_job = _public_created_job(job_state)
        if task_id:
            workflow_repository.link_task_job(task_id, job_state.job_id)
        if actor is not None:
            _record_job_audit(None, actor, "job.create", public_job)
        return {"status": "success", "job": public_job}
    except JobConflictError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=409)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)


@app.post("/api/hongguo/drama/douyin-fallback/jobs")
async def create_douyin_fallback_drama_job(
    payload: DouyinFallbackDramaJobRequest,
    principal: Principal = Depends(current_principal),
):
    actor = principal if isinstance(principal, Principal) else None
    if actor is not None:
        _require_permission(actor, "job.create.selection")
    task_id, project_id = (
        _job_task_context(payload.workflow_task_id, actor)
        if actor is not None
        else (None, None)
    )
    aweme_ids: List[str] = []
    for value in [*payload.aweme_ids, payload.aweme_id]:
        aweme_id = str(value or "").strip()
        if aweme_id and aweme_id not in aweme_ids:
            aweme_ids.append(aweme_id)
    if not payload.title.strip() or not aweme_ids:
        raise HTTPException(400, "剧名和用户确认的 aweme_id 必须有效")
    if len(aweme_ids) > 50:
        raise HTTPException(400, "单次最多下载 50 个抖音视频")
    job_id = job_manager.generate_job_id("hg")
    job = CrawlJob(
        job_id=job_id,
        platform="hg",
        crawler_type="douyin_fallback_analysis",
        extra={
            "title": payload.title.strip(),
            "aweme_id": aweme_ids[0],
            "aweme_ids": aweme_ids,
            "fallback_reason": payload.fallback_reason.strip(),
            "pause_path": str(job_manager.jobs_dir / f"{job_id}.pause"),
        },
    )
    try:
        job_state = job_manager.create_job(
            job,
            created_by_user_id=actor.user_id if actor is not None else None,
            workflow_task_id=task_id,
            project_id=project_id,
        )
        public_job = _public_created_job(job_state)
        if task_id:
            workflow_repository.link_task_job(task_id, job_state.job_id)
        if actor is not None:
            _record_job_audit(None, actor, "job.create", public_job)
        return {"status": "success", "job": public_job}
    except JobConflictError as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=409)
    except Exception as exc:
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

@app.get("/api/config/form")
async def get_config_form():
    try:
        data = read_config_data()
    except Exception as e:
        return JSONResponse({"status": "error", "message": f"config load failed: {e}"}, status_code=500)
    root = dict(normalize_config_root(data))
    items = root.get("items", [])
    if not isinstance(items, list):
        items = []
    clean_items = []
    for item in items:
        if isinstance(item, dict):
            clean_items.append(clean_config_item(item))
    root.pop("items", None)
    return {
        "status": "success",
        "root": root,
        "items": clean_items,
        "config_is_list": config_output_as_list(data),
        "item_fields": CONFIG_ITEM_FIELDS,
    }

@app.post("/api/config/form")
async def save_config_form(payload: ConfigFormPayload):
    try:
        existing_data = read_config_data()
    except Exception:
        existing_data = [{}]

    root = dict(payload.root or {})
    items = [clean_config_item(item) for item in payload.items if isinstance(item, dict)]

    crawler_type = str(root.get("默认爬取类型", root.get("crawler_type", ""))).lower()
    is_rank = "rank" in crawler_type or "榜单" in crawler_type
    is_search = not is_rank and ("search" in crawler_type or "搜索" in crawler_type or not crawler_type)
    if is_search:
        if not items:
            return JSONResponse({"status": "error", "message": "搜索类型至少需要一个项目配置"}, status_code=400)
        for index, item in enumerate(items, start=1):
            if not str(item.get("搜索关键词", "")).strip():
                return JSONResponse({"status": "error", "message": f"第 {index} 个项目缺少搜索关键词"}, status_code=400)

    root["items"] = items
    output_data = [root] if config_output_as_list(existing_data) else root

    try:
        if os.path.exists(state.config_file):
            backup_dir = Path("backups")
            backup_dir.mkdir(exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(state.config_file, backup_dir / f"data_backup_{timestamp}.json")
        with open(state.config_file, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)
        return {"status": "success", "message": "config saved"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/config/raw")
async def get_config_raw():
    if os.path.exists(state.config_file):
        try:
            with open(state.config_file, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        except Exception as e:
             return {"content": "", "error": str(e)}
    return {"content": ""}

@app.post("/api/config/raw")
async def save_config_raw(config: ConfigContent):
    try:
        # Validate JSON
        json.loads(config.content)
        # Write
        with open(state.config_file, "w", encoding="utf-8") as f:
            f.write(config.content)
        return {"status": "success", "message": "config saved"}
    except json.JSONDecodeError as e:
        return JSONResponse({"status": "error", "message": f"JSON格式错误: {e}"}, status_code=400)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.get("/api/config/backups")
async def get_backups(
    _: Principal = Depends(require_permission("*")),
):
    backup_dir = WORKSPACE_ROOT / "backups"
    if not backup_dir.exists():
        return []

    backups = []
    for f in backup_dir.glob("data_backup_*.json"):
        stats = f.stat()
        backups.append({
            "name": f.name,
            "path": str(f),
            "size": stats.st_size,
            "created": stats.st_ctime
        })
    backups.sort(key=lambda x: x["created"], reverse=True)
    return backups

@app.post("/api/config/backup/create")
async def create_backup(
    _: Principal = Depends(require_permission("*")),
):
    if not os.path.exists(state.config_file):
        return JSONResponse({"status": "error", "message": "config file not found"}, status_code=404)

    backup_dir = WORKSPACE_ROOT / "backups"
    backup_dir.mkdir(exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_name = f"data_backup_{timestamp}.json"
    backup_path = backup_dir / backup_name

    try:
        shutil.copy2(state.config_file, backup_path)
        return {"status": "success", "message": "备份创建成功", "filename": backup_name}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/config/backup/restore")
async def restore_backup(
    action: BackupAction,
    _: Principal = Depends(require_permission("*")),
):
    try:
        backup_path = resolve_backup_path(action.filename, WORKSPACE_ROOT / "backups")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not backup_path.exists():
        return JSONResponse({"status": "error", "message": "backup file not found"}, status_code=404)

    try:
        shutil.copy2(backup_path, state.config_file)
        return {"status": "success", "message": "config restored"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/config/backup/delete")
async def delete_backup(
    action: BackupAction,
    _: Principal = Depends(require_permission("*")),
):
    try:
        backup_path = resolve_backup_path(action.filename, WORKSPACE_ROOT / "backups")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not backup_path.exists():
        return JSONResponse({"status": "error", "message": "backup file not found"}, status_code=404)

    try:
        os.remove(backup_path)
        return {"status": "success", "message": "backup deleted"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/run")
async def run_crawler(
    config: CrawlerConfig,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    _require_permission(principal, "job.create.selection")
    _validate_generic_selection_job(
        config.platform.split()[0].strip().lower(),
        config.crawler_type.split()[0].strip().lower(),
    )
    return await run_crawler_as_job(config, principal, request)

    if state.is_running:
        return JSONResponse({"status": "error", "message": "crawler is running"}, status_code=400)

    # Process projects and keywords
    merged_keywords = ""
    project_map_json = "{}"

    # Read project items from config file to get their keywords
    project_items = []
    try:
        if os.path.exists(state.config_file):
            with open(state.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    project_items = data[0].get("items", [])
                elif isinstance(data, dict):
                    project_items = data.get("items", [])
    except:
        pass

    if project_items and config.projects:
        selected_keywords = set()
        keyword_to_projects = {}

        for item in project_items:
            p_name = item.get("project")
            if config.projects.get(p_name):
                k_str = _get_project_keywords(item)
                if k_str:
                    for k in [x.strip() for x in k_str.split(",") if x.strip()]:
                        selected_keywords.add(k)
                        if k not in keyword_to_projects:
                            keyword_to_projects[k] = []
                        keyword_to_projects[k].append(p_name)

        if selected_keywords:
            merged_keywords = ",".join(selected_keywords)
            project_map_json = json.dumps(keyword_to_projects, ensure_ascii=False)
            config.keywords = merged_keywords # Override keywords

    if not config.keywords and not config.specified_ids and not config.creator_ids:
         return JSONResponse({"status": "error", "message": "未提供关键词或ID，且未选择有效项目"}, status_code=400)

    state.clear_logs()
    command = build_command(config, project_map_json)
    threading.Thread(target=run_crawler_thread, args=(command, config), daemon=True).start()
    return {"status": "success", "message": "crawler started"}

@app.post("/api/jobs")
async def create_job(
    request: JobCreateRequest,
    principal: Principal = Depends(current_principal),
):
    _require_permission(principal, "job.create.selection")
    platform = request.platform.strip().lower()
    crawler_type = request.crawler_type.strip().lower()
    _validate_generic_selection_job(platform, crawler_type)
    workflow_task_id = str(request.extra.get("workflow_task_id") or "") or None
    task_id, project_id = _job_task_context(workflow_task_id, principal)
    safe_extra = {
        key: value
        for key, value in request.extra.items()
        if key not in _CLIENT_JOB_OWNERSHIP_FIELDS
    }
    data = {
        "platform": platform,
        "login_type": request.login_type,
        "crawler_type": crawler_type,
        "keywords": request.keywords,
        "start_page": request.start_page,
        "headless": request.headless,
        "save_data_option": request.save_option,
        "get_comments": request.get_comment,
        "get_sub_comments": request.get_sub_comment,
        "specified_ids": request.specified_ids,
        "creator_ids": request.creator_ids,
        "cookies": request.cookie,
        "project_keyword_map": request.project_map,
        "project_name": request.project_name,
        "extra": safe_extra,
    }
    try:
        job_state = job_manager.create_job(
            data,
            created_by_user_id=principal.user_id,
            workflow_task_id=task_id,
            project_id=project_id,
        )
        public_job = _public_created_job(job_state)
        if task_id:
            workflow_repository.link_task_job(task_id, job_state.job_id)
        _record_job_audit(None, principal, "job.create", public_job)
        return {"status": "success", "job": public_job}
    except JobConflictError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=409)
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@app.get("/api/jobs")
async def list_jobs(principal: Principal = Depends(current_principal)):
    return {
        "jobs": [
            job for job in job_manager.list_jobs() if _can_read_job(job, principal)
        ]
    }


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, principal: Principal = Depends(current_principal)):
    try:
        job = job_manager.get_job(job_id)
        _assert_can_read_job(job, principal)
        return job
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")


@app.get("/api/jobs/{job_id}/logs")
async def get_job_logs(
    job_id: str, principal: Principal = Depends(current_principal)
):
    try:
        job = job_manager.get_job(job_id)
        _assert_can_read_job(job, principal)
        return {"logs": job_manager.get_logs(job_id)}
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")


@app.post("/api/jobs/{job_id}/stop")
async def stop_job(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    try:
        before = job_manager.get_job(job_id)
        _assert_can_stop_job(before, principal)
        stopped = job_manager.stop(job_id)
        if stopped:
            _record_job_audit(
                request,
                principal,
                "job.stop",
                job_manager.get_job(job_id),
                before=before,
            )
        return {
            "status": "success" if stopped else "error",
            "message": "stop signal sent" if stopped else "job is not running",
        }
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")


@app.post("/api/jobs/{job_id}/pause")
async def pause_job(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    try:
        before = job_manager.get_job(job_id)
        _assert_can_mutate_owned_job(before, principal)
        paused = job_manager.pause(job_id)
        if paused:
            _record_job_audit(
                request,
                principal,
                "job.pause",
                job_manager.get_job(job_id),
                before=before,
            )
        return {"status": "success" if paused else "error", "message": "pause signal sent" if paused else "job is not running"}
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")


@app.post("/api/jobs/{job_id}/resume")
async def resume_job(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    try:
        before = job_manager.get_job(job_id)
        _assert_can_mutate_owned_job(before, principal)
        resumed = job_manager.resume(job_id)
        if resumed:
            _record_job_audit(
                request,
                principal,
                "job.resume",
                job_manager.get_job(job_id),
                before=before,
            )
        return {"status": "success" if resumed else "error", "message": "resume signal sent" if resumed else "job is not paused"}
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    try:
        before = job_manager.get_job(job_id)
        _assert_can_mutate_owned_job(before, principal)
        retried = job_manager.retry(job_id)
        retried_job = job_manager.get_job(retried.job_id)
        task_id = retried_job.get("workflow_task_id")
        if task_id and workflow_repository.get_task(str(task_id)) is not None:
            workflow_repository.link_task_job(str(task_id), retried.job_id)
        _record_job_audit(
            request, principal, "job.retry", retried_job, before=before
        )
        return {"status": "success", "job": retried_job}
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")
    except JobConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.delete("/api/jobs/{job_id}")
async def delete_job(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    if not principal.is_system_admin:
        raise HTTPException(403, "只有系统管理员可以删除机器任务历史")
    try:
        before = job_manager.get_job(job_id)
        job_manager.delete_job(job_id, force_missing=True)
        audit_service.record(
            actor=principal,
            action="job.delete",
            target_type="job",
            target_id=job_id,
            project_id=(
                str(before["project_id"])
                if before.get("project_id") is not None
                else None
            ),
            before=before,
            after={"deleted": True},
            reason=None,
            result="success",
            request=request,
        )
        return {"status": "success", "message": "job deleted"}
    except JobNotFoundError:
        raise HTTPException(404, "Job not found")
    except JobConflictError as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=409)


@app.post("/api/jobs/{job_id}/delete")
async def delete_job_post(
    job_id: str,
    request: Request,
    principal: Principal = Depends(current_principal),
):
    return await delete_job(job_id, request, principal)


@app.post("/api/stop")
async def stop_crawler(
    request: Request,
    principal: Principal = Depends(current_principal),
):
    for job in job_manager.list_jobs():
        if job["status"] not in {"pending", "running", "stopping"}:
            continue
        try:
            _assert_can_stop_job(job, principal)
        except HTTPException:
            continue
        if job_manager.stop(job["job_id"]):
            _record_job_audit(
                request,
                principal,
                "job.stop",
                job_manager.get_job(job["job_id"]),
                before=job,
            )
            return {"status": "success", "message": "stop signal sent"}
    if state.process:
        if not principal.is_system_admin:
            raise HTTPException(403, "历史运行进程只能由系统管理员控制")
        process = state.process
        target_id = f"legacy-process:{process.pid}"
        before = {"pid": process.pid, "status": "running", "legacy": True}
        state.stop_requested = True
        state.log("正在停止...", "info")

        # 使用 taskkill 强制结束进程树
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            process.terminate()

        audit_service.record(
            actor=principal,
            action="job.stop",
            target_type="job",
            target_id=target_id,
            project_id=None,
            before=before,
            after={"pid": process.pid, "status": "stop_requested", "legacy": True},
            reason=None,
            result="success",
            request=request,
        )

        return {"status": "success", "message": "stop signal sent"}
    return {"status": "error", "message": "没有运行中的爬虫"}

@app.get("/api/logs")
async def get_logs(principal: Principal = Depends(current_principal)):
    visible_jobs = [
        job for job in job_manager.list_jobs() if _can_read_job(job, principal)
    ]
    if visible_jobs:
        latest_job_id = visible_jobs[0]["job_id"]
        is_running = any(
            job["status"] in {"pending", "running", "stopping"}
            for job in visible_jobs
        )
        return {
            "logs": job_manager.get_logs(latest_job_id),
            "is_running": is_running,
            "batch_progress": f"{state.batch_current}/{state.batch_total}" if state.batch_total > 0 else "",
            "job_id": latest_job_id,
        }
    logs = []
    if principal.is_system_admin:
        while not state.log_queue.empty():
            logs.append(state.log_queue.get())
    return {
        "logs": logs,
        "is_running": state.is_running if principal.is_system_admin else False,
        "batch_progress": (
            f"{state.batch_current}/{state.batch_total}"
            if principal.is_system_admin and state.batch_total > 0
            else ""
        ),
    }

@app.get("/api/files")
async def list_files(filter: str = "all"):
    if not DATA_ROOT.exists():
        return []

    files = []
    for f in DATA_ROOT.rglob("*"):
        if f.is_file() and f.suffix.lower() in SUPPORTED_DATA_EXTENSIONS:
            stats = f.stat()
            classification = classify_data_file(f)
            file_info = {
                "name": f.name,
                "path": display_data_path(f),
                "size": stats.st_size,
                "modified": stats.st_mtime,
                "category": classification["category"],
                "legacy_category": classification["legacy_category"],
                "type": f.suffix.lower().lstrip("."),
                "media_type": classification["media_type"],
                **describe_data_file(f, classification),
            }
            if filter_matches_file(filter, file_info):
                files.append(file_info)

    files.sort(key=lambda x: x["modified"], reverse=True)
    return files

@app.get("/api/file/content")
async def get_file_content(path: str):
    file_path = resolve_data_file_path(path)
    if file_path.suffix.lower() not in TEXT_PREVIEW_EXTENSIONS:
        raise HTTPException(400, "该文件格式不支持文本预览")
    size = file_path.stat().st_size
    if size > TEXT_PREVIEW_MAX_BYTES:
        return JSONResponse(
            {"status": "error", "message": "文本文件超过 5 MB，请下载后查看", "size": size, "max_bytes": TEXT_PREVIEW_MAX_BYTES},
            status_code=413,
        )
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            # Try to parse JSON to pretty print
            if file_path.suffix.lower() == ".json":
                try:
                    parsed = json.loads(content)
                    return {"content": json.dumps(parsed, indent=2, ensure_ascii=False)}
                except:
                    pass
            return {"content": content}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=422)


def iter_file_range(path: Path, start: int, end: int, chunk_size: int = 1024 * 1024):
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = source.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/api/file/preview")
async def preview_file(path: str, request: Request):
    file_path = resolve_data_file_path(path)
    if file_path.suffix.lower() not in MEDIA_EXTENSIONS:
        raise HTTPException(400, "该文件格式不支持媒体预览")
    size = file_path.stat().st_size
    media_type = mimetypes.guess_type(file_path.name)[0] or (
        "audio/wav" if file_path.suffix.lower() == ".wav" else "application/octet-stream"
    )
    start, end, status_code = 0, max(0, size - 1), 200
    range_header = request.headers.get("range", "").strip()
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
        if not match or (not match.group(1) and not match.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if match.group(1):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else end
        else:
            suffix_length = int(match.group(2))
            start = max(0, size - suffix_length)
        end = min(end, size - 1)
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status_code = 206
    content_length = max(0, end - start + 1)
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "private, max-age=0",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(iter_file_range(file_path, start, end), status_code=status_code, media_type=media_type, headers=headers)

@app.get("/api/file/download")
async def download_file(path: str):
    file_path = resolve_data_file_path(path)
    return FileResponse(str(file_path), filename=file_path.name)


@app.delete("/api/files")
async def delete_files(
    req: FileDeleteRequest,
    _: Principal = Depends(require_permission("*")),
):
    paths = list(dict.fromkeys(path.strip() for path in req.paths if path.strip()))
    if not paths:
        raise HTTPException(400, "At least one file path is required")
    if len(paths) > 1000:
        raise HTTPException(400, "Too many files requested")

    # Resolve every path before deleting so an invalid selection cannot cause a partial deletion.
    file_paths = [resolve_data_file_path(path) for path in paths]
    deleted_paths = []
    failed = []
    for file_path in file_paths:
        try:
            file_path.unlink()
            deleted_paths.append(display_data_path(file_path))
        except OSError as exc:
            backend_logger.warning("Failed to delete data file %s: %s", file_path, exc)
            failed.append({"path": display_data_path(file_path), "message": str(exc)})

    if failed and not deleted_paths:
        return JSONResponse(
            {"status": "error", "deleted_paths": [], "failed": failed},
            status_code=500,
        )
    return {
        "status": "partial_success" if failed else "success",
        "deleted_paths": deleted_paths,
        "failed": failed,
    }

@app.post("/api/batch/crawl_comments")
async def batch_crawl_comments(task: BatchTask):
    # This logic mimics the `run_batch_comment_crawler`
    # We need to scan files to find IDs for the selected projects?
    # Actually, the UI should provide the projects and we assume the logic to find IDs is done?
    # No, the UI just selects projects. We need to parse the files to find IDs.

    # Wait, the previous GUI `crawl_comments_from_file` did the parsing.
    # In the new API, we should split this:
    # 1. UI requests "Analysis" of a file -> returns list of projects and counts.
    # 2. UI lets user select projects.
    # 3. UI calls this endpoint with selected projects AND the file path?
    # Or we just pass the file path and selected projects.
    pass

# We need an endpoint to analyze a file first
class AnalyzeRequest(BaseModel):
    file_path: str

@app.post("/api/file/analyze")
async def analyze_file(req: AnalyzeRequest):
    file_path = resolve_data_file_path(req.file_path)

    # Use the logic from `extract_and_filter_aweme_ids`
    # ... (Simplified for brevity, assuming we just need to return project list)
    project_ids = {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, list):
                    project_ids[k] = len(v)
        elif isinstance(data, list):
            project_ids["Default"] = len(data)

    except Exception as e:
        return {"error": str(e)}

    return project_ids

@app.post("/api/audio/analyze")
async def analyze_audio(req: AudioAnalyzeRequest):
    file_path = resolve_data_file_path(req.file_path)
    if file_path.suffix.lower() not in AUDIO_EXTENSIONS:
        return JSONResponse({"status": "error", "message": "Only audio files can be analyzed"}, status_code=400)

    analyzed_at = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        asr_provider = create_asr_provider()
        transcript = await asr_provider.transcribe(str(file_path))
        transcript_text = str(transcript.get("text", "")).strip()
        if not transcript_text:
            return JSONResponse(
                {
                    "status": "error",
                    "file_name": file_path.name,
                    "analysis_time": analyzed_at,
                    "message": "Audio transcription returned empty text",
                },
                status_code=500,
            )

        llm_provider = create_llm_provider()
        max_chars = getattr(config, "BILI_LLM_MAX_TRANSCRIPT_CHARS", 30000)
        transcript_used = transcript_text[:max_chars]
        llm_result = await llm_provider.analyze_video(
            {
                "source": "audio_file",
                "file_name": file_path.name,
                "path": display_data_path(file_path),
                "transcript": transcript_used,
                "transcript_truncated": len(transcript_text) > max_chars,
                "transcript_chars_used": len(transcript_used),
            }
        )
        return {
            "status": "success",
            "file_name": file_path.name,
            "file_path": display_data_path(file_path),
            "transcript": transcript_text,
            "summary": llm_result.get("summary", ""),
            "key_points": llm_result.get("key_points", []),
            "tags": llm_result.get("tags", []),
            "analysis": {
                "content_type": llm_result.get("content_type", ""),
                "sentiment": llm_result.get("sentiment", ""),
                "audience": llm_result.get("audience", ""),
                "action_items": llm_result.get("action_items", []),
                "risk_flags": llm_result.get("risk_flags", []),
                "provider": llm_result.get("provider", ""),
                "transcript_truncated": len(transcript_text) > max_chars,
                "transcript_chars_used": len(transcript_used),
            },
            "analysis_time": analyzed_at,
        }
    except Exception as e:
        return JSONResponse(
            {
                "status": "error",
                "file_name": file_path.name,
                "analysis_time": analyzed_at,
                "message": str(e),
            },
            status_code=500,
        )

@app.post("/api/hongguo/analyze-rank")
async def analyze_hongguo_rank(req: HongguoRankAnalyzeRequest):
    file_path = resolve_data_file_path(req.file_path)
    if file_path.suffix.lower() != ".json":
        return JSONResponse({"status": "error", "message": "Only Hongguo rank JSON files can be analyzed"}, status_code=400)
    try:
        manifest = analyze_rank_file(
            file_path,
            options=AnalyzeOptions(
                top_n=max(1, req.top_n),
                episode_limit=max(1, req.episode_limit),
                fetch_details=req.fetch_details,
                fetch_play_urls=req.fetch_play_urls,
                download_media=req.download_media,
                with_asr=req.with_asr,
                with_llm=req.with_llm,
                keep_video=req.keep_video,
                media_limit_mb=max(1, req.media_limit_mb),
            ),
        )
        return {"status": "success", "manifest": manifest}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/douyin/analyze-rank")
async def analyze_douyin_rank(req: DouyinRankAnalyzeRequest):
    file_path = resolve_data_file_path(req.file_path)
    if file_path.suffix.lower() != ".json":
        return JSONResponse({"status": "error", "message": "Only Douyin rank JSON files can be analyzed"}, status_code=400)
    try:
        manifest = analyze_douyin_rank_file(
            file_path,
            options=DouyinAnalyzeOptions(
                top_n=max(1, req.top_n),
                with_llm=req.with_llm,
                per_category=req.per_category,
                download_media=req.download_media,
                with_asr=req.with_asr,
            ),
        )
        return {"status": "success", "manifest": manifest}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

class BatchRunRequest(BaseModel):
    file_path: str
    projects: List[str] # List of project names to process
    auto_upload: bool = True # Whether to auto upload after export

class FileReviewRequest(BaseModel):
    file_path: str

def review_worker(file_path: str):
    """Worker thread for file review"""
    state.is_running = True
    try:
        file_path_obj = Path(file_path)
        state.log(f"开始复查文件: {file_path_obj.name}", "info")

        # 1. 读取配置文件
        state.log("正在读取配置文件 data.json ...", "info")
        if not os.path.exists("data.json"):
            state.log("找不到 data.json", "error")
            return

        with open("data.json", "r", encoding="utf-8") as f:
            config_data = json.load(f)
            if isinstance(config_data, list) and len(config_data) > 0:
                config_data = config_data[0]

        host = config_data.get("host")
        if not host:
            state.log("data.json 中未配置 host", "error")
            return

        api_url = f"{host.rstrip('/')}/api/filter_video_titles"

        # 准备项目配置映射
        project_configs = {}
        for item in config_data.get("items", []):
            p_name = item.get("project")
            if p_name:
                project_configs[p_name] = {
                    "topic": item.get("project", ""),
                    "include": item.get("include", item.get("keywords", "")),
                    "exclude": item.get("exclude", item.get("block_keywords", ""))
                }

        # 2. 读取内容文件
        state.log(f"正在读取文件: {file_path_obj.name}", "info")
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 3. 处理数据
        total_processed = 0
        has_changes = False
        import requests

        # 统一转为字典处理
        data_dict = {}
        if isinstance(data, list):
            data_dict["Default"] = data
        elif isinstance(data, dict):
            data_dict = data

        for project_name, items in data_dict.items():
            if not items: continue

            if state.stop_requested:
                state.log("用户请求停止复查", "warning")
                break

            # 获取筛选配置
            filter_config = project_configs.get(project_name)
            if not filter_config:
                state.log(f"missing filter config for project {project_name}, skipped", "warning")
                continue

            state.log(f"processing project: {project_name}, items: {len(items)}", "info")

            # 清除pass并准备检查列表
            items_to_check = []
            indices_to_check = []

            for i, item in enumerate(items):
                # 清除旧的pass状态
                if "pass" in item:
                    del item["pass"]
                    has_changes = True

                items_to_check.append(item)
                indices_to_check.append(i)

            # 分批调用接口
            batch_size = 10
            for i in range(0, len(items_to_check), batch_size):
                if state.stop_requested: break

                batch_items = items_to_check[i:i+batch_size]
                batch_indices = indices_to_check[i:i+batch_size]

                titles = [item.get("title", "") or item.get("desc", "") for item in batch_items]

                payload = {
                    "titles": titles,
                    "filter_config": filter_config
                }

                try:
                    resp = requests.post(api_url, json=payload, timeout=30)
                    if resp.status_code == 200:
                        res_json = resp.json()
                        if res_json.get("success"):
                            results = res_json.get("results", [])
                            for j, passed in enumerate(results):
                                if j < len(batch_items):
                                    idx = batch_indices[j]
                                    items[idx]["pass"] = passed
                                    has_changes = True
                                    total_processed += 1
                                    status_str = "通过" if passed else "拒绝"
                                    state.log(f"  - [{status_str}] {titles[j][:30]}...", "info")
                        else:
                            state.log(f"[ERROR] 接口返回失败: {res_json}", "error")
                    else:
                        state.log(f"[ERROR] 接口HTTP错误: {resp.status_code}", "error")
                except Exception as e:
                    state.log(f"[ERROR] 请求异常: {e}", "error")

                # 稍微停顿避免请求过快
                time.sleep(0.5)

        # 4. 保存文件
        if has_changes:
            state.log("正在保存文件...", "info")
            # 恢复原始结构
            save_data = data_dict if isinstance(data, dict) else data_dict["Default"]

            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(save_data, f, ensure_ascii=False, indent=4)
            state.log("[SUCCESS] 文件保存成功", "success")
            state.log(f"review completed, processed {total_processed} items", "success")
        else:
            state.log("没有数据发生变化", "info")

    except Exception as e:
        state.log(f"复查失败: {str(e)}", "error")
    finally:
        state.is_running = False
        state.stop_requested = False

@app.post("/api/file/review")
async def review_file(req: FileReviewRequest):
    if state.is_running:
         return JSONResponse({"status": "error", "message": "task is running"}, status_code=400)

    if not os.path.exists(req.file_path):
        return JSONResponse({"status": "error", "message": "file not found"}, status_code=404)

    state.clear_logs()
    state.stop_requested = False
    threading.Thread(target=review_worker, args=(req.file_path,), daemon=True).start()
    return {"status": "success", "message": "review task started"}

@app.post("/api/batch/run")
async def run_batch(req: BatchRunRequest):
    if state.is_running:
        return JSONResponse({"status": "error", "message": "task is running"}, status_code=400)

    # Extract IDs
    try:
        with open(req.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        queue_items = []
        for proj in req.projects:
            ids = []
            if isinstance(data, dict):
                items = data.get(proj, [])
            else:
                items = data # Default

            for item in items:
                # Extract ID logic
                oid = item.get("aweme_id") or item.get("note_id") or item.get("id")
                if oid:
                    ids.append(oid)

            if ids:
                queue_items.append((proj, ids))

        if not queue_items:
             return JSONResponse({"status": "error", "message": "未找到有效ID"}, status_code=400)

        state.batch_queue = queue_items
        state.stop_requested = False
        state.clear_logs()

        threading.Thread(target=batch_worker, daemon=True).start()
        return {"status": "success", "message": "batch task started"}

    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/export/excel")
async def export_excel(req: BatchRunRequest):
    # Uses dict_to_小鸡
    if not excel_converter:
        return JSONResponse({"status": "error", "message": "缺少导出模块"}, status_code=500)

    # Logic to export selected projects
    # We can run this in a thread too as it might take time
    def export_worker():
        state.log("开始导出Excel...", "info")
        try:
            # Load data
            with open(req.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            for proj in req.projects:
                items = data.get(proj) if isinstance(data, dict) else data
                if not items: continue

                # Create temp file
                temp_file = f"temp_{proj}.json"
                with open(temp_file, "w", encoding="utf-8") as tf:
                    json.dump({proj: items}, tf, ensure_ascii=False, indent=4)

                # Convert
                state.log(f"正在转换项目: {proj}", "info")
                excel_path = excel_converter.convert_json_to_excel(temp_file)
                state.log(f"导出成功: {excel_path}", "success")

                # Auto Upload
                if req.auto_upload:
                    upload_res = upload_excel_to_interface(excel_path, project_override=proj)
                    if upload_res and "error" not in upload_res:
                         state.log(f"项目 {proj} 上传成功", "success")
                    else:
                         err = upload_res.get("error", "未知错误")
                         state.log(f"项目 {proj} 上传失败: {err}", "error")
                else:
                    state.log(f"项目 {proj} 导出完成 (未开启自动上传)", "info")

                # Cleanup
                if os.path.exists(temp_file):
                    os.remove(temp_file)

        except Exception as e:
            state.log(f"导出失败: {str(e)}", "error")

    threading.Thread(target=export_worker, daemon=True).start()
    return {"status": "success", "message": "export task started"}


@app.get("/api/server/ip")
async def get_server_ip():
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # 不真正连接，只是为了获取本机IP
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return {"ip": ip, "port": 8899}
    except:
        return {"ip": "127.0.0.1", "port": 8899}


# --- QR Code Login Functions ---

QRCODE_LOGIN_STATE_FILE = "qrcode_login_state.json"

async def check_login_state_douyin(browser_context) -> bool:
    # Check Douyin login state.
    try:
        current_cookie = await browser_context.cookies()
        for cookie in current_cookie:
            if cookie.get("name") == "LOGIN_STATUS" and cookie.get("value") == "1":
                return True

        # 检查localStorage
        pages = browser_context.pages
        for page in pages:
            try:
                local_storage = await page.evaluate("() => window.localStorage")
                if local_storage.get("HasUserLogin", "") == "1":
                    return True
            except:
                pass
        return False
    except Exception as e:
        return False


async def save_login_state(browser_context, platform: str = "douyin"):
    # Save login state to file.
    try:
        cookies = await browser_context.cookies()
        state_data = {
            "platform": platform,
            "timestamp": time.time(),
            "cookies": cookies,
        }

        # 尝试获取localStorage
        pages = browser_context.pages
        for page in pages:
            try:
                local_storage = await page.evaluate("() => window.localStorage")
                state_data["local_storage"] = local_storage
                break
            except:
                pass

        with open(QRCODE_LOGIN_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存登录状态失败: {e}")
        return False


def load_login_state() -> dict:
    # Load login state from file.
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            with open(QRCODE_LOGIN_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"加载登录状态失败: {e}")
    return {}


async def perform_qrcode_login(platform: str = "douyin"):
    # Perform qrcode login flow.
    from playwright.async_api import async_playwright

    browser = None
    browser_context = None
    login_success = False
    error_msg = ""

    try:
        state.log("[扫码登录] 正在启动浏览器...", "info")
        async with async_playwright() as p:
            # 启动浏览器
            browser = await p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            state.log("[扫码登录] 浏览器已启动", "info")

            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}
            )
            state.log("message", "info")

            # 打开页面
            page = await browser_context.new_page()
            state.log("[扫码登录] 新页面已创建", "info")

            if platform == "douyin":
                state.log("[扫码登录] 正在打开抖音网站...", "info")
                try:
                    # 先尝试快速加载
                    await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=30000)
                    state.log("[扫码登录] 抖音网站已加载 (domcontentloaded)", "info")
                except:
                    # 如果超时，继续等待网络空闲
                    state.log("[扫码登录] 页面基础内容已加载，继续等待...", "info")

                # 再等待一段时间让页面完全加载
                await page.wait_for_timeout(5000)
                state.log("[扫码登录] 页面加载完成", "info")
                await page.wait_for_timeout(3000)

                # 尝试点击登录按钮
                try:
                    login_btn = page.locator("xpath=//p[text() = '登录']")
                    count = await login_btn.count()
                    state.log(f"found {count} login buttons", "info")
                    if count > 0:
                        await login_btn.click()
                        await page.wait_for_timeout(1000)
                        state.log("clicked login button", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 点击登录按钮失败: {e}", "warning")

                # 等待二维码出现
                try:
                    qrcode_selector = "xpath=//div[@id='animate_qrcode_container']//img"
                    state.log("[扫码登录] 等待二维码出现...", "info")
                    await page.wait_for_selector(qrcode_selector, timeout=10000)
                    state.log("[扫码登录] 请使用手机扫描二维码", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 未找到二维码: {e}", "warning")
                    # 截图查看当前页面状态
                    try:
                        await page.screenshot(path="qrcode_debug.png")
                        state.log("[扫码登录] 已保存调试截图: qrcode_debug.png", "info")
                    except:
                        pass

            # 等待登录完成（最多等待5分钟）
            max_wait = 300  # 5分钟
            waited = 0
            check_interval = 2

            state.log("[扫码登录] 开始等待扫码，请在5分钟内完成扫码...", "info")
            while waited < max_wait:
                try:
                    if await check_login_state_douyin(browser_context):
                        login_success = True
                        break

                    # 检查页面是否仍然打开
                    if page.is_closed():
                        state.log("[扫码登录] 页面已被关闭", "warning")
                        error_msg = "page closed, please retry"
                        break

                    await page.wait_for_timeout(check_interval * 1000)
                    waited += check_interval

                    if waited % 10 == 0:
                        state.log(f"waiting for qrcode login: {waited}s", "info")
                except Exception as e:
                    state.log(f"[扫码登录] 等待过程中出错: {e}", "warning")
                    # 如果是页面关闭的错误，直接退出循环
                    if "closed" in str(e).lower():
                        error_msg = "browser closed, please retry"
                        break
                    # 其他错误继续等待
                    await asyncio.sleep(check_interval)
                    waited += check_interval

            if login_success:
                # 保存登录状态
                await save_login_state(browser_context, platform)
                state.log("[扫码登录] 登录成功，状态已保存", "success")
            elif not error_msg:
                error_msg = "登录超时，请重试"
                state.log("[扫码登录] 登录超时", "error")

    except Exception as e:
        error_msg = str(e)
        state.log(f"[扫码登录] 发生错误: {e}", "error")
        import traceback
        state.log(f"[扫码登录] 错误详情: {traceback.format_exc()}", "error")
    finally:
        if browser_context:
            try:
                await browser_context.close()
            except:
                pass
        if browser:
            try:
                await browser.close()
            except:
                pass
        state.log("[扫码登录] 浏览器已关闭", "info")

    return {"success": login_success, "error": error_msg}


# QR Code Login API Endpoints

@app.get("/api/qrcode/status")
async def get_qrcode_status():
    # Return qrcode login status.
    state_data = load_login_state()

    if not state_data:
        return {"has_state": False, "message": "login state not found"}

    # 检查是否过期（7天）
    timestamp = state_data.get("timestamp", 0)
    if time.time() - timestamp > 7 * 24 * 3600:
        return {"has_state": False, "message": "登录状态已过期", "expired": True}

    platform = state_data.get("platform", "unknown")
    save_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))

    return {
        "has_state": True,
        "platform": platform,
        "save_time": save_time,
        "message": f"已保存 {platform} 登录状态（{save_time}）"
    }


@app.post("/api/qrcode/login")
async def start_qrcode_login():
    # Start qrcode login flow.
    if state.is_running:
        return JSONResponse({"status": "error", "message": "爬虫正在运行中，请先停止"}, status_code=400)

    state.clear_logs()
    state.log("[扫码登录] 启动扫码登录流程...", "info")

    # 在后台线程中执行登录
    def login_worker():
        import asyncio
        result = asyncio.run(perform_qrcode_login("douyin"))
        return result

    threading.Thread(target=login_worker, daemon=True).start()

    return {"status": "success", "message": "扫码登录已启动"}


@app.post("/api/qrcode/clear")
async def clear_qrcode_state():
    # Clear saved qrcode login state.
    try:
        if os.path.exists(QRCODE_LOGIN_STATE_FILE):
            os.remove(QRCODE_LOGIN_STATE_FILE)
            return {"status": "success", "message": "登录状态已清除"}
        return {"status": "success", "message": "没有已保存的登录状态"}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# --- Media Download API Endpoints ---

def sanitize_filename(name: str) -> str:
    # Sanitize filename.
    import re
    # 替换非法字符为下划线
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
    # 移除多余空格
    name = re.sub(r'\s+', ' ', name)
    name = name.strip()
    # 限制长度
    if len(name) > 100:
        name = name[:100]
    return name


def build_media_filename(title: str, aweme_id: str, media_type: str, extension: str) -> str:
    # Build a media download filename.
    title = sanitize_filename(title)
    return f"{title}_{aweme_id}_{media_type}.{extension}"


@app.get("/api/media/list")
async def get_media_list(file_path: str):
    # Return media URLs from a result file.
    if not os.path.exists(file_path):
        raise HTTPException(404, "File not found")

    videos = []
    musics = []

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 支持多种数据格式
        items = []
        if isinstance(data, dict):
            # 可能是 {project_name: [items]} 或直接是 {aweme_id: {...}}
            for key, value in data.items():
                if isinstance(value, list):
                    items.extend(value)
                elif isinstance(value, dict):
                    # 单个项目直接是数据
                    items.append(value)
        elif isinstance(data, list):
            items = data

        for item in items:
            aweme_id = item.get("aweme_id", "")
            title = item.get("title") or item.get("desc", "") or ""

            # 提取视频URL
            video_url = item.get("video_download_url", "")
            if video_url and isinstance(video_url, str) and video_url.startswith("http"):
                videos.append(MediaItem(
                    aweme_id=aweme_id,
                    title=title[:50],
                    url=video_url,
                    filename=build_media_filename(title, aweme_id, "video", "mp4")
                ))

            # 提取音频URL
            music_url = item.get("music_download_url", "")
            if music_url and isinstance(music_url, str) and music_url.startswith("http"):
                musics.append(MediaItem(
                    aweme_id=aweme_id,
                    title=title[:50],
                    url=music_url,
                    filename=build_media_filename(title, aweme_id, "music", "mp3")
                ))

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

    return MediaListResponse(videos=videos, musics=musics)


@app.get("/api/media/download")
async def download_media(url: str, media_type: str = "video", filename: str = "media"):
    # Proxy a media download URL.
    if not url or not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    try:
        # 确定文件扩展名
        ext = "mp4" if media_type == "video" else "mp3"
        if not filename.endswith(f".{ext}"):
            filename = f"{filename}.{ext}"

        # 使用httpx异步获取内容并流式返回
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    raise HTTPException(response.status_code, f"Failed to fetch media: {response.status_code}")

                # 确定内容类型
                content_type = response.headers.get("content-type", "")
                if not content_type or "application/octet-stream" in content_type:
                    content_type = "video/mp4" if media_type == "video" else "audio/mpeg"

                async def stream_response():
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        if chunk:
                            yield chunk

                return StreamingResponse(
                    stream_response(),
                    media_type=content_type,
                    headers={
                        "Content-Disposition": f'attachment; filename*=UTF-8\'\'{filename}',
                        "Content-Length": response.headers.get("content-length", ""),
                    }
                )
    except Exception as e:
        raise HTTPException(500, f"Download failed: {str(e)}")


# Mount static files
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

if __name__ == "__main__":
    # Ensure directories exist
    os.makedirs("data", exist_ok=True)
    os.makedirs("templates", exist_ok=True)

    print("Starting AiWorker GUI...")
    # Open browser
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8899")).start()
    uvicorn.run(app, host="0.0.0.0", port=8899, log_level=WEB_LOG_LEVEL.lower())
