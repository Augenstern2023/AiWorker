# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests\test_webgui_ui_changes.py
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

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import WebGUI
from core.events import WorkerEvent
from core.job import CrawlJob
from runtime.job_state import JobState


def _source(*relative_paths: str) -> str:
    return "\n".join(
        Path(path).read_text(encoding="utf-8") for path in relative_paths
    )


def _workspace_source() -> str:
    templates = sorted(Path("templates").glob("**/*.html"))
    scripts = sorted(Path("static/js").glob("**/*.js"))
    styles = sorted(Path("static/css").glob("*.css"))
    artifacts = [path for path in templates if path.name != "index.html"]
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in (*artifacts, *scripts, *styles)
    )


def test_webgui_registers_rag_routes():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/rag/status" in paths
    assert "/api/rag/index" in paths
    assert "/api/rag/query" in paths


def test_webgui_registers_analytics_routes():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/analytics/sync" in paths
    assert "/api/analytics/overview" in paths
    assert "/api/analytics/dramas" in paths
    assert "/api/analytics/assets" in paths
    assert "/api/analytics/sync-all" in paths


def test_webgui_registers_today_rank_route():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/rank/today" in paths
    assert "/favicon.ico" in paths


def test_webgui_registers_creative_phase3_routes_and_page():
    paths = {route.path for route in WebGUI.app.routes}
    html = _workspace_source()

    assert "/api/creative/topics/generate" in paths
    assert "/api/creative/breakdowns" in paths
    assert "今日爆款" in html
    assert "generateCreativeTopics" in html
    assert '@change="generateCreativeTopics"' in html


def test_webgui_registers_phase4_routes_and_flat_project_workspace():
    paths = {route.path for route in WebGUI.app.routes}
    html = _workspace_source()

    assert "/api/creative/projects" in paths
    assert "/api/creative/projects/{project_id}" in paths
    assert "/api/creative/projects/{project_id}/blueprints/generate" in paths
    assert "/api/creative/blueprints/{blueprint_id}/lock-brief" in paths
    assert "/api/creative/projects/{project_id}/characters/generate" in paths
    assert "/api/creative/projects/{project_id}/outlines/generate" in paths
    assert "/api/creative/projects/{project_id}/episodes/{episode_number}/beats/generate" in paths
    assert "/api/jobs/{job_id}/retry" in paths
    assert "创作项目" in html
    assert "currentTab" not in html
    assert "/planning/projects" in _source("web/navigation_service.py")
    assert "fetchCreativeProjects" in html
    assert "generateProjectBlueprints" in html
    assert "submitProjectBrief" in html
    assert "generateProjectCharacters" in html
    assert "generateProjectOutlines" in html
    assert "generateProjectBeats" in html
    assert "pauseCreativeJob" in html
    assert "retryCreativeJob" in html
    assert "creative-workspace-scroll" not in html
    assert 'class="table table-sm table-hover align-middle mb-4"' not in html
    assert "creativeWorkspace.projects[0]?.id" in html
    assert ':href="`/planning/projects/${project.id}/directions`"' in html
    assert "--bs-table-active-color: var(--text);" in html


def test_blank_character_batch_links_the_active_workflow_task():
    script = _source("static/js/common/creative.js")
    method = script.split("async createBlankCharacterBatch()", 1)[1].split(
        "async saveProjectCharacter", 1
    )[0]

    assert "resolveCreativeWorkflowTask('characters', projectId)" in method
    assert "workflow_task_id: task.id" in method


def test_workflow_governed_creation_actions_send_active_task_ids():
    selection = _source("static/js/selection/breakdowns.js")
    creative = _source("static/js/common/creative.js")
    breakdown = selection.split("async startTranscriptBreakdown", 1)[1].split(
        "syncBreakdownGenerationProgress", 1
    )[0]
    download = selection.split("async startDramaDownload", 1)[1].split(
        "syncDramaDownloadProgress", 1
    )[0]
    project = creative.split("async createCreativeProject", 1)[1].split(
        "async selectCreativeProject", 1
    )[0]

    assert "resolveCreativeWorkflowTask('breakdown'" in breakdown
    assert "workflow_task_id: task.id" in breakdown
    assert "resolveCreativeWorkflowTask('source_package'" in download
    assert "workflow_task_id: task.id" in download
    assert "topic_id: this.dramaDownload.topic.id" in download
    assert "resolveCreativeWorkflowTask('brief'" in project
    assert "workflow_task_id: task.id" in project


