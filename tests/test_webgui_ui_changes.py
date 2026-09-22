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
import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader

import WebGUI
from core.events import WorkerEvent
from core.job import CrawlJob
from runtime.job_state import JobState
from identity.schema import Principal


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


def test_llm_settings_supports_credential_vault_rotation_without_key_echo():
    html = _source(
        "templates/admin/config.html",
        "templates/common/workspace/free_creative_modals.html",
    )
    script = _source(
        "static/js/common/tasks.js",
        "static/js/admin/workspace.js",
    )

    assert "llm-provider-api-key-env-var" in html
    assert "API Key 环境变量" in html
    assert 'for="llm-provider-api-key"' in html
    assert 'id="llm-provider-api-key"' in html
    assert 'type="password"' in html
    assert 'autocomplete="new-password"' in html
    assert "清除页面密钥" in html
    assert "Fernet 加密保存" not in html
    assert "api_key_env_var" in script
    assert "/credential" in script
    assert "api_key: ''" in script


def test_llm_model_settings_omit_pricing_and_prompt_ab_controls():
    html = _source("templates/admin/config.html")
    script = _source(
        "static/js/common/tasks.js",
        "static/js/admin/workspace.js",
    )

    for removed in (
        "输入/百万 Token",
        "输出/百万 Token",
        "未配置 Token 单价",
        "llm-manual-model-currency",
    ):
        assert removed not in html
    for removed in (
        "input_price_per_million",
        "output_price_per_million",
        "price_currency",
    ):
        assert removed not in script

    assert "llm-manual-model-max-output-tokens" in html
    assert "max_output_tokens" in script


def test_webgui_does_not_register_prompt_ab_or_iteration_routes():
    paths = {route.path for route in WebGUI.app.routes}

    assert not any("/prompt-library/ab-tests" in path for path in paths)
    assert not any("/prompt-library/iteration-drafts" in path for path in paths)


def test_webgui_registers_rag_routes():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/rag/status" in paths
    assert "/api/rag/index" in paths
    assert "/api/rag/query" in paths


def test_webgui_shares_creative_application_dependencies():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/creative/projects/{project_id}/blueprints" in paths
    assert WebGUI.creative_application_service.repository is WebGUI.creative_repository
    assert (
        WebGUI.creative_application_service.workflow_adapter
        is WebGUI.creative_workflow_adapter
    )
    assert (
        WebGUI.workflow_transition_service.creative_adapter
        is WebGUI.creative_workflow_adapter
    )


def test_creative_generation_routes_use_only_public_application_service_api():
    source = _source("creative/api.py")

    assert "._snapshot_extra(" not in source
    assert "start_generation_compat(" in source


def test_webgui_registers_analytics_routes():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/analytics/sync" in paths
    assert "/api/analytics/overview" in paths
    assert "/api/analytics/dramas" in paths
    assert "/api/analytics/assets" in paths
    assert "/api/analytics/sync-all" not in paths


def test_webgui_registers_today_rank_route():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/api/rank/today" in paths
    assert "/favicon.ico" in paths


def test_user_list_normalizes_datetime_values_with_short_timezone_suffix():
    source = _source("static/js/admin/users.js")

    assert r"/(?:Z|[+-]\d\d(?::?\d\d)?)$/" in source


def test_system_admin_resources_do_not_include_team_mode_branches():
    users_template = _source("templates/admin/users.html")
    roles_template = _source("templates/admin/roles.html")
    users_script = _source("static/js/admin/users.js")
    roles_script = _source("static/js/admin/roles.js")

    for source in (users_template, roles_template, users_script, roles_script):
        assert "team_mode" not in source
        assert "teamMode" not in source
        assert "/api/team/" not in source

    assert "/api/admin/users" in users_script
    assert "/api/admin/roles" in roles_script
    assert "页面授权" in roles_template


def test_douyin_today_rank_uses_switchable_rank_and_heat_boards():
    script = _source("static/js/selection/ranks.js")
    template = _source("templates/common/workspace/selection_ranks.html")

    assert "douyinSections()" in script
    assert "selectedDouyinSection()" in script
    assert "this.todayRankSectionKey = parts[2] || '榜单'" in script
    assert "todayRankPlatform === 'dy'" in template
    assert "setTodayRankRoute('dy', section.key)" in template


def test_hongguo_today_rank_opens_explanations_from_an_info_icon():
    template = _source("templates/common/workspace/selection_ranks.html")
    page = _source("templates/selection/ranks.html")
    modal = _source("templates/common/workspace/hongguo_rank_info_modal.html")
    styles = _source("static/css/workspaces.css")

    assert 'x-show="todayRankPlatform === \'hg\'"' in template
    assert 'data-bs-target="#hongguoRankInfoModal"' in template
    assert 'aria-label="查看红果榜单说明"' in template
    assert 'bi-info-circle' in template
    assert "推荐榜:观看/互动量及个人兴趣排名" not in template
    assert 'hongguo_rank_info_modal.html' in page
    assert 'id="hongguoRankInfoModal"' in modal
    assert "榜单说明" in modal
    assert "推荐榜:观看/互动量及个人兴趣排名" in modal
    assert "热播榜:全局观看/互动综合热度排名" in modal
    assert "臻果榜:根据红果已开分真人剧评分排序" in modal
    assert "预约榜:基于红果预约/预告片播放等综合期待值排序" in modal
    assert "新剧榜:上架3天内剧集热度值排名" in modal
    assert "热搜榜:与前一日搜索热度值排序" in modal
    assert "必看榜:按2025年后上线的剧集" in modal
    assert "收藏榜:以实时累计收藏人数排名" in modal
    assert "更新说明" in modal
    assert "数据源于红果抓包每个工作日不定期更新" in modal
    assert ".hongguo-rank-info-modal .modal-title" in styles
    assert "color: #f7f9ff;" in styles


def test_today_rank_supports_adoption_actions_and_shared_result_modal():
    rank_template = _source("templates/common/workspace/selection_ranks.html")
    rank_script = _source("static/js/selection/ranks.js")
    workspace = _source("static/js/common/tasks.js")
    breakdowns = _source("static/js/selection/breakdowns.js")
    modal = _source("templates/common/workspace/adoption_notice_modal.html")
    styles = _source("static/css/workspaces.css")

    assert rank_template.count('class="rank-adopt-action"') == 4
    assert rank_template.count('@click="adoptRankEntry(item)"') == 2
    assert rank_template.count('rankingTableColspan(tbl) + 1') == 2
    assert "/api/creative/topics/adopt-from-rank" in rank_script
    assert "rankAdoptionFailureMessage" in rank_script
    assert "收录接口尚未生效，请刷新页面或重启 Web 服务。" in rank_script
    assert "adoptionNotice" in workspace
    assert "showAdoptionNotice" in workspace
    assert "showAdoptionNotice" in breakdowns
    assert "采纳成功" in modal
    assert "采纳失败" in modal
    assert ".today-rank-table .rank-adopt-action" in styles


def test_webgui_registers_creative_phase3_routes_and_page():
    paths = {route.path for route in WebGUI.app.routes}
    html = _workspace_source()

    assert "/api/creative/topics/generate" in paths
    assert "/api/creative/breakdowns" in paths
    assert "今日爆款" in html
    assert "generateCreativeTopics" in html
    assert '@change="changeCreativePlatform"' in html


def test_webgui_registers_script_review_page_and_api():
    paths = {route.path for route in WebGUI.app.routes}
    html = _workspace_source()

    assert "/review/scripts" in paths
    assert "/api/script-reviews" in paths
    assert "/api/script-reviews/{review_id}/confirm" in paths
    assert "剧本审核" in html


def test_webgui_registers_independent_writer_agent_workbench():
    paths = {route.path for route in WebGUI.app.routes}
    template = _source("templates/review/writer_agent.html")
    review_template = _source("templates/review/_writer_project_review.html")
    script = _source("static/js/review/writer_agent.js")

    assert "/review/writer-agent" in paths
    assert "/api/writer-agent/sessions" in paths
    assert "/api/writer-agent/sessions/{session_id}/export" in paths
    assert "/api/writer-agent/sessions/{session_id}/reports/{report_id}/export" in paths
    assert "/api/writer-agent/sessions/{session_id}/actions/{action_id}" in paths
    assert (
        "/api/writer-agent/sessions/{session_id}/actions/{action_id}/decision"
        in paths
    )
    assert (
        "/api/writer-agent/sessions/{session_id}/actions/{action_id}/retry" in paths
    )
    assert "/api/writer-agent/sessions/{session_id}/project-reviews/preview" in paths
    assert "/api/writer-agent/sessions/{session_id}/project-reviews/{review_id}" in paths
    assert "/api/writer-agent/sessions/{session_id}/project-reviews/{review_id}/resume" in paths
    assert "/api/writer-agent/sessions/{session_id}/project-reviews/{review_id}/export" in paths
    assert WebGUI.app.state.writer_action_service is not None
    assert "编剧大师" in template
    assert "对话评审、原创立项与项目创作" in template
    assert "writer-agent-project-context" in template
    assert "创建原创项目" in template
    assert "关联已有项目" in template
    assert "writer-agent-file" in template
    assert ".docx,.pdf" in template
    assert "/api/writer-agent/sessions/file" in script
    assert "renderReport" in script
    assert "writerAgentWorkbench" in script
    assert "/actions/${action.id}/decision" in script
    assert "writerProjectReviewUI" in script
    assert '{% include "review/_writer_project_review.html" %}' in template
    assert "writer-project-review-drawer" in review_template
    assert "writer_project_review.js" in template


def test_writer_agent_uses_three_panel_chat_and_report_history_layout():
    template = _source("templates/review/writer_agent.html")
    script = _source("static/js/review/writer_agent.js")

    assert "writer-agent-layout" in template
    assert "writer-agent-session-detail" in template
    assert "writer-agent-reports" in template
    assert "writer-agent-project-progress" in template
    assert "writer-agent-inspector" in template
    assert "writer-agent-composer" in template
    assert "renderWriterProjectProgress" in script
    assert "renderReportFiles" in script
    assert "/reports/${reportId}/export" in script


def test_writer_agent_does_not_start_an_automatic_review_after_material_upload():
    script = _source("static/js/review/writer_agent.js")

    assert "请对这份材料做一次全面的短剧/漫剧编剧评审" not in script
    assert "请结合刚刚提交的补充材料继续评审" not in script


def test_writer_agent_can_expand_and_hide_the_review_reports_panel():
    template = _source("templates/review/writer_agent.html")
    script = _source("static/js/review/writer_agent.js")

    assert "writer-agent-toggle-reports" in template
    assert "writer-agent-layout.reports-hidden" in template
    assert "toggleReportsPanel" in script
    assert "reports-hidden" in script


def test_writer_agent_shows_an_immediate_thinking_state_while_waiting_for_a_reply():
    template = _source("templates/review/writer_agent.html")
    script = _source("static/js/review/writer_agent.js")

    assert "writer-agent-thinking" in template
    assert "beginThinking" in script
    assert "正在梳理结构与人物关系" in script
    assert "pendingMessage" in script


def test_script_review_page_renders_episode_and_cross_episode_findings():
    script = _source("static/js/review/scripts.js")

    assert "review.report.episode_reviews" in script
    assert "review.report.cross_episode_findings" in script


def test_script_review_uses_a_dropdown_for_single_episode_selection():
    script = _source("static/js/review/scripts.js")

    assert "mode.value === 'single'" in script
    assert "document.createElement('select')" in script
    assert "episodes.querySelector('select')" in script


def test_script_review_deduplicates_core_and_episode_findings():
    script = _source("static/js/review/scripts.js")

    assert "coreFindingKeys" in script
    assert "episodeFindings" in script


def test_script_review_groups_report_content_with_clear_headings():
    script = _source("static/js/review/scripts.js")

    assert "修改建议" in script
    assert "剧情概要" in script
    assert "跨集问题" in script


def test_script_review_renders_a_partial_report_after_an_interrupted_run():
    script = _source("static/js/review/scripts.js")

    assert "阶段性报告" in script
    assert "completed_episode_count" in script
    assert "跨集汇总尚未完成" in script


def test_script_review_history_offers_a_delete_action_for_finished_records():
    script = _source("static/js/review/scripts.js")

    assert "/api/script-reviews/${reviewId}" in script
    assert "删除审核记录" in script
    assert "bi-trash" in script


def test_script_review_labels_cancelled_reviews_as_cancelled_not_running():
    script = _source("static/js/review/scripts.js")

    assert "cancelled: '已取消'" in script


def test_blueprint_table_shows_why_each_imitation_direction_was_generated():
    blueprint_template = _source("templates/common/workspace/project_blueprints.html")
    creative_script = _source("static/js/common/creative.js")

    assert "仿制原因" in blueprint_template
    assert "imitationReason(item.content)" in blueprint_template
    assert "imitationReason(selectedProjectBlueprint().content)" in blueprint_template
    assert "imitationReason(content)" in creative_script


def test_blueprint_text_clamp_does_not_break_table_row_alignment():
    blueprint_template = _source("templates/common/workspace/project_blueprints.html")
    workspace_css = _source("static/css/workspaces.css")

    assert '<td class="blueprint-conflict-cell" style=' not in blueprint_template
    assert blueprint_template.count('class="blueprint-conflict-clamp"') == 2
    assert ".blueprint-conflict-clamp" in workspace_css


def test_character_workspace_can_choose_among_locked_brief_directions():
    template = _source("templates/common/workspace/project_characters.html")
    script = _source("static/js/common/creative.js")

    assert "创作方向（锁定 Brief）" in template
    assert "availableLockedBriefs()" in template
    assert "selectCharacterBrief" in template
    assert "/briefs/${briefId}/select" in script


