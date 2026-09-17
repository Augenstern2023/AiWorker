# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_creative_results_show_frozen_template_provenance_in_every_stage():
    script = _read("static/js/common/creative.js")
    stages = {
        "project_blueprints.html": "selectedProjectBlueprint()",
        "project_characters.html": "currentProjectCharacters()[0]",
        "project_outlines.html": "item",
        "project_beats.html": "selectedProjectBeats()",
        "project_scripts.html": "selectedProjectScript()",
    }
    assert "creativeTemplateProvenance(item)" in script
    assert "meta.prompt_snapshot" in script
    for filename, resource in stages.items():
        html = _read(f"templates/common/workspace/{filename}")
        assert f"creativeTemplateProvenance({resource})" in html
        assert 'data-template-provenance' in html


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js unavailable")
def test_creative_template_provenance_does_not_guess_from_current_binding():
    cases = [
        [{"generation_meta": {"prompt_snapshot": {"template_key": "private-key", "title": "冻结标题", "revision": 3, "content_hash": "hash"}, "job_id": "job-1"}}, "模板生成：冻结标题 · 模板 v3"],
        [{"generation_meta": {"prompt_snapshot": {"template_key": "private-key", "title": "冻结标题", "revision": 3}, "manual_revision": True}}, "基于模板版本修订：冻结标题 · 模板 v3"],
        [{"direction_type": "original", "parent_version_id": 12, "generation_meta": {"prompt_snapshot": {"template_key": "private-key", "title": "冻结标题", "revision": 3}, "job_id": "old-job"}}, "基于模板版本修订：冻结标题 · 模板 v3"],
        [{"generation_meta": {"origin": "manual"}}, "手动创建/修订 · 未按生成模板生成"],
        [{"generation_meta": {"manual_revision": True, "job_id": "old-job"}}, "手动修订 · 原模板来源未记录"],
        [{"generation_meta": {"manual_revision": True, "prompt_source": "system"}}, "基于系统提示词版本修订 · 未使用模板库"],
        [{"generation_meta": {"job_id": "new-job", "prompt_source": "system"}}, "系统提示词生成 · 未使用模板库"],
        [{"generation_meta": {"job_id": "legacy-job"}}, "历史生成 · 模板记录缺失"],
        [{"generation_meta": {}}, "模板来源未记录"],
    ]
    program = (
        "global.window = {aiworkerModules: []}; "
        "require('./static/js/common/creative.js'); "
        "const component = window.aiworkerModules[0](); "
        f"const cases = {json.dumps(cases, ensure_ascii=False)}; "
        "for (const [item, expected] of cases) { "
        "const actual = component.creativeTemplateProvenance(item); "
        "if (actual !== expected) throw new Error(`${actual} != ${expected}`); "
        "}"
    )
    result = subprocess.run(
        ["node", "-e", program], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    assert result.returncode == 0, result.stderr


def test_new_generations_freeze_system_prompt_origin_without_template_snapshot():
    standard = _read("creative/phase4_worker.py")
    free = _read("creative/free_phase4_worker.py")
    api = _read("creative/api.py")
    assert '"prompt_source": "template" if isinstance(prompt_snapshot, dict) else "system"' in standard
    assert free.count('"prompt_source": "template" if isinstance(prompt_snapshot, dict) else "system"') == 2
    assert api.count('generation_meta={"origin": "manual"}') == 4


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
    for source in (brief, characters, outlines, beats, scripts):
        assert "选择生成模板" in source


def test_prompt_library_information_architecture_separates_daily_and_advanced_work():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    center = _read("templates/common/workspace/prompt_template_center.html")
    navigation = _read("templates/common/workspace/project_module_navigation.html")
    routes = _read("web/page_routes.py")
    router = _read("web/page_router.py")

    assert "prompt_library_surface|default('picker')" in modal
    assert "这里只决定本次生成使用什么模板" in modal
    assert "我的模板" in center
    assert "项目模板" in center
    assert "模板市场" in center
    assert "promptWorkspaceUrl('center')" in navigation
    assert "promptWorkspaceUrl('effects')" not in navigation
    assert "promptWorkspaceUrl('effects')" not in modal
    assert '"/projects/{project_id}/prompt-templates"' in routes
    assert '"/projects/{project_id}/prompt-effects"' in routes
    assert '"common/workspace/prompt_template_center.html"' in router
    assert "legacy_prompt_effects_page" in router


def test_prompt_library_ui_keeps_contract_locked_and_supports_all_mvp_scopes():
    modal = _read("templates/common/workspace/prompt_library_modal.html")

    assert "输出字段、JSON 契约和运行变量由系统锁定" in modal
    assert "作为当前阶段默认模板" in modal
    assert "仅第 ${creativeWorkspace.selectedEpisode} 集默认使用" in modal
    assert "仅下次生成使用" in modal
    assert "另存到我的模板" in modal
    assert "另存为项目模板" in modal
    assert "保存为新版本" in modal


def test_prompt_picker_clicks_full_card_and_shows_selected_template_details():
    modal = _read("templates/common/workspace/prompt_library_modal.html")

    assert '@click="selectPromptTemplate(template)"' in modal
    assert '@keydown.enter.prevent="selectPromptTemplate(template)"' in modal
    assert 'input class="visually-hidden" type="radio" name="prompt-template"' in modal
    assert "'bg-primary border-primary text-white shadow-sm'" in modal
    assert ">✓</span>" in modal
    assert 'class="form-check-input mt-1" type="radio" name="prompt-template"' not in modal
    assert 'data-prompt-template-details' in modal
    assert "所选模板详情" in modal
    assert "七维风格" in modal
    assert "共享作品 DNA" in modal
    assert "查看全部阶段规则、参数和示例" in modal
    assert "example.include_in_context ? '参与生成' : '仅作说明'" in modal
    assert "查看模板说明" not in modal


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


def test_prompt_library_ui_omits_ab_testing_and_pricing_workflows():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")
    effects = _read("templates/common/workspace/prompt_template_effects.html")

    for removed in (
        "对比测试",
        "实验记录",
        "A/B",
        "本次最高预算",
        "预计费用",
        "受控模板迭代草稿",
    ):
        assert removed not in modal
        assert removed not in effects
    for removed in (
        "/prompt-library/ab-tests",
        "/prompt-library/iteration-drafts",
        "openPromptAB",
        "promptABExportUrl",
    ):
        assert removed not in script
    assert "项目模板" in modal
    assert "看共享模板状态" in modal
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

    assert "效果数据" in modal
    assert "Token 只读取供应商 usage" in modal
    assert "模板归因覆盖" in modal
    assert "计算费用" not in modal
    assert "价格覆盖" not in modal
    assert "promptCostSummary" not in script
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


def test_old_project_upgrade_is_folded_into_first_real_template_action():
    modal = _read("templates/common/workspace/prompt_library_modal.html")
    script = _read("static/js/common/creative.js")
    api = _read("creative/api.py")

    assert "这是旧项目，当前默认仍使用原有提示词" in modal
    assert "仅下次生成使用”不会改变项目设置" in modal
    assert "首次“保存默认设置”会同时启用模板设置" in modal
    assert "只影响之后新生成的内容，不会修改已有内容" in modal
    assert "升级到模板系统" not in modal
    assert "enableCurrentProjectPromptLibrary" not in script
    assert "/prompt-system/enable" not in script
    assert 'x-show="creativeWorkspace.promptLibrary.settings?.enabled" @click="usePromptForNextGeneration"' not in modal
    assert 'x-show="creativeWorkspace.promptLibrary.settings?.enabled" @click="savePromptBinding"' not in modal
    assert "if (!state.settings?.enabled && !window.confirm(" in script
    assert "state.settings = data.settings || state.settings" in script
    assert '@router.post("/projects/{project_id}/prompt-system/enable")' in api


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