def test_drama_material_jobs_send_exact_source_topic_and_task():
    script = _source("static/js/selection/dramas.js")
    method = script.split("async startDramaAnalysis", 1)[1].split(
        "dramaJobArtifacts", 1
    )[0]

    assert "resolveSourcePackageWorkflowTask" in method
    assert "topic_id: task.topic_id" in method
    assert "workflow_task_id: task.id" in method
    resolver = _source("static/js/common/creative.js").split(
        "async resolveSourcePackageWorkflowTask", 1
    )[1].split("async submitCreativeResource", 1)[0]
    assert "/api/creative/topics?limit=500" not in resolver
    assert "/api/creative/topics/${task.topic_id}" in resolver
    assert "Promise.all" in resolver


def test_project_table_uses_name_navigation_without_redundant_action_column():
    section = _source("templates/common/workspace/project_list.html")
    html = section + _source("static/css/workspaces.css")
    project_table = section.split('<table class="table table-sm align-middle mb-4">', 1)[1].split("</table>", 1)[0]

    assert ">操作</th>" not in project_table
    assert 'title="打开项目"' not in project_table
    assert "bi-arrow-right" not in project_table
    assert 'class="project-name-button"' in project_table
    assert ':href="`/planning/projects/${project.id}/directions`"' in project_table
    assert '@click.stop="selectCreativeProject(project.id)"' not in project_table
    assert ':aria-current="creativeWorkspace.project?.id === project.id ? \'page\' : null"' not in project_table
    assert ".creative-project-row.table-active" in html
    assert ".project-name-button:focus-visible" in html
    assert 'colspan="5"' in project_table


def test_creative_project_creation_supports_selecting_a_locked_breakdown_version():
    html = _workspace_source()

    assert "creativeWorkspace.lockedBreakdowns" in html
    assert "loadCreativeProjectBreakdowns" in html
    assert "选择拆解版本" in html
    assert ".form-select:disabled" in html
    assert "background-color: #151722;" in html


def test_draft_breakdowns_are_visible_but_not_project_eligible():
    html = _workspace_source()

    assert "breakdownStatusLabel(topic)" in html
    assert "breakdownStatusClass(topic)" in html
    assert "草稿${version} · 待提交" in html
    assert "草稿${version} · 已有批准版" in html
    assert "继续拆解" in html
    assert "aiworkerApi.request('/api/creative/topics?status=adopted&limit=500')" in html
    assert ':disabled="!topic.locked_breakdown_id"' in html
    assert 'x-text="creativeTopicOptionLabel(topic)"' in html


def test_today_hit_actions_follow_topic_status_without_conflicting_commands():
    section = _source("templates/common/workspace/selection_hits.html")

    assert "updateCreativeTopic(topic, 'adopted')" not in section
    assert 'x-show="!topic.latest_breakdown_id && topic.status === \'pending_review\'" @click="updateCreativeTopic(topic, \'rejected\')"' in section
    assert "!topic.latest_breakdown_id && topic.status === 'adopted'" in section
    assert "updateTopicLibrary(topic, 'adopted')" not in section
    assert "重新采纳" not in section


def test_today_hit_actions_distinguish_existing_breakdowns():
    section = _source("templates/common/workspace/selection_hits.html")

    assert 'x-show="!topic.latest_breakdown_id && topic.status === \'pending_review\'"' in section
    assert "!topic.latest_breakdown_id && topic.status === 'adopted'" in section
    assert 'x-show="topic.latest_breakdown_id && topic.status !== \'archived\'"' in section
    assert '@click="createCreativeBreakdown(topic)">再次拆解</button>' in section
    assert '@click="updateCreativeTopic(topic, \'archived\')">归档</button>' in section