def test_locking_a_brief_saves_edited_project_episode_count_first():
    script = _source("static/js/common/creative.js")
    method = script.split("async completeProjectBrief()", 1)[1].split("async createBlankCharacterBatch", 1)[0]

    assert method.index("await this.saveCreativeProjectSettings()") < method.index("completeCreativeResource('brief'")


def test_outline_workspace_can_choose_locked_brief_and_character_versions():
    template = _source("templates/common/workspace/project_outlines.html")
    script = _source("static/js/common/creative.js")

    assert "创作方向（锁定 Brief）" in template
    assert "人物关系版本（已锁定）" in template
    assert "availableLockedCharacterBatches()" in template
    assert "selectOutlineCharacterBatch" in template
    assert "/character-batches/${batchId}/select" in script


def test_outline_workspace_explains_when_current_direction_has_no_locked_character_batch():
    template = _source("templates/common/workspace/project_outlines.html")

    assert "暂无与当前创作方向匹配的已锁定人物关系" in template
    assert "前往人物关系" in template


def test_script_review_can_start_and_review_optimization_drafts():
    script = _source("static/js/review/scripts.js")

    assert "/optimizations" in script
    assert "生成优化草稿" in script
    assert "script-optimization-drafts" in script
    assert "编辑草稿" in script


def test_script_review_project_selector_includes_free_projects_and_uses_heads():
    script = _source("static/js/review/scripts.js")

    assert "item.outline_batch_id || item.workflow_mode === 'free'" in script
    assert "projectData.resource_heads" in script
    assert "reviewableScripts" in script


def test_script_review_multi_episode_picker_has_a_select_all_control():
    script = _source("static/js/review/scripts.js")

    assert "script-review-select-all-episodes" in script
    assert "toggleAllReviewEpisodes" in script
    assert "取消全选" in script


def test_partial_outline_checkpoints_do_not_mark_the_generation_job_complete():
    script = _source("static/js/common/creative.js")
    method = script.split("creativeProjectHasJobResult(project, action, jobId)", 1)[1].split(
        "finishCreativeGeneration", 1
    )[0]

    assert "generatedForJob.length >= Number(project.target_episode_count || 0)" in method


def test_partial_outline_checkpoints_cannot_be_locked_from_the_workspace():
    template = _source("templates/common/workspace/project_outlines.html")

    assert "currentProjectOutlines().length === Number(creativeWorkspace.project.target_episode_count || 0)" in template


def test_workspace_downloads_the_current_generated_resource_instead_of_legacy_export():
    creative = _source("static/js/common/creative.js")
    outlines = _source("templates/common/workspace/project_outlines.html")
    blueprints = _source("templates/common/workspace/project_blueprints.html")
    characters = _source("templates/common/workspace/project_characters.html")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "creativeGeneratedDownloadUrl(stage, format = 'md')" in creative
    assert "generated-content/download?${params.toString()}" in creative
    assert "/export?stage=" not in creative
    assert "creativeGeneratedDownloadUrl('outline', 'md')" in outlines
    assert "creativeGeneratedDownloadUrl('brief', 'md')" in blueprints
    assert "creativeGeneratedDownloadUrl('characters', 'md')" in characters
    assert "creativeGeneratedDownloadUrl('beats', 'md')" in beats


def test_writing_workspaces_offer_resumable_full_series_beats_and_script_generation():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "async generateProjectBeatsSeries()" in creative
    assert "/beats/generate-series" in creative
    assert "async generateProjectScriptsSeries()" in creative
    assert "/scripts/generate-series" in creative
    assert "canGenerateProjectScriptsSeries()" in creative
    assert "series_generation" in creative
    assert "generateProjectBeatsSeries" in beats
    assert "generateProjectScriptsSeries" in scripts
    assert "继续全剧生成" in creative


def test_free_workspace_can_generate_beats_for_all_episodes_at_once():
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")
    beats = _source("templates/common/workspace/project_beats.html")
    modal = _source("templates/common/workspace/free_creative_modals.html")

    assert "openFreeCreativeGeneration('beats', {series: true})" in creative
    assert "/free/${plural}/generate-series" in creative
    assert "expected_head_revisions" in creative
    assert "{series: true}" in creative
    assert "series: false" in tasks
    assert 'x-show="!isFreeCreativeProject()" @click="generateProjectBeatsSeries"' not in beats
    assert "全剧 Beats" in modal


def test_free_workspace_uses_shared_per_request_model_selector():
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")
    modal = _source("templates/common/workspace/free_creative_modals.html")
    navigation = _source(
        "templates/common/workspace/project_module_navigation.html"
    )

    assert "/api/llm/available-models" in creative
    assert "default_model_record_id" in creative
    assert "payload.llm_model_record_id" in creative
    assert "modelOptions: []" in tasks
    assert 'x-model="creativeWorkspace.freeGeneration.modelRecordId"' in modal
    assert 'x-model="creativeWorkspace.freeGeneration.reasoningEffort"' in modal
    assert 'type="radio" name="free-generation-reasoning-effort"' in modal
    assert "本次推理档位（单选）" in modal
    assert "payload.reasoning_effort" in creative
    assert "本次切换无需重新输入" in modal
    assert "item.model_label" in navigation


def test_free_workspace_can_generate_scripts_for_all_episodes_at_once():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")
    modal = _source("templates/common/workspace/free_creative_modals.html")

    assert "openFreeCreativeGeneration('script', {series: true})" in creative
    assert "/free/${plural}/generate-series" in creative
    assert "this.creativeHeadRevision(stage, number)" in creative
    assert 'x-show="!isFreeCreativeProject()" @click="generateProjectScriptsSeries"' not in scripts
    assert "全剧场次剧本" in modal
    assert "if (state.series && field === 'beats_id') return false;" in creative


def test_free_multi_episode_workspaces_offer_atomic_series_locking():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "completeFreeProjectEpisodeSeries(stage)" in creative
    assert "/free/${plural}/lock-series" in creative
    assert "expected_head_revisions" in creative
    assert "canBatchLockProjectEpisodes(stage)" in creative
    assert "全集完成并锁定" in beats
    assert "全集完成并锁定" in scripts
    assert 'completeProjectScriptsBatch' in scripts
    assert "return this.completeFreeProjectEpisodeSeries('script')" in creative


def test_beats_workspace_uses_an_episode_selector_to_review_series_drafts():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "projectBeatEpisodeLabel(episodeNumber)" in creative
    assert "projectBeatEpisodeLabel(episodeNumber)" in beats
    assert '<select class="form-select form-select-sm"' in beats


def test_writing_episode_workspaces_can_show_and_download_selected_or_all_episodes():
    navigation = _source("templates/common/workspace/project_module_navigation.html")
    outlines = _source("templates/common/workspace/project_outlines.html")
    beats = _source("templates/common/workspace/project_beats.html")
    scripts = _source("templates/common/workspace/project_scripts.html")
    modal = _source("templates/common/workspace/creative_episode_download_modal.html")
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")
    api = _source("creative/api.py")

    assert '<option :value="0">全集</option>' in navigation
    assert '<option :value="0">全集</option>' in beats
    assert '<option :value="0">全集</option>' in scripts
    assert "selected === 0" in _source("static/js/writing/outlines.js")
    assert "projectStageEpisodeItems('beats')" in beats
    assert "projectStageEpisodeItems('script')" in scripts
    assert "多集/全集下载" in outlines
    assert "多集/全集下载" in beats
    assert "多集/全集下载" in scripts
    assert "selectedEpisodes" in modal
    assert "templateOptions" in modal
    assert "在线预览" in modal
    assert 'value="md"' in modal
    assert 'value="pdf"' in modal
    assert 'value="docx"' in modal
    assert 'aria-live="polite"' in modal
    assert "downloadSelectedEpisodes()" in creative
    assert "loadScreenplayExportTemplates" in creative
    assert "previewSelectedEpisodes" in creative
    assert "params.set('template', state.templateId)" in creative
    assert "episodeView === 'all'" in tasks
    assert '"/projects/{project_id}/episodes/download"' in api
    assert '"/projects/{project_id}/episodes/export/templates"' in api
    assert '"/projects/{project_id}/episodes/export/preview"' in api


def test_brief_and_characters_navigation_hide_episode_selector():
    environment = Environment(loader=FileSystemLoader("templates"), autoescape=True)
    template = environment.get_template(
        "common/workspace/project_module_navigation.html"
    )
    context = {
        "can_open_planning_project_details": True,
        "can_open_writing_project_details": True,
    }

    for module in ("brief", "characters"):
        rendered = template.render(creative_module=module, **context)
        assert 'id="creative-module-episode"' not in rendered

    for module in ("outline", "beats", "script"):
        rendered = template.render(creative_module=module, **context)
        assert 'id="creative-module-episode"' in rendered


def test_beats_workspace_labels_single_episode_generation_as_regeneration_when_present():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "projectBeatGenerationActionLabel()" in creative
    assert "重新生成 Beats" in creative
    assert "projectBeatGenerationActionLabel()" in beats


def test_beats_workspace_offers_a_confirmed_batch_lock_for_full_current_drafts():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "async completeProjectBeatsBatch()" in creative
    assert "/beats/lock" in creative
    assert "批量完成并锁定" in beats
    assert "canBatchLockProjectBeats()" in creative


def test_review_findings_render_outline_anchors_with_writer_facing_labels():
    review_script = _source("static/js/review/scripts.js")

    assert "function outlineAnchorText(anchor)" in review_script
    assert "结尾悬念" in review_script
    assert "关联节拍" in review_script
    assert "field=" not in review_script


def test_structure_optimization_explains_when_no_finding_requires_beats_changes():
    review_script = _source("static/js/review/scripts.js")

    assert "当前没有需要调整 Beats 的问题" in review_script
    assert "可选择" in review_script
    assert "start.disabled = selectableCount === 0" in review_script


def test_script_review_uses_text_file_upload_instead_of_a_paste_editor():
    template = _source("templates/review/scripts.html")
    review_script = _source("static/js/review/scripts.js")

    assert 'id="script-review-file"' in template
    assert ".markdown" in template
    assert 'id="script-review-text"' not in template
    assert "/api/script-reviews/file" in review_script
    assert "X-Script-Filename" in review_script
    assert "粘贴文本" not in review_script


def test_review_report_offers_a_markdown_export():
    review_script = _source("static/js/review/scripts.js")

    assert "downloadReviewReport(review.id)" in review_script
    assert "/export" in review_script
    assert "下载审核报告" in review_script


def test_beats_workspace_edits_continuity_updates_as_structured_fields_not_json():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "addContinuityUpdate(beat)" in creative
    assert "removeContinuityUpdate(beat, updateIndex)" in creative
    assert "continuityUpdatesText(beat)" not in beats
    assert "连续性变更" in beats
    assert "知情人" in beats
    assert "textareaRows(update.event, 24)" in beats


def test_outline_workspace_exposes_continue_generation_for_a_saved_partial_batch():
    script = _source("static/js/common/creative.js")
    template = _source("templates/common/workspace/project_outlines.html")

    assert "hasResumableProjectOutlines()" in script
    assert "outlineGenerationActionLabel()" in script
    assert "outlineGenerationActionLabel()" in template


def test_outline_workspace_calls_generation_a_regeneration_after_a_locked_current_outline():
    script = _source("static/js/common/creative.js")
    method = script.split("outlineGenerationActionLabel()", 1)[1].split(
        "currentOutlineBatchStatus() {", 1
    )[0]

    assert "重新生成" in method
    assert "['locked', 'approved']" in method


def test_outline_workspace_does_not_resume_a_draft_from_an_old_direction_or_character_batch():
    script = _source("static/js/common/creative.js")
    function_start = script.index("latestOutlineBatchId()")
    function_end = script.index("currentProjectOutlines()", function_start)
    function = script[function_start:function_end]

    assert "item.upstream_refs?.brief_id" in function
    assert "item.upstream_refs?.character_batch_id" in function


def test_breakdown_lock_refreshes_the_reactive_editor_record():
    script = _source("static/js/selection/breakdowns.js")
    method = script.split("async submitBreakdownEditor()", 1)[1].split(
        "async saveTopicLibraryBreakdown", 1
    )[0]

    assert "this.syncBreakdownEditorRecord(data);" in method


def test_script_review_polls_optimization_tasks_until_drafts_are_ready():
    script = _source("static/js/review/scripts.js")

    assert "scheduleOptimizationHistory" in script
    assert "['queued', 'running'].includes(optimization.status)" in script
    assert "重新生成本集" not in script
    assert "acceptOptimizationDraft" not in script
    assert "locked: '已采纳'" in script
    assert "deleteFailedOptimization" in script
    assert "method: 'DELETE'" in script
    assert "删除记录" in script
    assert "此操作无法恢复" in script
    assert "请填写放弃原因" in script
    assert "rejection_note: rejectionNote" in script


def test_script_review_shows_a_progress_modal_while_the_review_runs():
    template = _source("templates/review/scripts.html")
    script = _source("static/js/review/scripts.js")

    assert 'id="scriptReviewProgressModal"' in template
    assert 'data-bs-backdrop="static"' in template
    assert "reviewProgressModal" in script
    assert "showReviewProgress" in script
    assert "updateReviewProgress" in script
    assert "hideReviewProgress" in script


def test_script_review_shows_a_progress_modal_while_generating_optimization_drafts():
    template = _source("templates/review/scripts.html")
    script = _source("static/js/review/scripts.js")

    assert 'id="scriptOptimizationProgressModal"' in template
    assert 'id="script-optimization-progress-view-drafts"' in template
    assert 'id="script-optimization-progress-retry"' in template
    assert "showOptimizationProgress" in script
    assert "updateOptimizationProgress" in script
    assert "showOptimizationProgressError" in script
    assert "retryOptimization" in script


