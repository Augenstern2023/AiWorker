# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_all_enabled_generation_workspaces_expose_the_shared_prompt_library():
    brief = _read("templates/planning/brief.html")
    characters = _read("templates/planning/characters.html")
    outlines = _read("templates/writing/outlines.html")
    beats = _read("templates/common/workspace/project_beats.html")
    scripts = _read("templates/common/workspace/project_scripts.html")

    assert "openPromptLibrary('brief')" in brief
    assert "openPromptLibrary('characters')" in characters
    assert "openPromptLibrary('outline')" in outlines
    assert "openPromptLibrary('beats')" in beats
    assert "openPromptLibrary('script')" in scripts
    assert 'include "common/workspace/prompt_library_modal.html"' in brief
    assert 'include "common/workspace/prompt_library_modal.html"' in characters
    assert 'include "common/workspace/prompt_library_modal.html"' in outlines
    assert 'include "common/workspace/prompt_library_modal.html"' in beats
    assert 'include "common/workspace/prompt_library_modal.html"' in scripts


def test_prompt_library_ui_keeps_contract_locked_and_supports_all_mvp_scopes():
    modal = _read("templates/common/workspace/prompt_library_modal.html")

    assert "输出字段、JSON 契约和运行变量由系统锁定" in modal
    assert "作为当前阶段默认模板" in modal
    assert "仅第 ${creativeWorkspace.selectedEpisode} 集默认使用" in modal
    assert "仅下次生成使用" in modal
    assert "另存到我的模板" in modal
    assert "另存为项目模板" in modal
    assert "保存为新版本" in modal


def test_prompt_library_ui_explains_recommendations_and_requires_import_confirmation():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")

    assert "七维匹配度排序" in modal
    assert "promptRecommendation(template).reasons" in modal
    assert "导入外部提示词" in modal
    assert "不会自动执行、保存、绑定或启用" in modal
    assert "确认并保存为模板" in modal
    assert "保存后仍不会设为默认模板" in modal
    assert "/prompt-library/recommendations?stage=" in script
    assert "/prompt-library/import-preview" in script
    assert "saveImportedPromptTemplate" in script


def test_prompt_library_ui_exposes_budgeted_candidate_only_ab_testing():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")

    assert "模板 A/B 对比" in modal
    assert "两组使用同一份冻结输入和同一模型" in modal
    assert "本次最高预算" in modal
    assert "预估调用与费用" in modal
    assert "输入 Token" in modal
    assert "输出 Token" in modal
    assert "启动两组候选生成" in modal
    assert "不会自动替换当前版本" in modal
    assert "当前版本不支持 Beats/场次剧本的全剧批量 A/B" in modal
    assert "/prompt-library/ab-tests/preflight" in script
    assert "/prompt-library/ab-tests`" in script
    assert "!state.abPreflight?.can_start" in script
    assert "人工效果评价" in modal
    assert "每项 1–5 分" in modal
    assert "不会自动修改项目默认模板或锁定候选" in modal
    assert "savePromptABEvaluation" in script
    assert "/evaluation`" in script
    assert "当前阶段效果汇总" in modal
    assert "只统计已完成且已人工确认的评价" in modal
    assert "iteration_suggestions" in modal
    assert "loadPromptABReport" in script
    assert "/ab-tests/report?stage=" in script
    assert "根据短板创建迭代草稿" in modal
    assert "受控模板迭代草稿" in modal
    assert "修改前" in modal and "修改后" in modal
    assert "确认发布只创建不可变的新模板版本" in modal
    assert "createPromptIterationDraft" in script
    assert "publishPromptIterationDraft" in script
    assert "/prompt-library/iteration-drafts" in script
    assert "导出 JSON" in modal
    assert "导出 Markdown" in modal
    assert "promptABExportUrl(experiment.id, 'json')" in modal
    assert "promptABExportUrl(experiment.id, 'md')" in modal
    assert "/export?format=${normalizedFormat}" in script
    assert "项目模板治理" in modal
    assert "先查看使用影响" in modal
    assert "批准当前版本" in script
    assert "标记弃用" in script
    assert "恢复并送审" in script
    assert "governPromptTemplate" in script
    assert "/prompt-library/governance" in script
    assert "/governance`" in script