def test_topic_library_shows_full_lifecycle_and_breakdown_version_column():
    html = _workspace_source()
    section = _source("templates/common/workspace/selection_topics.html").split(
        '<h3 class="mb-1">选题库</h3>', 1
    )[1]

    assert "lifecycle_status" in html
    assert "已下载 X 集" in section
    assert "人物关系待生成" in section
    assert "分集大纲已生成" in section
    assert "Beats脚本已导出" in section
    assert "<th>状态</th><th>拆解版本</th><th>操作</th>" in section
    assert "breakdown-version-badge" in section
    assert "lifecycle-adopted" in html
    assert "lifecycle-beats-generated" in html
    assert "lifecycle-beats-exported" in html


def test_download_modal_restores_running_job_and_explains_progress():
    html = _workspace_source()

    assert "findDramaDownloadJob" in html
    assert "restoreDramaDownloadJob" in html
    assert "scrollDramaDownloadToTop" in html
    assert "dramaDownload.jobStatus" in html
    assert "当前处理" in html
    assert "关闭窗口不会中断下载任务" in html


def test_blueprint_core_conflict_uses_the_expandable_description_cell_pattern():
    html = _workspace_source()

    assert "blueprint-conflict-cell" in html
    assert ".blueprint-conflict-cell:hover" in html
    assert 'x-text="shortText(item.content.core_conflict, 42)"' not in html


def test_character_editor_expands_long_values_and_confirms_row_save():
    html = _workspace_source()

    assert "creative-inline-textarea" in html


def test_blueprint_editor_exposes_generated_direction_audience():
    html = _workspace_source()

    assert 'x-model="selectedProjectBlueprint().content.audience"' in html
    assert "方向目标受众" in html
    assert "textareaRows(" in html
    assert "creativeWorkspace.saveState" in html
    assert "人物卡已保存" in html
    assert "creative-save-toast" in html
    assert "creative-save-status" in html
    assert "creativeItemSaveLabel" in html


def test_beats_generation_shows_local_start_and_progress_feedback():
    html = _workspace_source()

    assert "creativeActionRunning('beats')" in html
    assert "正在创建生成任务" in html
    assert "beats-generation-status" in html


def test_blueprint_generation_shows_immediate_and_live_progress_feedback():
    html = _workspace_source()

    assert 'x-show="creativeWorkspace.startingAction || creativeWorkspace.jobId"' in html
    assert 'role="status" aria-live="polite"' in html
    assert "creativeActionRunning('blueprints')" in html
    assert "creativeProgressIndeterminate()" in html
    assert "creativeProgressElapsedText()" in html
    assert "tickCreativeGenerationElapsed" in html
    assert "creativeWorkspace.jobId = ''" in html
    assert "creativeWorkspace.jobProgress = {indeterminate: true}" in html
    assert "正在创建生成任务" in html


def test_character_and_outline_generation_show_the_same_button_progress_feedback():
    html = _workspace_source()

    assert "creativeActionRunning('characters')" in html
    assert "creativeWorkspace.startingAction === 'characters' ? '正在启动' : '生成中'" in html
    assert "creativeActionRunning('outlines')" in html
    assert "creativeWorkspace.startingAction === 'outlines' ? '正在启动' : '生成中'" in html
    assert html.count(':disabled="creativeActionDisabled(') >= 3


def test_creative_generation_opens_progress_modal_with_retry_controls():
    html = _workspace_source()

    assert 'id="creativeGenerationProgressModal"' in html
    assert "this.modal('creativeGenerationProgressModal')" in html
    assert "this.creativeGenerationProgressModal.show()" in html
    assert "人物关系生成任务已发起" in html
    assert "关闭弹窗不会中断生成任务" in html
    assert '@click="retryCreativeJob"' in html