def test_writing_pages_load_optimization_draft_comparison():
    template = _source("templates/writing/scripts.html")
    beats_template = _source("templates/writing/beats.html")
    assert "optimization_draft.js" in template
    assert 'id="optimization-draft-review-back-link"' in template
    assert 'id="optimization-draft-review-back-link"' in beats_template
    assert "optimization_draft.js?v={{ asset_version }}-return-review" in template
    assert "optimization_draft.js" in beats_template
    script = _source("static/js/writing/optimization_draft.js")
    assert "/optimization-drafts" in script
    assert "AI 优化草稿" in script
    assert "renderScriptPreview" in script
    assert "renderBeatsPreview" in script
    assert "当前版本" in script
    assert "value.split(/[,，、]/)" in script
    assert "renderScriptEditor" in script
    assert "renderBeatsEditor" in script
    assert "acceptAndLockOptimizationDraft" in script
    assert "saveOptimizationDraft" in script
    assert "regenerateOptimizationDraft" in script
    assert "renderDraftNavigation" in script
    assert "beforeunload" in script
    assert "window.location.assign(reviewPageUrl())" in script
    assert "下载草稿" in script
    assert "保存草稿" in script
    assert "新增节拍" in script
    assert "删除节拍" in script
    assert "新增变更" in script
    assert "删除变更" in script
    assert "应用为新版本" not in script
    assert "应用并锁定" not in script
    assert "锁定并采纳" in script
    assert "上一份" in script
    assert "下一份" in script
    assert "重新生成本集" in script
    assert "renderDraftComparison" in script
    assert "waitForScriptWorkspace" in script
    assert "返回审核" in script
    assert "optimization-draft-review-back-link" in script
    assert "review_id=${reviewId}" in script
    assert "computeLineDiff" in script
    assert "line.scope" in script
    assert "uniqueLinePositions" in script
    assert "diff-removed" in script
    assert "diff-added" in script
    assert ".optimization-diff-line.diff-removed" in _source("static/css/workspaces.css")
    assert ".optimization-diff-line.diff-added" in _source("static/css/workspaces.css")

    beats_workspace = _source("templates/common/workspace/project_beats.html")
    module_navigation = _source("templates/common/workspace/project_module_navigation.html")
    project_progress = _source("templates/common/workspace/project_progress.html")
    assert 'id="project-beats-workspace"' in beats_workspace
    assert 'id="writing-beats-page-header"' in beats_template
    assert 'id="project-module-navigation"' in module_navigation
    assert 'id="project-generation-progress"' in project_progress
    assert "function prepareBeatsOptimizationLayout()" in script
    assert "'optimization-draft-top-actions'" in script
    assert "actions.append(backLink)" in script
    assert "prepareBeatsOptimizationLayout();" in script
    assert "function hideOriginalWorkspace()" in script
    assert "'project-beats-workspace'" in script
    assert "'#writing-beats-page-header'" in script
    assert "'#project-module-navigation'" in script
    assert "'#project-generation-progress'" in script
    assert "hideOriginalWorkspace();" in script
    assert "hideConflictingBeatsLocks" not in script

    review_script = _source("static/js/review/scripts.js")
    assert "review_id" in review_script
    assert "openReview(returnReviewId)" in review_script
    assert "&review_id=${review.id}" in review_script


def test_my_projects_support_editing_and_permanent_deletion():
    template = _source("templates/common/workspace/my_tasks.html")
    script = _source("static/js/common/my_tasks.js")

    assert 'id="project-edit-modal"' in template
    assert 'id="project-delete-modal"' in template
    assert "bi-pencil" in script
    assert "bi-trash" in script
    assert "method: 'PATCH'" in script
    assert "method: 'DELETE'" in script
    assert "删除后将同时清除" in template


def test_new_brief_version_keeps_project_name_readonly_but_allows_setting_edits():
    template = _source("templates/common/workspace/project_blueprints.html")

    assert 'x-model="creativeWorkspace.project.name" :disabled="true"' in template
    assert 'x-model="creativeWorkspace.project.genre" :disabled="!creativeResourceEditable(selectedProjectBlueprint())"' in template
    assert 'x-model="creativeWorkspace.project.target_platform" :disabled="!creativeResourceEditable(selectedProjectBlueprint())"' in template
    assert 'x-model="creativeWorkspace.project.target_audience" :disabled="!creativeResourceEditable(selectedProjectBlueprint())"' in template
    assert 'x-model.number="creativeWorkspace.project.target_episode_count" :disabled="!creativeResourceEditable(selectedProjectBlueprint())"' in template
    assert 'x-model.number="creativeWorkspace.project.episode_duration_seconds" :disabled="!creativeResourceEditable(selectedProjectBlueprint())"' in template


def test_webgui_does_not_register_a_standalone_breakdown_page():
    paths = {route.path for route in WebGUI.app.routes}

    assert "/selection/breakdowns" not in paths


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
    assert "/planning/projects" in _source("web/page_routes.py")
    assert "fetchCreativeProjects" in html
    assert "generateProjectBlueprints" in html
    assert "completeProjectBrief" in html
    creative_stage_templates = "\n".join(
        _source(f"templates/common/workspace/{name}")
        for name in (
            "project_blueprints.html",
            "project_characters.html",
            "project_outlines.html",
            "project_beats.html",
        )
    )
    assert "提交审批" not in creative_stage_templates
    assert "generateProjectCharacters" in html
    assert "generateProjectOutlines" in html
    assert "generateProjectBeats" in html
    assert "pauseCreativeJob" in html
    assert "retryCreativeJob" in html
    assert "creative-workspace-scroll" not in html
    assert 'class="table table-sm table-hover align-middle mb-4"' not in html
    assert "creativeWorkspace.projects[0]?.id" in html
    assert ':href="creativeProjectStageUrl(project)"' in html
    assert "--bs-table-active-color: var(--text);" in html


def test_blank_character_batch_links_the_active_workflow_task():
    script = _source("static/js/common/creative.js")
    method = script.split("async createBlankCharacterBatch()", 1)[1].split(
        "async saveProjectCharacter", 1
    )[0]

    assert "resolveCreativeWorkflowTask('characters', projectId)" in method
    assert "workflow_task_id: task.id" in method


def test_selection_adoption_download_and_breakdown_do_not_send_workflow_tasks():
    selection = _source("static/js/selection/breakdowns.js")
    breakdown = selection.split("async startTranscriptBreakdown", 1)[1].split(
        "syncBreakdownGenerationProgress", 1
    )[0]
    download = selection.split("async startDramaDownload", 1)[1].split(
        "syncDramaDownloadProgress", 1
    )[0]

    assert "resolveCreativeWorkflowTask('breakdown'" not in breakdown
    assert "workflow_task_id: task.id" not in breakdown
    assert "resolveCreativeWorkflowTask('source_package'" not in download
    assert "workflow_task_id: task.id" not in download
    assert "topic_id: this.dramaDownload.topic.id" in download


def test_drama_material_jobs_auto_adopt_without_workflow_task():
    script = _source("static/js/selection/dramas.js")
    method = script.split("async startDramaAnalysis", 1)[1].split(
        "dramaJobArtifacts", 1
    )[0]
    template = _source("templates/common/workspace/selection_dramas.html")
    creative = _source("static/js/common/creative.js")

    assert "resolveSourcePackageWorkflowTask" not in method
    assert "/api/workflow/tasks/" not in method
    assert "workflow_task_id" not in method
    assert "topic_id: task.topic_id" not in method
    assert "正在采纳选题并创建下载任务" in method
    assert "this.dramaAnalysis.topic = data.topic" in method
    assert 'x-text="dramaAnalysis.loadingMessage"' in template
    assert 'aria-live="polite"' in template
    assert "resolveSourcePackageWorkflowTask" not in creative


def test_project_table_shares_original_mode_source_and_open_action():
    section = _source("templates/common/workspace/project_list.html")
    html = section + _source("static/css/workspaces.css")
    project_table = section.split('<table class="table table-sm align-middle mb-4 project-list-table">', 1)[1].split("</table>", 1)[0]

    assert "创作模式" in project_table
    assert "创作来源" in project_table
    assert "更新时间/操作" in project_table
    assert 'class="project-name-button"' in project_table
    assert project_table.count(':href="creativeProjectStageUrl(project)"') == 2
    assert ".creative-project-row.table-active" in html
    assert ".project-name-button:focus-visible" in html
    assert 'colspan="6"' in project_table


def test_creative_project_creation_supports_selecting_a_locked_breakdown_version():
    html = _workspace_source()

    assert "creativeWorkspace.lockedBreakdowns" in html
    assert "loadCreativeProjectBreakdowns" in html
    assert "选择拆解版本" in html
    assert ".form-select:disabled" in html
    assert "background-color: #151722;" in html


def test_topic_project_creation_displays_api_errors():
    template = _source("templates/common/workspace/project_list.html")
    creative = _source("static/js/common/creative.js")

    assert 'x-show="creativeWorkspace.error"' in template
    assert 'x-text="creativeWorkspace.error"' in template
    method = creative.split("async createCreativeProject()", 1)[1].split(
        "setOriginalBoundaries(value)", 1
    )[0]
    assert "this.creativeWorkspace.error = '';" in method
    assert "this.showCreativeNotice(this.creativeWorkspace.error, 'error');" in method


def test_topic_project_free_mode_creates_without_seed_input():
    template = _source("templates/common/workspace/project_list.html")
    tasks = _source("static/js/common/tasks.js")
    creative = _source("static/js/common/creative.js")
    creation_section = template.split(
        'data-section="topic-project-creation"', 1
    )[1].split("{% endif %}", 1)[0]

    assert "创作种子" not in creation_section
    assert "seed_text.trim()" not in creation_section
    assert "seed_text: ''" not in tasks.split("originalCreate:", 1)[0]
    method = creative.split("async createCreativeProject()", 1)[1].split(
        "setOriginalBoundaries(value)", 1
    )[0]
    assert "seed_text: ''" not in method


def test_planning_project_list_offers_permission_aware_deletion():
    template = _source("templates/common/workspace/project_list.html")
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")
    api = _source("creative/api.py")

    assert 'x-show="project.can_delete"' in template
    assert '@click="deleteCreativeProject(project)"' in template
    assert "deletingProjectId: null" in tasks
    method = creative.split("async deleteCreativeProject(project)", 1)[1].split(
        "setOriginalBoundaries(value)", 1
    )[0]
    assert "window.confirm" in method
    assert "method: 'DELETE'" in method
    assert "await this.fetchCreativeProjects()" in method
    assert 'item["can_delete"]' in api
    assert "permission_service.can_delete_project" in api


def test_mode_switch_does_not_request_a_creative_seed():
    modal = _source("templates/common/workspace/free_creative_modals.html")
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")
    migration_modal = modal.split('id="creativeModeMigrationModal"', 1)[1]
    method = creative.split("async openCreativeModeMigration()", 1)[1].split(
        "async generateProjectBlueprints()", 1
    )[0]

    assert "创作种子" not in migration_modal
    assert "modeMigration.seed_text" not in migration_modal
    assert "seed_text" not in method
    assert "modeMigration: {preview: null, confirmed:" in tasks
    assert "仅复制已保存内容" in migration_modal
    assert "原标准流程项目和任务保持不变" in migration_modal
    assert "await this.selectCreativeProject(data.project.id)" in method
    assert "new AbortController()" in method
    assert "controller.abort(), 10000" in method
    assert "aiworkerApi.isAbortError(error)" in method
    assert "迁移预检超时，请重试" in method
    assert "retryCreativeModeMigrationPreview" in method
    assert "重试预检" in migration_modal
    assert "cancelCreativeModeMigrationPreview" in tasks
    assert 'x-show.important="creativeWorkspace.modeMigration.loading"' in migration_modal
    assert 'x-show="creativeWorkspace.modeMigration.loading"' not in migration_modal
    assert 'x-show.important="creativeWorkspace.freeGeneration.loading"' in modal