def test_prompt_library_ui_exposes_real_usage_and_coverage_observability():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")
    state = _read("static/js/common/tasks.js")

    assert "模板可观测性" in modal
    assert "Token 只读取供应商 usage" in modal
    assert "任务创建时冻结的单价" in modal
    assert "模板归因覆盖" in modal
    assert "价格覆盖" in modal
    assert "样本不足，继续观察" in modal
    assert "loadPromptObservability" in script
    assert "/prompt-library/observability?" in script
    assert "observabilityReport" in state


def test_prompt_library_ui_exposes_moderated_cross_project_marketplace():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")
    state = _read("static/js/common/tasks.js")

    assert "模板市场" in modal
    assert "项目模板不能直接发布" in modal
    assert "完整模板冻结版本会公开展示" in modal
    assert "平台审核队列" in modal
    assert "接受许可并安装" in modal
    assert "已安装为项目独立副本" in modal
    assert "评分仅来自已安装用户；发布者不能自评" in modal
    assert "不会自动绑定模板或跟随来源更新" in modal
    assert "loadPromptMarketplace" in script
    assert "submitSelectedPromptToMarketplace" in script
    assert "reviewPromptMarketplaceSubmission" in script
    assert "installPromptMarketplaceListing" in script
    assert "ratePromptMarketplaceListing" in script
    assert "/prompt-marketplace?" in script
    assert "/marketplace-submissions`" in script
    assert "/install`" in script
    assert "/rating`" in script
    assert "marketplaceModerationQueue" in state
    assert "marketplaceRightsConfirmed" in state


def test_generation_payload_consumes_a_server_versioned_single_run_selection():
    script = _read("static/js/common/creative.js")

    assert "prompt_template_key: selection.templateKey" in script
    assert "prompt_template_revision: selection.revision" in script
    assert "prompt_override: {custom_requirements:" in script
    assert "promptStageForGenerationAction(action)" in script
    assert "runSelections[promptStage]" in script
    assert "this.promptGenerationPayload('brief'" in script
    assert "this.promptGenerationPayload('characters'" in script
    assert "this.promptGenerationPayload('outline'" in script
    assert "this.promptGenerationPayload('beats'" in script
    assert "this.promptGenerationPayload('script'" in script
    assert "this.promptGenerationPayload('outline', body)" in _read("static/js/writing/outlines.js")


def test_old_project_upgrade_is_explicit_and_uses_classic_compatibility():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")

    assert "这是旧项目，仍在使用原有硬编码提示词" in modal
    assert "enableCurrentProjectPromptLibrary" in modal
    assert "template_key: 'classic-compatible'" in script


def test_review_and_optimization_pages_send_single_run_template_selections():
    page = _read("templates/review/scripts.html")
    script = _read("static/js/review/scripts.js")

    assert 'id="script-review-prompt-template"' in page
    assert 'id="script-review-prompt-requirements"' in page
    assert "loadPromptSelector(" in script
    assert "'review', reviewPromptTemplate" in script
    assert "'optimization', promptSelect" in script
    assert "prompt_template_key: select.value" in script
    assert "prompt_template_revision: Number" in script
    assert "prompt_override: { custom_requirements:" in script


def test_writer_project_review_exposes_series_review_template_selection():
    script = _read("static/js/review/writer_project_review.js")

    assert "preview.prompt_module" in script
    assert "prompt_template_key: select.value" in script
    assert "prompt_template_revision:" in script
    assert "prompt_override: {custom_requirements:" in script
    assert "...selectedPrompt" in script