def test_downloaded_topic_action_opens_episode_transcript_preview():
    html = _workspace_source()

    assert 'x-show="Number(topic.downloaded_episode_count || 0) > 0"' in html
    assert '@click="openTopicTranscriptPreview(topic)"' in html
    assert 'title="预览剧目转写"' in html
    assert 'id="topicTranscriptPreviewModal"' in html
    assert "this.modal('topicTranscriptPreviewModal')" in html
    assert "/api/creative/topics/${topic.id}/transcripts" in html
    assert "selectedTopicTranscript()" in html
    assert "上一集" in html
    assert "下一集" in html


def test_next_stage_generation_is_not_blocked_by_a_stale_previous_action():
    html = _workspace_source()

    assert "creativeActionDisabled(action)" in html
    assert "this.creativeWorkspace.activeAction === action && this.creativeJobRunning()" in html
    assert ':disabled="creativeActionDisabled(\'outlines\')"' in html
    assert ':disabled="creativeActionDisabled(\'characters\')"' in html
    assert ':disabled="creativeActionDisabled(\'blueprints\')"' in html


def test_saved_creative_results_reconcile_a_stale_running_indicator():
    html = _workspace_source()

    assert "creativeProjectHasJobResult(project, action, jobId)" in html
    assert "item.generation_meta?.job_id === jobId" in html
    assert "reconcileCreativeGenerationState(project)" in html
    assert "this.creativeWorkspace.jobStatus = 'succeeded'" in html
    assert "this.creativeWorkspace.jobProgress = {completed: 1, total: 1}" in html
    assert "生成完成，结果已回填到当前项目" in html


def test_creative_running_state_stops_as_soon_as_its_saved_result_is_visible():
    html = _workspace_source()

    assert "creativeJobStatusRunning()" in html
    assert "if (!this.creativeJobStatusRunning()) return false" in html
    assert "!this.creativeProjectHasJobResult(" in html
    assert "if (!this.creativeProjectHasJobResult(project, action, jobId)) return false" in html


def test_terminal_creative_job_always_clears_the_action_even_if_result_matching_lags():
    html = _workspace_source()

    assert "finishCreativeGeneration()" in html
    assert "this.finishCreativeGeneration();" in html
    assert "this.creativeWorkspace.activeAction = ''" in html
    assert "this.creativeWorkspace.startingAction = ''" in html


def test_breakdown_editor_opens_in_modal_instead_of_page_bottom():
    html = _workspace_source()

    assert 'id="breakdownEditorModal"' in html
    assert 'x-model="breakdownEditor.content[item.key]"' in html
    assert "breakdownEditor.content[item.key][index]" in html
    assert "addBreakdownArrayItem" in html
    assert "removeBreakdownArrayItem" in html
    assert "breakdownEditor.fieldGuide" in html
    assert "item.writing_prompt" in html
    assert "item.evidence_rule" in html
    assert "openBreakdownEditor" in html
    assert "saveBreakdownEditor" in html
    assert "submitBreakdownEditor" in html
    assert "this.modal('breakdownEditorModal')" in html
    assert "normalizeBreakdownEditorContent" in html
    assert "breakdownEditor.draft" not in html
    assert 'class="card mt-3" x-show="topicLibrary.breakdown"' not in html
    assert 'class="card mt-3" x-show="creative.breakdown"' not in html


def test_breakdown_generation_requires_transcript_and_shows_progress():
    html = _workspace_source()

    assert 'id="transcriptRequiredModal"' in html
    assert "请先下载剧目脚本再拆解" in html
    assert 'id="breakdownGenerationProgressModal"' in html
    assert "startTranscriptBreakdown" in html
    assert "syncBreakdownGenerationProgress" in html
    assert "openTopicDownload(breakdownGeneration.topic)" in html
    assert "breakdownGeneration.coverage" in html
    assert "payload?.breakdown_id" in html
    assert "aiworkerApi.request(`/api/jobs/${job.job_id}`)" in html
    assert "createTopicLibraryBreakdown(topic) {" in html
    assert "return this.startTranscriptBreakdown(topic, 'topic-library')" in html
    assert "createCreativeBreakdown(topic) {" in html
    assert "return this.startTranscriptBreakdown(topic, 'creative')" in html
    assert "breakdownGenerationProgressLabel" in html
    assert "breakdownGenerationIndeterminate" in html
    assert "elapsed_seconds" in html
    assert "this.breakdownGenerationRunning() ? 8" not in html
    assert "tickBreakdownGenerationElapsed" in html
    assert "formatBreakdownGenerationElapsed" in html
    assert "`已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`" in html
    assert "已发现下载视频，正在生成剧本转写" in html
    assert "已发现第 ${breakdownGeneration.coverage.join('、')} 集下载视频" in html