def test_mode_migration_preview_loading_has_a_bounded_wait():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const nativeSetTimeout = global.setTimeout;
global.window = {
  aiworkerModules: [],
  setTimeout: (callback, _delay, ...args) => nativeSetTimeout(callback, 5, ...args),
  clearTimeout: global.clearTimeout,
};
global.aiworkerApi = {
  isAbortError: error => error?.name === 'AbortError',
  request: async (_url, options = {}) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }, {once: true});
  }),
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const component = window.aiworkerModules[0]();
Object.assign(component, {
  creativeWorkspace: {
    project: {id: 4, workflow_mode: 'sequential'},
    modeMigration: {
      preview: null, confirmed: false, loading: false,
      submitting: false, error: '', requestSequence: 0,
    },
  },
  creativeModeMigrationModal: {show() {}, hide() {}},
});
(async () => {
  const verdict = await Promise.race([
    component.openCreativeModeMigration().then(() => 'settled'),
    new Promise(resolve => nativeSetTimeout(() => resolve('still-loading'), 100)),
  ]);
  assert.equal(verdict, 'settled', 'migration preview remained loading after its request deadline');
  assert.equal(component.creativeWorkspace.modeMigration.loading, false);
  assert.match(component.creativeWorkspace.modeMigration.error, /超时/);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_draft_breakdowns_are_visible_but_not_project_eligible():
    html = _workspace_source()

    assert "breakdownStatusLabel(topic)" in html
    assert "breakdownStatusClass(topic)" in html
    assert "草稿${version} · 待提交" in html
    assert "草稿${version} · 已有批准版" in html
    assert "更新拆解" in html
    assert "继续拆解" not in _source("templates/common/workspace/selection_topics.html")
    breakdowns = _source("static/js/selection/breakdowns.js")
    assert "topicProjectEligible(topic)" in breakdowns
    assert "return !!topic?.locked_breakdown_id;" in breakdowns
    assert "aiworkerApi.request('/api/creative/topics?status=adopted&limit=500&include_lifecycle=false')" in html
    assert ':disabled="!topic.locked_breakdown_id"' in html
    assert 'x-text="creativeTopicOptionLabel(topic)"' in html


def test_today_hits_only_load_unadopted_recommendations_and_adoption_moves_them_to_library():
    topics = _source("static/js/selection/topics.js")
    breakdowns = _source("static/js/selection/breakdowns.js")
    section = _source("templates/common/workspace/selection_hits.html")

    assert "&status=pending_review" in topics.split("async fetchCreativeTopics", 1)[1].split("async fetchTopicLibrary", 1)[0]
    assert "new URLSearchParams({status: 'adopted,archived', all: 'true'})" in topics
    update_topic = breakdowns.split("async updateCreativeTopic", 1)[1].split("createCreativeBreakdown", 1)[0]
    assert "this.creative.items = this.creative.items.filter" in update_topic
    assert "await this.fetchCreativeTopics();" not in update_topic
    assert "updateCreativeTopic(topic, 'adopted')" in section
    assert "createCreativeBreakdown(topic)" not in section
    assert "topic.status === 'adopted'" not in section


def test_topic_library_keeps_archived_topics_and_offers_restore_and_delete():
    topics = _source("static/js/selection/topics.js")
    breakdowns = _source("static/js/selection/breakdowns.js")
    section = _source("templates/common/workspace/selection_topics.html")

    assert "status: 'adopted,archived'" in topics
    assert '<option value="archived">已归档</option>' in section
    assert "updateTopicLibrary(topic, 'adopted')" in section
    assert "deleteTopicLibrary(topic)" in section
    assert section.count('x-show="topic.status === \'archived\'"') == 2
    assert 'x-show="topic.status === \'adopted\' && Number(topic.transcript_episode_count || 0) > 0"' in section
    assert 'x-show="topic.status === \'adopted\' && topic.locked_breakdown_id && !topic.lifecycle_project_id"' in section
    assert 'x-show="topic.status === \'adopted\' && topic.lifecycle_project_id && topic.can_open_project"' in section
    assert "method: 'DELETE'" in breakdowns


def test_topic_library_offers_filtered_csv_and_excel_exports():
    topics = _source("static/js/selection/topics.js")
    section = _source("templates/common/workspace/selection_topics.html")

    assert "topicLibraryExportParams(format)" in topics
    assert "async fetchTopicLibrary" in topics
    assert "params.set('platform', this.topicLibrary.platform)" in topics
    assert "params.set('lifecycle_status', this.topicLibrary.lifecycleStatus)" in topics
    assert "params.set('category', this.topicLibrary.category.trim())" in topics
    assert "params.set('min_score', this.topicLibrary.minScore)" in topics
    assert "params.set('has_locked_breakdown', this.topicLibrary.hasLocked)" in topics
    assert "`/api/creative/topics/export?${params.toString()}`" in topics
    assert '@click="exportTopicLibrary(\'csv\')"' in section
    assert '@click="exportTopicLibrary(\'excel\')"' in section
    assert "导出选题库" in section


def test_today_hits_restores_the_last_selected_platform_after_refresh():
    tasks = _source("static/js/common/tasks.js")
    topics = _source("static/js/selection/topics.js")
    section = _source("templates/common/workspace/selection_hits.html")

    assert "aiworker.selection.hits.platform" in tasks
    assert "creativePlatformPreference()" in tasks
    assert "rememberCreativePlatform()" in topics
    assert "@change=\"changeCreativePlatform\"" in section


def test_today_hits_switches_generation_and_display_by_rank_source():
    tasks = _source("static/js/common/tasks.js")
    topics = _source("static/js/selection/topics.js")
    breakdowns = _source("static/js/selection/breakdowns.js")
    section = _source("templates/common/workspace/selection_hits.html")

    assert "creativeRankSourcePreference" in tasks
    assert "rankSource: creativeRankSourcePreference" in tasks
    assert "['榜单', '热榜']" in tasks
    assert "['全部', '真人剧', '漫剧', 'AI剧']" in tasks
    assert "changeCreativeRankSource" in topics
    assert "rank_source=${encodeURIComponent(this.creative.rankSource)}" in topics
    assert "const rankSource = this.creative.rankSource" in breakdowns
    assert "rank_source: rankSource" in breakdowns
    assert "creative:topics:hits:${this.creative.platform}:${this.creative.rankSource}" in topics
    assert "window.aiworkerUi?.writeCache(cacheKey, items" in topics
    assert 'aria-label="榜单来源"' in section
    assert "creativeRankSourceOptions()" in section
    assert "榜单来源" in section
    assert "topic.rank_source || topic.rank_type || '-'" in section
    assert "topic.platform" not in section


def test_today_hits_restores_the_saved_rank_source_for_the_selected_platform():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")

    script = r'''
const fs = require('fs');
const vm = require('vm');
const values = new Map([
  ['aiworker.selection.hits.rank-source.hg', '漫剧'],
]);
global.window = {
  aiworkerModules: [],
  addEventListener: () => {},
  localStorage: {
    getItem: (key) => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
  },
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
  filename: process.argv[1],
});
const component = window.aiworkerModules[0]();
const state = Object.assign({}, component, {
  creative: {platform: 'hg', rankSource: '榜单'},
  generateCreativeTopics: async () => {},
});
(async () => {
  await state.changeCreativePlatform();
  if (state.creative.rankSource !== '漫剧') {
    throw new Error(`expected saved hg source, got ${state.creative.rankSource}`);
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/selection/topics.js"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_today_hits_initial_load_generates_the_latest_top_ten():
    tasks = _source("static/js/common/tasks.js")
    hits_init = tasks.split("} else if (this.page === 'selection-hits') {", 1)[1].split(
        "} else if (this.page === 'selection-topics')", 1
    )[0]

    assert "await this.generateCreativeTopics();" in hits_init
    assert "await this.fetchCreativeTopics();" not in hits_init


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


def test_topic_library_keeps_review_download_jobs_by_drama_for_parallel_captures():
    tasks = _source("static/js/common/tasks.js")
    creative = _source("static/js/common/creative.js")
    topics = _source("templates/common/workspace/selection_topics.html")

    assert "dramaReviewJobs: {}" in tasks
    assert "dramaReviewJobForTopic(topic)" in creative
    assert "persistDramaReviewJob(state)" in creative
    assert "this.pollDramaReviewCapture(state)" in creative
    assert "dramaReviewRunningForTopic(topic)" in topics


def test_topic_library_offers_review_download_for_douyin_topics():
    paths = {route.path for route in WebGUI.app.routes}
    topics = _source("templates/common/workspace/selection_topics.html")
    creative = _source("static/js/common/creative.js")

    assert "/api/douyin/reviews/jobs" in paths
    assert "/api/douyin/reviews/jobs/{job_id}" in paths
    assert "/api/douyin/reviews/jobs/{job_id}/download" in paths
    assert "includes('dy')" in topics
    assert "isDouyinDramaReview(state)" in creative
    assert "'/api/douyin/reviews/jobs'" in creative


def test_drama_search_can_target_douyin_directly():
    paths = {route.path for route in WebGUI.app.routes}
    drama_page = _source("templates/common/workspace/selection_dramas.html")
    script = _source("static/js/selection/dramas.js")

    assert "/api/douyin/drama/search" in paths
    assert 'x-model="dramaAnalysis.platform"' in drama_page
    assert '<option value="dy">抖音</option>' in drama_page
    assert "'/api/douyin/drama/search'" in script
    assert "isDouyinDramaSearch()" in script


def test_completed_review_capture_only_shows_the_local_download_action():
    modal = _source("templates/common/workspace/selection_modals.html")

    assert 'x-show="dramaReviews.status !== \'completed\'"' in modal
    assert "dramaReviews.status === 'failed' ? '重新下载' : '开始下载'" in modal


def test_closing_a_completed_review_modal_syncs_its_row_action_state():
    tasks = _source("static/js/common/tasks.js")
    creative = _source("static/js/common/creative.js")

    assert "finishDisplayedDramaReviewJob?.()" in tasks
    assert "finishDisplayedDramaReviewJob()" in creative
    assert "['completed', 'failed'].includes(state.status)" in creative


def test_topic_library_unifies_project_creation_and_open_actions():
    topics = _source("static/js/selection/topics.js")
    table = _source("templates/common/workspace/selection_topics.html")

    assert "openCreateProject(topic)" in topics
    assert "submitCreateProject" in topics
    assert "openTopicProject(topic)" in topics
    assert "loadProjectMemberCandidates" not in topics
    assert "planner_member_ids" not in topics
    assert "writer_member_ids" not in topics
    assert "openStageWorkflowAssignment(topic)" not in topics
    assert "workflowAssignmentForStage" not in topics
    assert '@click="openCreateProject(topic)"' in table
    assert '@click="openTopicProject(topic)"' in table
    assert "分配创意策划" not in table


def test_create_project_refreshes_the_topic_library_after_success():
    topics = _source("static/js/selection/topics.js")
    submit_method = topics.split("async submitCreateProject()", 1)[1].split(
        "openCreatedProject()", 1
    )[0]

    assert "await this.fetchTopicLibrary();" in submit_method
    assert "planner_member_ids" not in submit_method
    assert "writer_member_ids" not in submit_method


def test_saving_a_scene_script_refreshes_topic_library_state():
    creative = _source("static/js/common/creative.js")
    save_method = creative.split("async saveProjectEpisode(item)", 1)[1].split(
        "async saveCreativeItem", 1
    )[0]

    assert "item.stage === 'script'" in save_method
    assert "await this.refreshCreativeTopicLibrary();" in save_method
    assert "await this.fetchTopicLibrary();" in creative.split(
        "async refreshCreativeTopicLibrary()", 1
    )[1].split("async", 1)[0]


def test_completed_scene_script_generation_refreshes_topic_library_state():
    creative = _source("static/js/common/creative.js")
    reconcile_method = creative.split("reconcileCreativeGenerationState(project)", 1)[1].split(
        "async syncCreativeJobProgress", 1
    )[0]

    assert "action === 'script'" in reconcile_method
    assert "await this.refreshCreativeTopicLibrary();" in reconcile_method


def test_scene_script_preview_and_download_use_delivery_formats():
    topics = _source("static/js/selection/topics.js")
    modal = _source("templates/common/workspace/selection_modals.html")

    assert "buildScriptPreview" in topics
    assert "format = 'txt'" in topics
    assert "generatedContentPreview.stage === 'script' ? 'txt' : 'md'" in modal


def test_topic_scene_script_preview_filters_history_by_version():
    topics = _source("static/js/selection/topics.js")
    modal = _source("templates/common/workspace/selection_modals.html")

    assert "generatedContentPreviewVersionOptions()" in topics
    assert "filteredGeneratedContentVersions()" in topics
    assert "filterGeneratedContentPreviewByVersion()" in topics
    assert 'x-model.number="generatedContentPreview.selectedVersion"' in modal
    assert "全部版本" in modal


def test_delivery_page_lists_existing_resources_without_delivery_actions():
    page = _source("templates/admin/deliveries.html")
    deliveries = _source("static/js/admin/deliveries.js")
    creative_api = _source("creative/api.py")

    assert "已生成成果" in page
    assert 'id="generated-resource-list"' in page
    assert "正式交付" not in page
    assert "草稿预览" not in deliveries
    assert "resource_status" in deliveries
    assert "/api/creative/generated-resources" in deliveries
    assert "generated-content/download" in deliveries
    assert "generated_resources=4" in page
    assert 'id="generated-resource-search"' in page
    assert 'id="generated-resource-stage-filter"' in page
    assert 'id="generated-resource-status-filter"' in page
    assert 'id="generated-resource-generated-after"' in page
    assert 'id="generated-resource-generated-before"' in page
    assert "生成时间" in page
    assert "applyFilters" in deliveries
    assert "generated_at" in deliveries
    assert "formatGeneratedAt" in deliveries
    assert '"generated_at": float(item.get("created_at") or 0)' in creative_api


def test_outline_page_exposes_beats_and_script_update_records_per_episode():
    page = _source("templates/writing/outlines.html")
    outline = _source("templates/common/workspace/project_outlines.html")
    creative = _source("static/js/common/creative.js")

    assert 'id="outlineEpisodeUpdatesModal"' in page
    assert "projectEpisodeUpdateRecords" in creative
    assert "hasProjectEpisodeUpdates" in creative
    assert "openOutlineEpisodeUpdates" in creative
    assert "projectEpisodeUpdateSummary" in outline
    assert "Beats 更新" in creative
    assert "查看 Beats 与剧本更新记录" in outline


def test_scene_script_workspace_routes_all_download_scopes_through_template_picker():
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "mergedScriptDownloadUrl()" not in scripts
    assert "generated-content/download" not in scripts
    assert "openEpisodeDownload('script', {forceAll: true})" in scripts
    assert "本集下载" in scripts
    assert "全集下载" in scripts


def test_scene_script_page_uses_the_writing_workspace_layout():
    page = _source("templates/writing/scripts.html")

    assert '{% extends "layouts/base.html" %}' in page
    assert 'src="/static/js/common/creative.js?v={{ asset_version }}"' in page
    assert 'src="/static/js/writing/scripts.js?v={{ asset_version }}"' in page


def test_locked_characters_offer_a_direct_link_to_episode_outlines():
    characters = _source("templates/common/workspace/project_characters.html")

    assert "/writing/projects/${creativeWorkspace.project.id}/outlines" in characters
    assert "进入全剧大纲" in characters


def test_creative_generation_claims_an_available_stage_task_automatically():
    creative = _source("static/js/common/creative.js")
    resolver = creative.split("async resolveCreativeWorkflowTask", 1)[1].split(
        "async resolveSelectionTopicByTitle", 1
    )[0]

    assert "item.status === 'pool'" in resolver
    assert "/api/workflow/tasks/${task.id}/claim" in resolver


def test_creative_generation_bootstraps_a_task_for_legacy_projects():
    creative = _source("static/js/common/creative.js")
    api = _source("creative/api.py")

    assert "/workflow-tasks/${resourceType}/ensure" in creative
    assert '"/projects/{project_id}/workflow-tasks/{resource_type}/ensure"' in api


def test_writing_workspace_refreshes_topics_only_when_the_page_provides_a_library():
    creative = _source("static/js/common/creative.js")

    assert "async refreshCreativeTopicLibrary()" in creative
    assert "typeof this.fetchTopicLibrary === 'function'" in creative
    assert "await this.refreshCreativeTopicLibrary();" in creative


def test_creative_progress_modal_blurs_the_close_button_before_it_is_hidden():
    tasks = _source("static/js/common/tasks.js")

    assert "document.getElementById('creativeGenerationProgressModal')?.addEventListener('hide.bs.modal'" in tasks
    assert "document.activeElement?.blur();" in tasks


@pytest.mark.parametrize(
    ("template_name", "redundant_label"),
    (
        ("templates/planning/brief.html", "人物关系"),
        ("templates/planning/characters.html", "返回 Brief"),
        ("templates/writing/outlines.html", "返回人物关系"),
        ("templates/writing/beats.html", "返回全剧大纲"),
        ("templates/writing/scripts.html", "返回 Beats"),
    ),
)
def test_project_stage_pages_use_project_list_instead_of_redundant_stage_links(
    template_name: str,
    redundant_label: str,
):
    page = _source(template_name)

    assert '{% include "common/workspace/project_list_back_link.html" %}' in page
    assert redundant_label not in page


def test_outline_rows_show_a_link_when_the_episode_script_exists():
    creative = _source("static/js/common/creative.js")
    outlines = _source("templates/common/workspace/project_outlines.html")

    assert "projectEpisodeScript(episodeNumber)" in creative
    assert "projectEpisodeScriptLabel(episodeNumber)" in creative
    assert "projectEpisodeScript(item.episode_number)" in outlines
    assert "projectEpisodeScriptLabel(item.episode_number)" in outlines
    assert "/scripts`" in outlines


def test_locked_outlines_hide_edit_actions():
    outlines = _source("templates/common/workspace/project_outlines.html")

    assert 'x-show="currentProjectOutlines().length && creativeResourceEditable(currentProjectOutlines()[0])"' in outlines
    assert 'x-show="creativeResourceEditable(item)" @click="saveProjectEpisode(item)"' in outlines


def test_outline_workspace_offers_merged_scene_script_download():
    outlines = _source("templates/common/workspace/project_outlines.html")
    creative = _source("static/js/common/creative.js")

    assert "剧本下载" in outlines
    assert "mergedScriptDownloadUrl()" in outlines
    assert "projectHasGeneratedScripts()" in outlines
    assert "/scripts/download`" in creative


def test_beats_workspace_uses_a_labeled_scene_script_action():
    beats = _source("templates/common/workspace/project_beats.html")

    assert "场次剧本" in beats
    assert "生成场次剧本" not in beats
    assert "bi-file-earmark-richtext" in beats


def test_episode_workspace_reads_the_episode_number_from_beats_and_scripts_urls():
    tasks = _source("static/js/common/tasks.js")

    assert r"/\/episodes\/(\d+)\/(?:beats|scripts)" in tasks


def test_scene_script_actions_stop_before_navigation_without_locked_beats():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "currentEpisodeHasLockedBeats()" in creative
    assert "openProjectScriptWorkspace()" in creative
    assert "请先生成并完成当前集 Beats，再生成场次剧本" in creative
    assert '@click="openProjectScriptWorkspace"' in beats


def test_scene_script_workspace_keeps_superseded_scripts_out_of_current_version():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "return this.projectEpisodeScript(episodeNumber);" in creative
    assert "scriptWorkspaceEmptyMessage()" in creative
    assert "当前仅有基于 Beats v${olderScriptBeatsVersion || '未知'} 的旧场次剧本；当前 Beats 为 v${currentBeats.version}" in creative
    assert 'x-text="scriptWorkspaceEmptyMessage()"' in scripts


def test_scene_script_workspace_can_preview_superseded_versions_read_only():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "projectEpisodeScriptVersions(episodeNumber)" in creative
    assert "projectEpisodeHistoricalScripts(episodeNumber)" in creative
    assert "projectScriptEpisodeLabel(episodeNumber)" in creative
    assert "selectedProjectScriptIsHistorical()" in creative
    assert "selectedProjectScriptEditable()" in creative
    assert 'x-model="creativeWorkspace.selectedScriptVersionId"' in scripts
    assert "历史版本仅供查看" in scripts
    assert "creativeGeneratedDownloadUrl('script', 'txt')" in scripts


def test_scene_script_workspace_calls_generation_a_regeneration_when_locked_beats_changed():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "projectScriptGenerationActionLabel()" in creative
    assert "重新生成场次剧本" in creative
    assert 'x-text="creativeActionRunning(\'script\') ? \'生成中\' : projectScriptGenerationActionLabel()"' in scripts


def test_scene_script_workspace_locks_standard_and_free_mode_versions():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert '@click="completeProjectScript"' in scripts
    assert 'x-show="isFreeCreativeProject()" @click="completeProjectScript"' not in scripts
    assert "await this.lockFreeCreativeResource(" in creative
    assert "'script', String(item.id), item.episode_number" in creative
    assert "await this.completeCreativeResource(" in creative
    assert "'script', item.id, this.creativeWorkspace.project.id" in creative
    assert "async completeProjectScriptsBatch()" in creative
    assert "/scripts/lock`" in creative
    assert '@click="completeProjectScriptsBatch"' in scripts
    assert "批量完成并锁定" in scripts
    assert 'x-show="!isFreeCreativeProject()" :href="mergedScriptDownloadUrl()"' not in scripts
    assert "openEpisodeDownload('script', {forceAll: true})" in scripts
    assert "selectedProjectScript().version" in scripts


def test_all_creative_module_links_are_clickable_before_upstream_locking():
    navigation = _source("templates/common/workspace/project_module_navigation.html")
    project_list = _source("templates/common/workspace/project_list.html")

    assert 'aria-label="全部创作模块"' in navigation
    assert "creativeModuleUrl('brief')" in navigation
    assert "creativeModuleUrl('characters')" in navigation
    assert "creativeModuleUrl('outline')" in navigation
    assert "creativeModuleUrl('beats')" in navigation
    assert "creativeModuleUrl('script')" in navigation
    assert '<template x-if="isFreeCreativeProject()">' not in navigation.split(
        'aria-label="全部创作模块"', 1
    )[0]
    assert "所有模块入口均可直接进入；生成与锁定仍按标准流程校验上游内容。" in navigation
    assert "五个创作模块入口始终可直接进入" in project_list


def test_sequential_workflow_selects_model_only_when_starting_generation():
    navigation = _source("templates/common/workspace/project_module_navigation.html")
    modal = _source("templates/common/workspace/free_creative_modals.html")
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")

    assert 'id="sequential-generation-model"' not in navigation
    assert 'id="sequentialGenerationModelModal"' in modal
    assert 'x-model="creativeWorkspace.sequentialGeneration.modelRecordId"' in modal
    assert 'x-model="creativeWorkspace.sequentialGeneration.reasoningEffort"' in modal
    assert 'type="radio" name="sequential-generation-reasoning-effort"' in modal
    assert "openSequentialGenerationModel" in creative
    assert "submitSequentialGeneration" in creative
    assert "llm_model_record_id: modelRecordId" in creative
    assert "sequentialGeneration:" in tasks


def test_standard_generate_opens_model_dialog_before_sending_job():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
global.window = {aiworkerModules: []};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const component = window.aiworkerModules[0]();
const calls = [];
Object.assign(component, {
  creativeWorkspace: {
    project: {id: 4, workflow_mode: 'sequential'},
    sequentialGeneration: {modelOptions: [], modelRecordId: '', reasoningEffort: '', loading: false, error: '', pending: null},
  },
  sequentialGenerationModelModal: {show: () => calls.push('show'), hide: () => calls.push('hide')},
  loadSequentialGenerationModels: async () => {
    calls.push('load');
    component.creativeWorkspace.sequentialGeneration.loading = false;
  },
});
(async () => {
  await component.startCreativeGeneration('/api/creative/projects/4/blueprints/generate', {count: 3}, 'blueprints');
  assert.deepEqual(calls, ['show', 'load']);
  assert.equal(component.creativeWorkspace.sequentialGeneration.pending.body.count, 3);
  component.creativeWorkspace.sequentialGeneration.modelRecordId = 'chosen-model';
  component.creativeWorkspace.sequentialGeneration.reasoningEffort = 'xhigh';
  component.startCreativeGeneration = async (...args) => calls.push(args);
  await component.submitSequentialGeneration();
  assert.equal(calls[2], 'hide');
  assert.equal(calls[3][1].llm_model_record_id, 'chosen-model');
  assert.equal(calls[3][1].reasoning_effort, 'xhigh');
  assert.equal(calls[3][3].modelConfirmed, true);
  assert.equal(component.creativeWorkspace.sequentialGeneration.pending, null);
  component.creativeWorkspace.sequentialGeneration.pending = {
    url: '/api/creative/projects/4/characters/generate', body: {}, action: 'characters', options: {},
  };
  component.creativeWorkspace.sequentialGeneration.modelRecordId = '';
  component.creativeWorkspace.sequentialGeneration.reasoningEffort = '';
  component.creativeWorkspace.sequentialGeneration.error = '模型列表暂时不可用';
  await component.submitSequentialGeneration();
  assert.equal(calls[5][1].llm_model_record_id, undefined);
  assert.equal(calls[5][1].reasoning_effort, undefined);
  assert.equal(calls[5][3].modelConfirmed, true);
  component.creativeWorkspace.sequentialGeneration.pending = {
    url: '/api/creative/projects/4/outlines/generate', body: {}, action: 'outlines', options: {},
  };
  component.creativeWorkspace.sequentialGeneration.defaultModelRecordId = 'default-model';
  component.creativeWorkspace.sequentialGeneration.reasoningEffort = 'high';
  await component.submitSequentialGeneration();
  assert.equal(calls[7][1].llm_model_record_id, 'default-model');
  assert.equal(calls[7][1].reasoning_effort, 'high');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr


def test_standard_generation_reuses_last_model_and_reasoning_selection():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const storage = new Map();
global.window = {
  aiworkerModules: [],
  localStorage: {
    getItem: key => storage.has(key) ? storage.get(key) : null,
    setItem: (key, value) => storage.set(key, value),
  },
};
global.aiworkerApi = {request: async () => ({
  ok: true,
  json: async () => ({
    default_model_record_id: 'model-a',
    items: [
      {id: 'model-a', reasoning_effort_options: [{value: 'high', label: '高'}]},
      {id: 'model-b', reasoning_effort_options: [{value: 'high', label: '高'}]},
    ],
  }),
})};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));

const createComponent = () => {
  const component = window.aiworkerModules[0]();
  Object.assign(component, {
    creativeWorkspace: {
      sequentialGeneration: {
        modelOptions: [], modelRecordId: '', defaultModelRecordId: '',
        reasoningEffortOptions: [], reasoningEffort: '', loading: false,
        error: '', pending: null,
      },
    },
    sequentialGenerationModelModal: {show: () => {}},
  });
  return component;
};

(async () => {
  const first = createComponent();
  await first.openSequentialGenerationModel('/generate', {}, 'script', {});
  first.creativeWorkspace.sequentialGeneration.modelRecordId = 'model-b';
  first.syncGenerationReasoningEfforts(first.creativeWorkspace.sequentialGeneration);
  first.creativeWorkspace.sequentialGeneration.reasoningEffort = 'high';
  first.persistSequentialGenerationPreference();

  const afterReload = createComponent();
  await afterReload.openSequentialGenerationModel('/generate', {}, 'script', {});
  assert.equal(afterReload.creativeWorkspace.sequentialGeneration.modelRecordId, 'model-b');
  assert.equal(afterReload.creativeWorkspace.sequentialGeneration.reasoningEffort, 'high');

  storage.set(
    afterReload.sequentialGenerationPreferenceStorageKey(),
    JSON.stringify({modelRecordId: 'retired-model', reasoningEffort: 'high'})
  );
  const withRetiredModel = createComponent();
  await withRetiredModel.openSequentialGenerationModel('/generate', {}, 'script', {});
  assert.equal(withRetiredModel.creativeWorkspace.sequentialGeneration.modelRecordId, '');
  assert.equal(withRetiredModel.creativeWorkspace.sequentialGeneration.reasoningEffort, '');
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_free_outline_keeps_field_level_ai_rewrite_and_head_conflict_guard():
    outlines = _source("static/js/writing/outlines.js")
    schema = _source("creative/schema.py")

    rewrite_guard = outlines.split("outlineFieldRewriteAllowed(item)", 1)[1].split(
        "openOutlineFieldRewrite", 1
    )[0]
    assert "if (this.isFreeCreativeProject?.()) return false" not in rewrite_guard
    assert "this.creativeHead?.('outline')?.resource_ref" in rewrite_guard
    assert "expected_head_revision" in outlines
    assert "expected_head_revision: int | None" in schema


def test_brief_page_mounts_only_one_production_export_modal():
    brief = _source("templates/planning/brief.html")

    assert brief.count('include "common/workspace/production_export_modal.html"') == 1


def test_free_locked_resources_offer_non_destructive_revision_actions():
    creative = _source("static/js/common/creative.js")
    api = _source("creative/api.py")
    blueprints = _source("templates/common/workspace/project_blueprints.html")
    characters = _source("templates/common/workspace/project_characters.html")
    outlines = _source("templates/common/workspace/project_outlines.html")
    beats = _source("templates/common/workspace/project_beats.html")
    scripts = _source("templates/common/workspace/project_scripts.html")
    revision_method = creative.split(
        "async createFreeResourceRevision(stage)", 1
    )[1].split("async openCreativeModeMigration()", 1)[0]

    assert "async createFreeResourceRevision(stage)" in creative
    assert "structuredClone(" not in revision_method
    assert revision_method.count("this.cloneCreativeJson(") == 6
    assert "this.freeStageHeadRef(stage" in creative
    assert "expected_head_revision" in creative
    assert "原锁定版本保持不变" in creative
    assert '"can_create_revision": can_write' in api
    assert "createProjectBlueprintVersion" in blueprints
    assert "freeStageHeadRef('brief')" in blueprints
    for stage, template in (
        ("characters", characters),
        ("outline", outlines),
        ("beats", beats),
        ("script", scripts),
    ):
        assert f"createFreeResourceRevision('{stage}')" in template
        assert f"freeStageCan('{stage}', 'can_create_revision')" in template
        assert "['locked', 'approved'].includes" in template


def test_free_brief_generation_runtime_sends_selected_count():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
global.window = {aiworkerModules: []};
global.aiworkerApi = {request: async () => ({ok: true, json: async () => ({})})};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const component = window.aiworkerModules[0]();
const requests = [];
Object.assign(component, {
  creativeWorkspace: {
    project: {id: 4, workflow_mode: 'free', resource_heads: []},
    directionCount: 3,
    freeGeneration: {},
    moduleCapabilities: {brief: {can_generate: true}},
  },
  startCreativeGeneration: async (url, payload) => requests.push({url, payload}),
});
(async () => {
  await component.generateProjectBlueprints();
  assert.equal(component.creativeWorkspace.freeGeneration.count, 3);
  component.creativeWorkspace.freeGeneration.modelRecordId = 'model-selected';
  component.creativeWorkspace.freeGeneration.reasoningEffort = 'max';
  await component.submitFreeCreativeGeneration();
  assert.equal(requests[0].url, '/api/creative/projects/4/free/briefs/generate');
  assert.equal(requests[0].payload.count, 3);
  assert.equal(requests[0].payload.llm_model_record_id, 'model-selected');
  assert.equal(requests[0].payload.reasoning_effort, 'max');
  component.creativeWorkspace.freeGeneration.count = 5;
  await component.submitFreeCreativeGeneration();
  assert.equal(requests[1].payload.count, 5);
  component.creativeWorkspace.project.blueprints = [1, 2, 3].map(id => ({
    id, candidate_group_id: `direction-${id}`, version: 1,
  }));
  assert.equal(component.latestProjectBlueprints().length, 3);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_free_generation_config_loading_has_a_bounded_wait():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const nativeSetTimeout = global.setTimeout;
global.window = {aiworkerModules: []};
global.setTimeout = (callback, _delay, ...args) => nativeSetTimeout(callback, 5, ...args);
global.aiworkerApi = {
  request: async (_url, options = {}) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }, {once: true});
  }),
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const component = window.aiworkerModules[0]();
Object.assign(component, {
  creativeWorkspace: {
    project: {id: 4, workflow_mode: 'free'},
    directionCount: 3,
    selectedEpisode: 1,
    freeGeneration: {},
    moduleCapabilities: {brief: {can_generate: true}},
  },
  freeCreativeGenerationModal: {show() {}, hide() {}},
});
(async () => {
  const verdict = await Promise.race([
    component.openFreeCreativeGeneration('brief').then(() => 'settled'),
    new Promise(resolve => nativeSetTimeout(() => resolve('still-loading'), 100)),
  ]);
  assert.equal(verdict, 'settled', 'generation config remained loading after its request deadline');
  assert.equal(component.creativeWorkspace.freeGeneration.loading, false);
  assert.match(component.creativeWorkspace.freeGeneration.error, /超时/);
})().catch(error => { console.error(error); process.exit(1); });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr


def test_free_revision_runtime_clones_alpine_proxy_content():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")

    script = r'''
const fs = require('fs');
const vm = require('vm');
global.window = {aiworkerModules: []};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'), {
  filename: process.argv[1],
});
const component = window.aiworkerModules[0]();
let requestCount = 0;
let notice = '';
global.aiworkerApi = {
  request: async () => {
    requestCount += 1;
    return {ok: true, json: async () => ({resource: {id: 2}})};
  },
};
Object.assign(component, {
  creativeWorkspace: {
    project: {
      id: 4,
      workflow_mode: 'free',
      characters: [{
        id: 1,
        batch_id: 'current-batch',
        status: 'locked',
        character_key: 'hero',
        content: new Proxy({name: '测试角色'}, {}),
      }],
      relationships: [],
      resource_heads: [{
        stage: 'characters',
        episode_number: 0,
        resource_ref: 'current-batch',
        revision: 2,
      }],
    },
    selectedEpisode: 1,
    moduleCapabilities: {characters: {can_create_revision: true}},
    error: '',
  },
  selectCreativeProject: async () => {},
  showCreativeNotice: (message) => { notice = message; },
});
(async () => {
  await component.createFreeResourceRevision('characters');
  if (requestCount !== 1) {
    throw new Error('revision did not issue exactly one request');
  }
  if (!notice.includes('原锁定版本保持不变')) {
    throw new Error('revision did not report success');
  }
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_saving_a_scene_script_reloads_its_new_version():
    creative = _source("static/js/common/creative.js")
    save_method = creative.split("async saveProjectEpisode(item)", 1)[1].split(
        "async saveCreativeItem", 1
    )[0]

    assert "await this.selectCreativeProject(this.creativeWorkspace.project.id);" in save_method


def test_script_series_generation_does_not_offer_resume_for_an_old_beats_version():
    creative = _source("static/js/common/creative.js")
    scripts = _source("templates/common/workspace/project_scripts.html")

    assert "isCompatibleProjectSeriesEpisode(stage, item)" in creative
    assert "refs.beats_id" in creative
    assert "全剧生成中" in scripts


def test_continuity_ledger_renders_known_status_codes_in_chinese():
    creative = _source("static/js/common/creative.js")
    beats = _source("templates/common/workspace/project_beats.html")

    assert "continuityStatusLabel(status)" in creative
    assert "blocked: '受阻'" in creative
    assert "active: '有效'" in creative
    assert "status: this.continuityStatusLabel(item.status)" in creative
    assert 'x-text="item.status"' in beats


def test_scene_script_lifecycle_badges_have_readable_backgrounds():
    styles = _source("static/css/workspaces.css")

    for status in (
        "pending-script",
        "script-in-progress",
        "script-generated",
        "script-exported",
    ):
        assert f".lifecycle-{status}" in styles


def test_topic_library_adoption_handles_non_json_server_errors():
    breakdowns = _source("static/js/selection/breakdowns.js")

    assert "async updateTopicLibrary(topic, status)" in breakdowns
    assert "const data = await res.json().catch(() => ({}));" in breakdowns


def test_download_click_notifies_when_a_douyin_full_drama_is_already_local():
    breakdowns = _source("static/js/selection/breakdowns.js")
    open_download = breakdowns.split("async openTopicDownload", 1)[1].split(
        "async selectDramaDownloadCandidate", 1
    )[0]

    assert "/api/hongguo/drama/download/status" in open_download
    assert "抖音全剧已下载" in open_download


def test_topic_library_can_capture_and_export_hongguo_reviews():
    topics = _source("templates/common/workspace/selection_topics.html")
    modal = _source("templates/common/workspace/selection_modals.html")
    creative = _source("static/js/common/creative.js")

    assert '@click="openTopicReviewCapture(topic)"' in topics
    assert 'id="dramaReviewCaptureModal"' in modal
    assert "dramaReviews.allReviews" in modal
    assert "CSV" in modal and "Excel" in modal
    assert "async openTopicReviewCapture(topic)" in creative
    assert "all_reviews: Boolean(state.allReviews)" in creative
    assert "format=${encodeURIComponent(state.format || 'csv')}" in creative


def test_my_task_tables_append_rows_instead_of_stringifying_them():
    script = _source("static/js/common/my_tasks.js")

    assert "submissionList.replaceChildren(...rows)" not in script
    assert "projectList.replaceChildren(...rows)" in script


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


def test_blueprint_editor_uses_a_dropdown_to_choose_the_current_direction():
    html = _workspace_source()

    assert 'x-model.number="creativeWorkspace.selectedBlueprintId"' in html
    assert 'x-for="item in latestProjectBlueprints()"' in html
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


def test_creative_generation_retry_uses_owning_business_action():
    script = _source("static/js/common/creative.js")
    modal = _source("templates/common/workspace/creative_progress_modal.html")

    assert "/generation-jobs/${this.creativeWorkspace.jobId}/retry" in script
    assert "operation-bound jobs" not in script
    assert "String(extra.workflow_mode || 'sequential') === 'sequential'" not in script
    assert "return status === 'failed';" in script
    assert "this.creativeWorkspace.retrying = true" in script
    assert "creativeJobRunning() && creativeWorkspace.jobProgress?.retry_attempt" in modal


def test_downloaded_topic_action_opens_episode_transcript_preview():
    html = _workspace_source()
    transcripts = _source("static/js/selection/breakdowns.js")

    assert 'x-show="topic.status === \'adopted\' && Number(topic.transcript_episode_count || 0) > 0"' in html
    assert '@click="openTopicTranscriptPreview(topic)"' in html
    assert 'title="预览字幕转写"' in html
    assert '@click="downloadTopicTranscripts(topic)"' in html
    assert 'title="下载字幕转写"' in html
    assert 'id="topicTranscriptPreviewModal"' in html
    assert "this.modal('topicTranscriptPreviewModal')" in html
    assert "/api/creative/topics/${topic.id}/transcripts" in html
    assert "/api/creative/topics/${topic.id}/transcripts/download" in transcripts
    assert "selectedTopicTranscript()" in html
    assert "上一集" in html
    assert "下一集" in html


def test_next_stage_generation_is_not_blocked_by_a_stale_previous_action():
    html = _workspace_source()

    assert "creativeActionDisabled(action, options = {})" in html
    assert "this.creativeWorkspace.activeAction === action && this.creativeJobRunning()" in html
    assert ':disabled="creativeActionDisabled(\'outlines\')"' in html
    assert ':disabled="creativeActionDisabled(\'characters\')"' in html
    assert ':disabled="creativeActionDisabled(\'blueprints\')"' in html


def test_episode_generation_controls_only_block_the_active_episode():
    creative = _source("static/js/common/creative.js")
    tasks = _source("static/js/common/tasks.js")

    assert "activeEpisode: 0" in tasks
    assert "this.creativeWorkspace.activeEpisode = ['beats', 'script'].includes(action)" in creative
    assert "['beats', 'script'].includes(action)" in creative
    assert "this.creativeWorkspace.activeEpisode" in creative


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
    assert "downloadBreakdown" in html
    assert "下载到本地" in html
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
    assert "'topic-library'" in html
    assert "createCreativeBreakdown(topic) {" in html
    assert "'creative'" in html
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
    assert "force_regenerate: Boolean(state.forceRegenerate)" in html
    assert 'id="breakdown-generation-model"' in html
    assert "llm_model_record_id" in html
    assert "startTranscriptBreakdown(topic, source, true)" in html
    assert "Boolean(topic?.locked_breakdown_id)" not in html
    assert "return this.startTranscriptBreakdown(\n                        topic,\n                        'topic-library'\n                    );" in html
    assert "return this.startTranscriptBreakdown(\n                        topic,\n                        'creative'\n                    );" in html
    assert ">更新拆解</button>" in html


def test_breakdown_task_log_opens_inside_the_generation_modal():
    html = _workspace_source()
    script = _source("static/js/selection/breakdowns.js")

    assert 'id="breakdownGenerationLogPane"' in html
    assert "breakdownGeneration.logs" in html
    assert "syncBreakdownGenerationLogs" in script
    assert "aiworkerApi.request(`/api/jobs/${jobId}/logs`)" in script
    assert "window.location.assign(`/tasks" not in script


def test_creative_generation_log_is_inline_and_can_reopen_from_project_progress():
    modal = _source("templates/common/workspace/creative_progress_modal.html")
    progress = _source("templates/common/workspace/project_progress.html")
    script = _source("static/js/common/creative.js")

    assert 'id="creativeGenerationLogPane"' in modal
    assert "creativeWorkspace.jobLogs" in modal
    assert '@click="openCreativeTaskLog"' not in modal
    assert '@click="openCreativeTaskLog"' in progress
    assert "syncCreativeJobLogs" in script
    assert "aiworkerApi.request(`/api/jobs/${jobId}/logs`)" in script
    assert "/tasks?job_id=" not in modal
    assert "/tasks?job_id=" not in progress


def test_standard_and_free_generation_show_and_fetch_logs_as_soon_as_job_starts():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const requests = [];
global.window = {
  aiworkerModules: [],
  aiworkerUi: {requestKey: async (_key, request) => request(undefined)},
};
global.aiworkerApi = {
  request: async (url) => {
    requests.push(url);
    if (url.endsWith('/beats/generate')) {
      const mode = url.includes('/free/') ? 'free' : 'sequential';
      const state = {
        job_id: `creative-beats-${mode}`, status: 'running',
        progress: {completed: 0, total: 1}, log_count: 1,
      };
      return {
        ok: true,
        json: async () => ({
          job: mode === 'free'
            ? {job: {job_id: state.job_id}, status: state.status, progress: state.progress, log_count: state.log_count}
            : state,
        }),
      };
    }
    if (url.startsWith('/api/jobs/creative-beats-') && url.endsWith('/logs')) {
      return {ok: true, json: async () => ({logs: ['[INFO] Beats generation started']})};
    }
    throw new Error(`Unexpected request: ${url}`);
  },
  isAbortError: () => false,
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));

const createComponent = workflowMode => {
  const component = window.aiworkerModules[0]();
  Object.assign(component, {
    creativeWorkspace: {
      project: {id: 7, workflow_mode: workflowMode}, selectedEpisode: 1,
      jobId: '', jobMessage: '', jobProgress: {}, jobStatus: '',
      jobLogs: [], jobLogCount: 0, showJobLogs: false,
    },
    creativeGenerationProgressModal: {show: () => {}},
    fetchJobs: async () => {},
    showCreativeNotice: () => {},
  });
  return component;
};

(async () => {
  for (const mode of ['sequential', 'free']) {
    const component = createComponent(mode);
    const segment = mode === 'free' ? '/free' : '';
    await component.startCreativeGeneration(
      `/api/creative/projects/7${segment}/beats/generate`, {}, 'beats',
      mode === 'sequential' ? {modelConfirmed: true} : {}
    );
    assert.equal(component.creativeWorkspace.showJobLogs, true, mode);
    assert.deepEqual(
      component.creativeWorkspace.jobLogs,
      ['[INFO] Beats generation started'],
      mode
    );
    assert.ok(requests.includes(`/api/jobs/creative-beats-${mode}/logs`), mode);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_creative_logs_keep_syncing_when_job_summary_is_temporarily_missing():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to execute the browser module")
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const requests = [];
global.window = {aiworkerModules: []};
global.aiworkerApi = {
  request: async url => {
    requests.push(url);
    return {
      ok: true,
      json: async () => ({logs: ['[INFO] 后台已经开始生成场次', '[INFO] 正在调用模型']}),
    };
  },
};
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const component = window.aiworkerModules[0]();
Object.assign(component, {
  jobs: [],
  creativeWorkspace: {
    project: {id: 20, episodes: []},
    jobId: 'creative-script-20', jobStatus: 'running', jobMessage: '',
    jobProgress: {}, jobLogs: [], jobLogCount: 2, showJobLogs: true,
    activeAction: 'script', activeEpisode: 1, activeSeriesGeneration: false,
  },
});

(async () => {
  await component.syncCreativeJobProgress();
  assert.ok(requests.includes('/api/jobs/creative-script-20/logs'));
  assert.deepEqual(component.creativeWorkspace.jobLogs, [
    '[INFO] 后台已经开始生成场次', '[INFO] 正在调用模型',
  ]);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    result = subprocess.run(
        [node, "-e", script, "static/js/common/creative.js"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_creative_generation_progress_shows_current_batch_and_slow_request_warning():
    modal = _source("templates/common/workspace/creative_progress_modal.html")
    script = _source("static/js/common/creative.js")

    assert "creativeBatchProgressText()" in modal
    assert "creativeBatchSlow()" in modal
    assert "模型响应较慢，任务仍在运行" in modal
    assert "系统会继续等待，已保存的检查点不会丢失" in modal
    assert "creativeBatchElapsedSeconds()" in script
    assert "creativeWorkspace.jobProgress?.batch_in_flight" in script
    assert "creativeWorkspace.jobProgress?.batch_started_at" in script
    assert ">= 720" in script
    assert "batch_start" in script and "batch_end" in script
    assert "reasoning_effort" in script


def test_drama_download_fallback_candidates_use_stable_unique_alpine_keys():
    modal = _source("templates/common/workspace/selection_modals.html")

    assert 'x-for="(candidate, index) in dramaDownload.candidates"' in modal
    assert 'candidate.aweme_id || \'candidate\'}:${index}`' in modal
    assert ':key="candidate.aweme_id"' not in modal


def test_download_modal_blurs_focused_controls_before_bootstrap_hides_it():
    script = _source("static/js/common/tasks.js")

    assert "dramaDownloadModal')?.addEventListener('hide.bs.modal'" in script
    assert "document.activeElement?.blur()" in script


def test_download_modal_plays_completed_video_artifacts_inline():
    modal = _source("templates/common/workspace/selection_modals.html")
    script = _source("static/js/selection/breakdowns.js")

    assert "isDramaVideoArtifact(item)" in modal
    assert '@click="openDramaDownloadVideo(item)"' in modal
    assert "dramaDownload.videoPreviewUrl" in modal
    assert "isDramaVideoArtifact(item)" in script
    assert "openDramaDownloadVideo(item)" in script
    assert "/api/file/preview?path=" in script


def test_download_artifacts_identify_their_episode_numbers():
    modal = _source("templates/common/workspace/selection_modals.html")
    script = _source("static/js/selection/breakdowns.js")

    assert "dramaDownloadArtifactEpisodeLabel(item)" in modal
    assert "dramaDownloadArtifactEpisodeLabel(item)" in script
    assert "episode_" in script
    assert "第 ${episode} 集" in script


def test_download_modal_collapses_episode_grid_when_some_episodes_are_already_downloaded():
    modal = _source("templates/common/workspace/selection_modals.html")
    script = _source("static/js/selection/breakdowns.js")

    assert "dramaDownload.episodesExpanded" in modal
    assert "继续下载" in modal
    assert "openDramaDownloadEpisodes" in modal
    assert "episodesExpanded" in script
    assert "openDramaDownloadEpisodes()" in script
    assert "isDramaEpisodeDownloaded(episode)" in modal
    assert "已下载" in modal
    assert "downloadedDramaEpisodeCount() > 0" in modal
    assert "downloadedDramaEpisodeCount() === 0" in modal
    assert "dramaDownload.finished && !dramaDownload.episodesExpanded" in modal
    assert "!dramaDownload.finished || dramaDownload.episodesExpanded" in modal
    assert "refreshDramaDownloadEpisodes" in script
    assert "if (this.downloadedDramaEpisodeCount() > 0) this.dramaDownload.finished = true;" not in script


def test_download_completed_summary_visibility_is_not_overridden_by_bootstrap_display_utility():
    modal = _source("templates/common/workspace/selection_modals.html")

    summary_condition = (
        'x-show="dramaDownload.finished && !dramaDownload.episodesExpanded '
        '&& downloadedDramaEpisodeCount() > 0"'
    )
    assert f'<div {summary_condition}>' in modal
    assert '<div class="border border-success p-3 d-flex align-items-center justify-content-between gap-3">' in modal
    unsafe_summary = (
        'class="border border-success p-3 d-flex align-items-center justify-content-between gap-3" '
        f'{summary_condition}'
    )
    assert unsafe_summary not in modal


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
    assert "todayRankMissingNoticeKeys" in html
    assert "默认展示 PostgreSQL 最新榜单快照" in html
    assert "默认展示本地最新榜单快照" not in html
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


def test_today_rank_update_is_admin_only_while_other_users_only_refresh_snapshot():
    script = _source("static/js/selection/ranks.js")
    template = WebGUI.templates.env.get_template(
        "common/workspace/selection_ranks.html"
    )
    admin_html = template.render(principal=SimpleNamespace(is_system_admin=True))
    user_html = template.render(principal=SimpleNamespace(is_system_admin=False))

    assert "async refreshTodayRank(canUpdateRank = false)" in script
    assert "if (!canUpdateRank)" in script
    assert "await this.fetchTodayRank(true)" in script
    assert "'/api/jobs'" in script
    assert "platform: 'dy'" in script
    assert "crawler_type: 'rank'" in script
    assert "pollTodayRankRefresh" in script
    assert "更新并抓取" in admin_html
    assert "hasTodayRankSnapshot()" in admin_html
    assert "今日已更新" in admin_html
    assert "refreshTodayRank(true)" in admin_html
    assert "refreshTodayRank(false)" not in admin_html
    assert "刷新榜单" in user_html
    assert "refreshTodayRank(false)" in user_html
    assert "更新并抓取" not in user_html
    assert "refreshTodayRank(true)" not in user_html


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
    assert "/api/hongguo/reviews/capture" in paths
    assert "/api/hongguo/reviews/jobs/{job_id}" in paths
    assert "/api/hongguo/reviews/jobs/{job_id}/download" in paths
    assert "/api/jobs/{job_id}/pause" in paths
    assert "/api/jobs/{job_id}/resume" in paths
    assert "/api/hongguo/drama/douyin-fallback/jobs" in paths
    assert "剧目检索下载" in html
    drama_page = _source("templates/common/workspace/selection_dramas.html")
    admin_page = _source("templates/admin/config.html")
    admin_actions = _source("static/js/admin/workspace.js")
    drama_actions = _source("static/js/selection/dramas.js")
    assert "红果剧目来源与抓包设置" in admin_page
    assert "capture_root" in admin_actions
    assert "capture_service_url" in admin_actions
    assert "wait_timeout_seconds" in admin_actions
    assert "检查来源与服务" in admin_page
    assert "检查中…" in admin_page
    assert 'aria-live="polite"' in admin_page
    assert "dramaAnalysis.interfaceTesting" in admin_page
    assert all(label in admin_page for label in ("正常", "部分异常", "异常"))
    assert all(style in admin_page for style in ("alert-success", "alert-warning", "alert-danger"))
    assert "checks" in admin_actions
    assert "配置中心地址" in admin_page
    assert "activationCode" in admin_actions
    assert "activation_code" in admin_actions
    assert "config_url" in admin_actions
    assert "/api/hongguo/drama/settings" in admin_actions
    assert "dramaReviewStatusLabel" in drama_actions
    assert "captureStatusLabel" in drama_actions
    assert "优先检索第三方剧目来源" in drama_page
    review_actions = _source("static/js/common/creative.js")
    assert "剧评检索下载" in drama_page
    assert "startDramaReviewCapture" in drama_page
    assert "dramaReviews.limit" in drama_page
    assert "/api/hongguo/reviews/capture" in review_actions
    assert "pollDramaReviewCapture" in review_actions
    assert "state.error = job.status === 'failed' ? String(job.error || '剧评采集失败') : '';" in review_actions
    assert "searchDrama" in html
    assert "notifyDramaJobCompletion" in html
    assert "完成后会弹窗提醒" in html
    assert "openTopicDownload" in html
    assert "/api/hongguo/drama/download/search" in html
    download_search = _source("static/js/selection/breakdowns.js").split(
        "async openTopicDownload", 1
    )[1].split("async selectDramaDownloadCandidate", 1)[0]
    assert "parseDramaDownloadResponse(res)" in download_search
    assert "async parseDramaDownloadResponse(response)" in html
    assert "await response.text()" in html
    assert "全剧下载" in html


def test_hongguo_provider_ui_requires_manual_source_selection():
    drama_page = _source("templates/common/workspace/selection_dramas.html")
    download_modal = _source("templates/common/workspace/selection_modals.html")
    drama_actions = _source("static/js/selection/dramas.js")
    breakdown_actions = _source("static/js/selection/breakdowns.js")

    assert "dramaAnalysis.providers" in drama_page
    assert "candidate.provider_id" in drama_page
    assert "data.providers || []" in drama_actions
    assert "provider_id: candidate.provider_id" in drama_actions
    assert "series_ref: candidate.series_ref" in drama_actions
    assert "data.providers || []" in breakdown_actions
    assert "this.dramaDownload.candidates.length === 1" not in breakdown_actions
    assert "dramaDownload.providers.length || dramaDownload.thirdPartyError || dramaDownload.captureError" in download_modal


def test_hongguo_settings_ui_preserves_third_party_and_capture_fields():
    page = _source("templates/admin/config.html")
    actions = _source("static/js/admin/workspace.js")
    html = _workspace_source()
    drama_page = _source("templates/common/workspace/selection_dramas.html")

    assert "第三方剧目来源" in page
    assert "抓包下载" in page
    assert 'id="hongguo-config-url"' in page
    assert 'id="hongguo-activation-code"' in page
    assert "config_url: this.dramaAnalysis.settings.config_url" in actions
    assert "...(activationCode ? {activation_code: activationCode} : {})" in actions
    assert "后台下载" in html
    assert "breakdownEditor.record?.status !== 'draft'" in html
    assert "episodesLoading" in html
    assert 'x-show="dramaDownload.searching"' in html
    assert "检索并选择剧集" in drama_page
    assert "onDramaTitleInput" not in drama_page
    assert "chooseDramaSuggestion" not in drama_page
    assert "topic-evidence-drawer" not in html
    assert "查看来源证据" not in html
    assert "topic.rank_type" in html
    assert "topic.description" in html
    assert "推荐分计算说明" in html
    assert "data-bs-toggle=\"tooltip\"" in html
    assert "置信度" not in html
    assert "可仿制/成本" not in html
    assert 'x-show="isDouyinDramaSearch()"' in drama_page
    assert 'x-text="dramaAnalysis.fallbackReason"' in drama_page
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


def test_drama_search_keeps_cross_source_candidates_distinct_and_review_input_independent():
    drama_page = _source("templates/common/workspace/selection_dramas.html")
    drama_script = _source("static/js/selection/dramas.js")

    assert ':key="`${candidate.provider_id}:${candidate.series_ref || candidate.book_id}`"' in drama_page
    assert "this.dramaReviews.title = title;" not in drama_script


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


@pytest.mark.asyncio
async def test_list_files_page_returns_a_bounded_cursor_page(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    for index in range(5):
        (data_root / f"item-{index}.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    first = await WebGUI.list_files_page(limit=2)
    second = await WebGUI.list_files_page(limit=2, cursor=first["next_cursor"])

    assert first["total"] == 5
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    assert len(second["items"]) == 2
    assert {item["path"] for item in first["items"]}.isdisjoint(
        item["path"] for item in second["items"]
    )


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


@pytest.mark.asyncio
async def test_batch_download_archives_multiple_selected_data_files(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    selected_dir = data_root / "jobs" / "demo"
    selected_dir.mkdir(parents=True)
    first = selected_dir / "first.json"
    second = selected_dir / "second.md"
    first.write_text('{"first": true}', encoding="utf-8")
    second.write_text("# second", encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    handler = getattr(WebGUI, "download_files_archive", None)
    assert handler is not None
    response = await handler(
        WebGUI.FileBatchDownloadRequest(
            paths=["data/jobs/demo/first.json", "data/jobs/demo/second.md"]
        )
    )

    assert response.media_type == "application/zip"
    with zipfile.ZipFile(response.path) as archive:
        assert archive.namelist() == ["jobs/demo/first.json", "jobs/demo/second.md"]
        assert archive.read("jobs/demo/first.json") == b'{"first": true}'
        assert archive.read("jobs/demo/second.md") == b"# second"
    await response.background()
    assert first.exists()
    assert second.exists()


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


def test_data_management_template_supports_multi_file_download():
    html = _workspace_source()

    assert "downloadSelectedFiles" in html
    assert "/api/files/operations" in html
    assert '@click="downloadSelectedFiles"' in html
    assert "bi bi-file-earmark-zip" in html


def test_batch_download_status_is_hidden_until_batch_download_is_started():
    template = _source("templates/common/workspace/admin_data.html")
    script = _source("static/js/admin/workspace.js")

    status_condition = "batchDownloadMode && filePage.operation?.operation === 'archive'"
    assert f'<div x-show="{status_condition}" x-cloak>' in template
    status_tag = template.split(status_condition, 1)[0].rsplit("<div", 1)[-1]
    assert "d-flex" not in status_tag
    assert "this.filePage.operation = {operation: 'archive'" in script
    assert "this.filePage.operation = null" in script


def test_data_management_file_checkboxes_follow_the_selected_path_array():
    html = _source("templates/common/workspace/admin_data.html")

    assert 'x-effect="$el.checked = isFileSelected(file.path)"' in html
    assert '@change="toggleFileSelection(file.path, $event.target.checked)"' in html


@pytest.mark.asyncio
async def test_data_operation_snapshot_uses_all_matching_selection_and_exclusions(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    for name in ("keep.json", "skip.json", "other.md"):
        (data_root / name).write_text(name, encoding="utf-8")

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    snapshot_path, total = WebGUI.create_data_operation_snapshot(
        WebGUI.FileOperationSelection(
            mode="all_matching",
            filter="json",
            excluded_paths=["data/skip.json"],
        ),
        "snapshot-test",
    )

    assert total == 1
    assert [json.loads(line) for line in snapshot_path.read_text(encoding="utf-8").splitlines()] == [
        "data/keep.json"
    ]


@pytest.mark.asyncio
async def test_data_operation_snapshot_rejects_invalid_explicit_path(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())

    with pytest.raises(HTTPException) as exc:
        WebGUI.create_data_operation_snapshot(
            WebGUI.FileOperationSelection(mode="explicit", paths=["data/missing.json"]),
            "invalid-snapshot",
        )
    assert exc.value.status_code == 404


def test_data_operation_cleanup_expires_old_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    operation_dir = tmp_path / ".runtime" / "data_operations"
    operation_dir.mkdir(parents=True)
    old_archive = operation_dir / "expired.zip"
    current_archive = operation_dir / "current.zip"
    old_archive.write_bytes(b"old")
    current_archive.write_bytes(b"current")
    expired_at = time.time() - 25 * 60 * 60
    os.utime(old_archive, (expired_at, expired_at))

    WebGUI.cleanup_data_operation_artifacts()

    assert not old_archive.exists()
    assert current_archive.exists()


@pytest.mark.asyncio
async def test_file_operation_creates_a_background_job_from_snapshot(tmp_path, monkeypatch):
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "selected.json").write_text("{}", encoding="utf-8")
    captured = {}
    principal = Principal(
        user_id="operator",
        username="operator",
        display_name="Operator",
        role_codes=(),
        permissions=frozenset({"data.media.preview"}),
        data_scope="all",
        session_id="session",
    )

    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI, "DATA_ROOT", data_root.resolve())
    monkeypatch.setattr(WebGUI.job_manager, "generate_job_id", lambda platform: "data-operation-test")
    monkeypatch.setattr(
        WebGUI.job_manager,
        "create_job",
        lambda job, **kwargs: captured.update(job=job, kwargs=kwargs) or SimpleNamespace(job_id=job.job_id),
    )
    monkeypatch.setattr(WebGUI, "_public_created_job", lambda _: {"job_id": "data-operation-test"})
    monkeypatch.setattr(WebGUI, "_record_job_audit", lambda *args, **kwargs: None)

    result = await WebGUI.create_file_operation(
        WebGUI.FileOperationRequest(
            operation="archive",
            selection=WebGUI.FileOperationSelection(mode="explicit", paths=["data/selected.json"]),
        ),
        principal,
    )

    assert result == {"job_id": "data-operation-test", "total": 1}
    assert captured["job"].platform == "data"
    assert captured["job"].crawler_type == "file_operation"
    assert json.loads(Path(captured["job"].extra["snapshot_path"]).read_text(encoding="utf-8")) == "data/selected.json"


@pytest.mark.asyncio
async def test_file_operation_requires_the_matching_permission():
    principal = Principal(
        user_id="operator",
        username="operator",
        display_name="Operator",
        role_codes=(),
        permissions=frozenset(),
        data_scope="all",
        session_id="session",
    )

    with pytest.raises(HTTPException) as exc:
        await WebGUI.create_file_operation(
            WebGUI.FileOperationRequest(
                operation="archive",
                selection=WebGUI.FileOperationSelection(mode="explicit", paths=["data/example.json"]),
            ),
            principal,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_file_operation_download_returns_its_completed_archive(tmp_path, monkeypatch):
    operation_dir = tmp_path / ".runtime" / "data_operations"
    operation_dir.mkdir(parents=True)
    archive = operation_dir / "data-operation-test.zip"
    archive.write_bytes(b"zip")
    principal = Principal(
        user_id="operator",
        username="operator",
        display_name="Operator",
        role_codes=(),
        permissions=frozenset({"data.media.preview", "job.read.own"}),
        data_scope="all",
        session_id="session",
    )
    job = {
        "job_id": "data-operation-test",
        "platform": "data",
        "crawler_type": "file_operation",
        "created_by_user_id": "operator",
        "project_id": None,
        "job": {
            "platform": "data",
            "crawler_type": "file_operation",
            "extra": {"operation": "archive", "output_path": str(archive)},
        },
    }
    monkeypatch.setattr(WebGUI, "WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(WebGUI.job_manager, "get_job", lambda _: job)

    response = await WebGUI.download_file_operation("data-operation-test", principal)

    assert response.path == str(archive)
    assert response.media_type == "application/zip"


def test_file_operation_creation_defers_permission_to_the_operation_handler():
    assert WebGUI._api_permission("POST", "/api/files/operations") is None


def test_data_management_uses_paged_file_requests_and_operation_jobs():
    html = _workspace_source()

    assert "/api/files/page" in html
    assert "/api/files/operations" in html
    assert "nextFilePage" in html
    assert "toggleAllMatchingFiles" in html


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


def test_workspace_shell_has_live_region_and_toast_container():
    base = _source("templates/layouts/base.html")
    assert 'id="ui-live-region"' in base and 'aria-live="polite"' in base
    assert 'id="ui-toast-region"' in base
    assert "ui-state.js" in base


def test_workspace_shell_has_accessible_mobile_drawer_controls():
    base = _source("templates/layouts/base.html")
    assert 'id="sidebar-toggle"' in base
    assert 'aria-controls="workspace-sidebar"' in base
    assert 'aria-expanded' in base
    assert 'id="sidebar-backdrop"' in base
    assert "Escape" in base


def test_workspace_shell_has_unified_context_bar_block():
    base = _source("templates/layouts/base.html")
    assert "{% block page_header %}{% endblock %}" in base


def test_api_layer_exposes_structured_errors_and_request_ids():
    api = _source("static/js/common/api.js")
    assert "class ApiError" in api
    assert "X-Request-ID" in api
    assert "isAbortError" in api
    assert "window.location.assign('/login')" in api
    assert "aiworker:permission-denied" in api
    assert "throw error" in api


def test_ui_state_module_exposes_the_shared_api():
    ui = _source("static/js/common/ui-state.js")
    for fn in ("requestKey", "cancelRequest", "readCache", "writeCache",
               "invalidateCache", "announce", "showToast", "setButtonLoading"):
        assert fn in ui


def test_jobs_polling_is_scoped_to_pages_that_consume_job_state():
    tasks = _source("static/js/common/tasks.js")
    assert "pageNeedsJobPolling()" in tasks
    assert "if (this.pageNeedsJobPolling())" in tasks


def test_initial_job_scan_does_not_block_page_data_loading():
    tasks = _source("static/js/common/tasks.js")
    api = _source("WebGUI.py")
    assert "initialJobsRequest = this.fetchJobs()" in tasks
    assert "if (this.pageNeedsJobPolling()) await this.fetchJobs();" not in tasks
    assert "await asyncio.to_thread(job_manager.list_jobs)" in api


def test_project_list_does_not_block_other_page_requests():
    api = _source("creative/api.py")
    assert "await asyncio.to_thread(repository.list_projects" in api


def test_workspace_initialization_runs_only_once():
    base = _source("templates/layouts/base.html")
    assert 'x-data="appData()"' in base
    assert 'x-init="init()"' not in base


def test_dashboard_no_longer_uses_limit_500():
    dashboard = _source("static/js/selection/dashboard.js")
    assert "limit=500" not in dashboard
    assert "limit=50&include_lifecycle=false" in dashboard
    assert "requestKey" in dashboard


def test_dashboard_uses_independent_requests():
    dashboard = _source("static/js/selection/dashboard.js")
    assert "overviewKey" in dashboard
    assert "analyticsKey" in dashboard
    assert "isAbortError" in dashboard
    assert "Promise.all" in dashboard


def test_dashboard_template_has_skeleton():
    html = _source("templates/selection/dashboard.html")
    assert "ui-skeleton" in html
    assert "aria-live" in html
    assert "page_header" in html


def test_my_tasks_uses_set_button_loading():
    script = _source("static/js/common/my_tasks.js")
    assert "setButtonLoading" in script
    assert "invalidateCache" in script
    assert "showToast" in script


def test_my_tasks_template_has_skeleton_and_aria_busy():
    html = _source("templates/common/workspace/my_tasks.html")
    assert "ui-skeleton" in html
    assert "aria-busy" in html
    assert 'id="my-tasks-skeleton"' in html


def test_selection_hits_uses_request_key_and_cache():
    topics = _source("static/js/selection/topics.js")
    assert "requestKey" in topics
    assert "readCache" in topics
    assert "writeCache" in topics


def test_breakdowns_invalidates_creative_topics_cache():
    breakdowns = _source("static/js/selection/breakdowns.js")
    assert "invalidateCache('creative:topics:')" in breakdowns


def test_selection_hits_template_has_skeleton():
    html = _source("templates/common/workspace/selection_hits.html")
    assert "ui-skeleton" in html
    assert "aria-live" in html


def test_selection_topics_template_has_skeleton():
    html = _source("templates/common/workspace/selection_topics.html")
    assert "ui-skeleton" in html


def test_creative_uses_show_toast_and_request_key():
    script = _source("static/js/common/creative.js")
    assert "showToast" in script
    assert "requestKey" in script


def test_creative_has_beforeunload_protection():
    script = _source("static/js/common/tasks.js")
    assert "beforeunload" in script
    assert "returnValue" in script


def test_planning_pages_use_page_header_block():
    for name in ("planning/brief.html", "planning/characters.html",
                 "writing/outlines.html", "writing/beats.html", "writing/scripts.html"):
        html = _source(f"templates/{name}")
        assert "{% block page_header %}" in html, f"{name} missing page_header block"


def test_editable_templates_show_lock_info():
    for name in ("project_blueprints.html", "project_characters.html"):
        html = _source(f"templates/common/workspace/{name}")
        assert "bi-lock" in html


def test_review_scripts_template_has_skeleton():
    html = _source("templates/review/scripts.html")
    assert "ui-skeleton" in html


def test_review_projects_template_has_skeleton():
    html = _source("templates/review/projects.html")
    assert "ui-skeleton" in html


def test_admin_users_uses_set_button_loading():
    script = _source("static/js/admin/users.js")
    assert "setButtonLoading" in script


def test_admin_users_template_has_skeleton():
    html = _source("templates/admin/users.html")
    assert "ui-skeleton" in html


def test_admin_users_presence_column_shows_the_full_last_online_time():
    html = _source("templates/admin/users.html")
    assert ".presence-column { width: 220px; min-width: 220px; white-space: nowrap; }" in html
    assert "text-overflow: ellipsis" not in html


def test_admin_audit_uses_set_button_loading():
    script = _source("static/js/admin/audit.js")
    assert "setButtonLoading" in script


def test_admin_audit_template_has_skeleton():
    html = _source("templates/admin/audit.html")
    assert "ui-skeleton" in html


def test_admin_pages_have_skeleton():
    for name in ("admin/roles.html", "admin/deliveries.html"):
        html = _source(f"templates/{name}")
        assert "ui-skeleton" in html, f"{name} missing skeleton"


def test_admin_jobs_uses_page_header_block():
    html = _source("templates/admin/jobs.html")
    assert "{% block page_header %}" in html


def test_workspace_css_has_admin_table_styles():
    css = _source("static/css/workspaces.css")
    assert "admin-table thead th" in css
    assert "ui-pagination" in css