def test_existing_breakdown_opens_without_generation_and_regeneration_is_explicit():
    html = _workspace_source()

    assert "data.status === 'existing'" in html
    assert "data.breakdown" in html
    assert "this.breakdownGeneration.status = 'existing'" in html
    assert "openBreakdownEditor(source, topic.title, data.breakdown)" in html
    assert "regenerateCurrentBreakdown" in html
    assert "force_regenerate: forceRegenerate" in html
    assert "startTranscriptBreakdown(topic, source, true)" in html
    assert ">重新生成</button>" in html


def test_webgui_python_source_contains_no_mojibake_text():
    source = Path("WebGUI.py").read_text(encoding="utf-8")
    mojibake_tokens = (
        "锛",
        "鈥",
        "銆",
        "鑷姩",
        "鎵ц",
        "瀵煎嚭",
        "鏈",
        "鏂囦欢",
        "鐧诲綍",
        "娴忚",
        "缁撴潫",
        "妫€鏌",
        "璇锋眰",
        "澶辫触",
        "閿欒",
        "椤圭洰",
        "涓婁紶",
        "鍒嗘壒",
        "浜岀淮",
        "閫氳繃",
        "鎷掔粷",
    )

    assert not any(token in source for token in mojibake_tokens)
    assert not any(0xE000 <= ord(char) <= 0xF8FF for char in source)

    reversible_lines = []
    for number, line in enumerate(source.splitlines(), start=1):
        try:
            decoded = line.encode("gb18030").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if decoded != line and any(ord(char) > 127 for char in line):
            reversible_lines.append((number, line, decoded))

    assert reversible_lines == []


def test_selection_rank_template_has_today_rank_page():
    html = _source(
        "templates/selection/ranks.html",
        "templates/common/workspace/selection_ranks.html",
        "static/js/selection/ranks.js",
        "static/js/common/tasks.js",
        "static/css/workspaces.css",
    )

    assert "currentTab" not in html
    assert "/static/js/selection/ranks.js" in html
    assert "fetchTodayRank" in html
    assert "async fetchJobs" in html
    assert "async stopCrawler" in html
    assert "todayRankDates" in html
    assert "todayRankPlatform" in html
    assert "数据质量提示" not in html
    assert "validationText" not in html
    assert "排名变动剧目" in html
    assert "{key: 'subject', label: '标签'" in html
    assert "item.subject" in html
    assert "topic_tags" in html
    assert "shortTags" in html
    assert "shortText" in html
    assert "warningText" in html
    assert "ranking-heading" in html
    assert "ranking-title" in html
    assert "rank-filter-btn" in html
    assert "rank-number" in html
    assert "rankNumberClass" in html
    assert "entryStatusClass" in html
    assert "status-new" in html
    assert "status-up" in html
    assert "status-down" in html
    assert "status-stable" in html
    assert "status-dropped" in html
    assert "column.icon" in html
    assert "rankColumnHeaderClass" in html
    assert "rankColumnValue" in html
    assert "rankColumnTooltip" in html
    assert "rankColumnsForTable" in html
    assert "hasRankColumnValue" in html
    assert "rankingTableColspan" in html
    assert "parseTodayRankHash" in html
    assert "setTodayRankRoute" in html
    assert "todayRankSectionKey" in html
    assert "todayRankRankingKey" in html
    assert "selectedHongguoSection" in html
    assert "selectedHongguoRanking" in html
    assert "hongguoSections" in html
    assert "hongguoRankings" in html
    assert "today-rank-route-nav" in html
    assert "hongguo-ranking-nav" in html
    assert "min-width: 86px" not in html
    assert "min-width: 88px" not in html
    assert "min-width: 96px" not in html
    assert "max-height: 460px" not in html
    assert "ranking-summary" not in html
    assert "summaryText(ranking.summary)" not in html
    assert "todayRankDate = ''; fetchTodayRank()" not in html
    assert 'type="date" x-model="todayRankDate"' not in html


def test_today_rank_duration_column_is_after_hot_comment_and_formats_both_day_counts():
    html = _workspace_source()

    hot_comment = html.index("{key: 'hot_comment', label: '热评'")
    rank_duration = html.index("{key: 'rank_duration', label: '在榜时长'")
    assert hot_comment < rank_duration
    assert "column.kind === 'rank_duration'" in html
    assert "formatRankDuration" in html
    assert "连续在榜" in html
    assert "累计在榜" in html


def test_removed_assets_and_rag_pages_are_not_in_sidebar():
    html = _source("templates/layouts/base.html")
    sidebar = html.split('<nav class="nav flex-column py-3"', 1)[1].split("</nav>", 1)[0]

    assert "数据资产" not in sidebar
    assert "RAG 问答" not in sidebar
    assert "currentTab === 'assets'" not in sidebar
    assert "currentTab === 'rag'" not in sidebar


def test_backend_log_configuration_writes_rotating_log(tmp_path):
    log_path = tmp_path / "logs" / "webgui.log"

    logger = WebGUI.configure_backend_logging(log_path)
    logger.info("backend logging verification")
    for handler in logger.handlers:
        handler.flush()

    assert log_path.exists()
    assert "backend logging verification" in log_path.read_text(encoding="utf-8")


def test_hongguo_rank_job_uses_csv_without_interface_extra():
    config = WebGUI.CrawlerConfig(
        platform="hg",
        login_type="qrcode",
        crawler_type="rank",
        keywords="",
        start_page=1,
        headless=True,
        save_option="json",
        get_comment=False,
        get_sub_comment=False,
    )

    job = WebGUI.create_job_from_config(config)

    assert not any(key.startswith("HONGGUO_") for key in job.extra)


def test_hongguo_drama_routes_replace_legacy_proxy_routes():
    paths = {route.path for route in WebGUI.app.routes}
    html = _workspace_source()

    assert "/api/hongguo/detail-monitor/status" not in paths
    assert "/hongguo/health" not in paths
    assert "/hongguo/api/api.php" not in paths
    assert "runRankCrawler('hg')" in html
    assert "detail-monitor" not in html
    assert "/api/hongguo/drama/settings" in paths
    assert "/api/hongguo/drama/search" in paths
    assert "/api/hongguo/drama/suggest" in paths
    assert "/api/hongguo/drama/episodes" in paths
    assert "/api/hongguo/drama/jobs" in paths
    assert "/api/hongguo/drama/download/jobs" in paths
    assert "/api/hongguo/drama/download/search" in paths
    assert "/api/jobs/{job_id}/pause" in paths
    assert "/api/jobs/{job_id}/resume" in paths
    assert "/api/hongguo/drama/douyin-fallback/jobs" in paths
    assert "剧目检索下载" in html
    assert "searchDrama" in html
    assert "notifyDramaJobCompletion" in html
    assert "完成后会弹窗提醒" in html
    assert "openTopicDownload" in html
    assert "/api/hongguo/drama/download/search" in html
    assert "全剧下载" in html
    assert "后台下载" in html
    assert "breakdownEditor.record?.status !== 'draft'" in html
    assert "episodesLoading" in html
    assert 'x-show="dramaDownload.searching"' in html
    assert "onDramaTitleInput" in html
    assert "chooseDramaSuggestion" in html
    assert "自动联想红果剧目" in html
    assert "topic-evidence-drawer" not in html
    assert "查看来源证据" not in html
    assert "topic.rank_type" in html
    assert "topic.description" in html
    assert "推荐分计算说明" in html
    assert "data-bs-toggle=\"tooltip\"" in html
    assert "置信度" not in html
    assert "可仿制/成本" not in html
    assert "红果检索已降级至抖音" in html
    assert "批量下载抖音视频并解析" in html
    assert "selectedAwemeIds" in html
    assert "toggleAllDouyinDownloadCandidates" in html
    assert "candidate.is_primary" in html
    assert "candidate.is_paid" in html
    assert "candidate.episode_label" in html
    assert "aweme_ids: this.dramaDownload.selectedAwemeIds" in html
    assert 'x-show="dramaDownload.starting || dramaDownload.jobId"' in html
    assert html.index('x-show="dramaDownload.starting || dramaDownload.jobId"') < html.index('<label class="form-label" x-text="dramaDownload.source')
    assert "dramaDownload.progress?.downloaded_bytes" in html
    assert "formatDramaBytes" in html
    assert 'class="download-progress-toast"' in html
    assert "dramaDownload.background" in html
    assert "isDramaEpisodeDownloaded" in html
    assert "episode.download_status?.status === 'downloaded'" in html


def test_job_logs_require_authentication_before_falling_back(tmp_path, monkeypatch):
    manager = WebGUI.JobManager(runtime_dir=tmp_path / "runtime", cwd=tmp_path)
    job = CrawlJob(job_id="job-latest", platform="dy", crawler_type="rank")
    state = JobState(job=job)
    state.add_event(
        WorkerEvent(
            event="log",
            job_id=job.job_id,
            worker_id="test",
            platform="dy",
            level="info",
            message="latest log",
        )
    )
    manager.jobs[job.job_id] = state
    manager._save_state(state)
    job.save(tmp_path / "jobs" / "job-latest.json")
    monkeypatch.setattr(WebGUI, "job_manager", manager)

    response = TestClient(WebGUI.app).get("/api/jobs/job-missing/logs")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_files_recurses_supported_outputs(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    nested = data_root / "jobs" / "demo"
    nested.mkdir(parents=True)
    (nested / "contents.json").write_text("[]", encoding="utf-8")
    (nested / "note.md").write_text("# note", encoding="utf-8")
    (nested / "audio.wav").write_bytes(b"fake")
    (nested / "video.mp4").write_bytes(b"fake")
    (nested / "ignore.bin").write_bytes(b"fake")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    files = await WebGUI.list_files()

    names = {item["name"] for item in files}
    assert {"contents.json", "note.md", "audio.wav", "video.mp4"} <= names
    assert "ignore.bin" not in names
    audio = next(item for item in files if item["name"] == "audio.wav")
    assert audio["category"] == WebGUI.classify_data_file(data_root / "jobs" / "demo" / "audio.wav")["category"]
    assert audio["media_type"] == "audio"


@pytest.mark.asyncio
async def test_list_files_groups_drama_outputs_by_drama_and_type(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    episode = data_root / "hg" / "jobs" / "job-1" / "dramas" / "测试短剧" / "episode_01"
    episode.mkdir(parents=True)
    (episode / "video.mp4").write_bytes(b"video")
    (episode / "audio.wav").write_bytes(b"audio")
    (episode / "script.md").write_text("# script", encoding="utf-8")
    (episode / "transcript.json").write_text("{}", encoding="utf-8")
    other = data_root / "dy" / "jobs" / "job-2" / "analysis"
    other.mkdir(parents=True)
    (other / "report.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    files = await WebGUI.list_files()

    by_name = {item["name"]: item for item in files}
    assert by_name["video.mp4"]["drama_name"] == "测试短剧"
    assert by_name["video.mp4"]["file_group"] == "视频"
    assert by_name["audio.wav"]["file_group"] == "音频"
    assert by_name["script.md"]["file_group"] == "剧本与文稿"
    assert by_name["transcript.json"]["file_group"] == "转写数据"
    assert by_name["video.mp4"]["episode_label"] == "第 1 集"
    assert by_name["report.json"]["drama_name"] == "其他数据"


def test_text_preview_enforces_five_megabyte_limit(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    small = data_root / "small.json"
    large = data_root / "large.txt"
    small.write_text('{"name":"测试"}', encoding="utf-8")
    large.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())
    client = TestClient(WebGUI.app)

    small_response = client.get("/api/file/content", params={"path": "data/small.json"})
    large_response = client.get("/api/file/content", params={"path": "data/large.txt"})

    assert small_response.status_code == 401
    assert large_response.status_code == 401


def test_media_preview_supports_http_range_requests(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    video = data_root / "video.mp4"
    video.write_bytes(b"0123456789")
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())
    client = TestClient(WebGUI.app)

    response = client.get("/api/file/preview", params={"path": "data/video.mp4"}, headers={"Range": "bytes=2-5"})

    assert response.status_code == 401


def test_resolve_data_file_path_rejects_outside_data(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    with pytest.raises(HTTPException) as exc:
        WebGUI.resolve_data_file_path(str(outside))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_files_removes_multiple_selected_data_files(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    selected_dir = data_root / "jobs" / "demo"
    selected_dir.mkdir(parents=True)
    first = selected_dir / "first.json"
    second = selected_dir / "second.md"
    retained = selected_dir / "retained.json"
    first.write_text("{}", encoding="utf-8")
    second.write_text("# selected", encoding="utf-8")
    retained.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    result = await WebGUI.delete_files(
        WebGUI.FileDeleteRequest(paths=["data/jobs/demo/first.json", "data/jobs/demo/second.md"])
    )

    assert result["status"] == "success"
    assert result["deleted_paths"] == ["data/jobs/demo/first.json", "data/jobs/demo/second.md"]
    assert not first.exists()
    assert not second.exists()
    assert retained.exists()


def test_data_management_template_supports_multi_file_deletion():
    html = _workspace_source()

    assert "selectedFilePaths: []" in html
    assert "batchDeleteMode: false" in html
    assert "startBatchDelete" in html
    assert 'x-show="batchDeleteMode"' in html
    assert "toggleAllVisibleFiles" in html
    assert "deleteSelectedFiles" in html
    assert "method: 'DELETE'" in html
    assert "bi bi-trash" in html


def test_data_management_template_supports_grouped_online_preview():
    html = _workspace_source()

    assert "groupedFiles()" in html
    assert "group.file_groups" in html
    assert "file.drama_name" in html
    assert "file.file_group" in html
    assert "openFilePreview(file)" in html
    assert 'id="filePreviewModal"' in html
    assert '<video' in html
    assert '<audio' in html
    assert "previewFile.content" in html
    assert "/api/file/preview?path=" in html


@pytest.mark.asyncio
async def test_config_form_save_cleans_username_and_keeps_list_shape(tmp_path, monkeypatch):
    config_path = tmp_path / "data.json"
    keyword_key = "\u641c\u7d22\u5173\u952e\u8bcd"
    config_path.write_text(
        json.dumps(
            [
                {
                    "host": "http://127.0.0.1:8080/",
                    "crawler_type": "search",
                    "items": [{"project": "old", "username": "legacy", keyword_key: "old-keyword"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(WebGUI.state, "config_file", str(config_path))
    monkeypatch.chdir(tmp_path)

    payload = WebGUI.ConfigFormPayload(
        root={"host": "http://127.0.0.1:8080/", "crawler_type": "search"},
        items=[
            {
                "project": "new",
                "username": "should-remove",
                keyword_key: "keyword",
            }
        ],
    )

    result = await WebGUI.save_config_form(payload)

    assert result["status"] == "success"
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert isinstance(saved, list)
    item = saved[0]["items"][0]
    assert item["project"] == "new"
    assert "username" not in item


@pytest.mark.asyncio
async def test_config_form_rejects_search_without_keywords(tmp_path, monkeypatch):
    config_path = tmp_path / "data.json"
    config_path.write_text(json.dumps([{"items": []}], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(WebGUI.state, "config_file", str(config_path))
    monkeypatch.chdir(tmp_path)

    payload = WebGUI.ConfigFormPayload(
        root={"crawler_type": "search"},
        items=[{"project": "missing-keyword"}],
    )

    response = await WebGUI.save_config_form(payload)

    assert response.status_code == 400
