window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                creativeTopicOptionLabel(topic) {
                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先完成拆解）`;
                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有完成版，最新草稿${version}）`;
                    return `${topic.title}（${this.creativeWorkflowStatusLabel(topic.latest_breakdown_status)}${version}）`;
                },

                async fetchCreativeProjects(selectId = null) {
                    this.creativeWorkspace.loading = true;
                    this.creativeWorkspace.error = '';
                    try {
                        const projectsUrl = this.page === 'writing-projects'
                            ? '/api/creative/projects?participating_only=true'
                            : '/api/creative/projects';
                        const [projectsResponse, topicsResponse] = await Promise.all([
                            aiworkerApi.request(projectsUrl),
                            aiworkerApi.request('/api/creative/topics?status=adopted&limit=500&include_lifecycle=false')
                        ]);
                        const projectsData = await projectsResponse.json();
                        const topicsData = await topicsResponse.json();
                        if (!projectsResponse.ok) throw new Error(projectsData.detail || '读取创作项目失败');
                        if (!topicsResponse.ok) throw new Error(topicsData.detail || '读取可用选题失败');
                        this.creativeWorkspace.projects = projectsData.items || [];
                        this.creativeWorkspace.eligibleTopics = topicsData.items || [];
                        const currentId = this.creativeWorkspace.project?.id;
                        const targetId = selectId
                            || (this.creativeWorkspace.projects.some(item => item.id === currentId) ? currentId : this.creativeWorkspace.projects[0]?.id);
                        if (targetId) await this.selectCreativeProject(targetId);
                        else this.creativeWorkspace.project = null;
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '读取创作项目失败';
                    } finally {
                        this.creativeWorkspace.loading = false;
                    }
                },

                async loadCreativeProjectBreakdowns(topicId) {
                    const response = await aiworkerApi.request(`/api/creative/topics/${topicId}/breakdowns?status=locked`);
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || '读取拆解版本失败');
                    this.creativeWorkspace.lockedBreakdowns = data.items || [];
                    const selected = Number(this.creativeWorkspace.create.breakdown_id);
                    if (!this.creativeWorkspace.lockedBreakdowns.some(item => Number(item.id) === selected)) {
                        this.creativeWorkspace.create.breakdown_id = this.creativeWorkspace.lockedBreakdowns[0]?.id || '';
                    }
                },

                async syncCreativeProjectSource() {
                    const topic = this.creativeWorkspace.eligibleTopics.find(item => Number(item.id) === Number(this.creativeWorkspace.create.topic_id));
                    if (!topic) {
                        this.creativeWorkspace.lockedBreakdowns = [];
                        this.creativeWorkspace.create.breakdown_id = '';
                        return;
                    }
                    try {
                        await this.loadCreativeProjectBreakdowns(topic.id);
                    } catch (e) {
                        this.creativeWorkspace.lockedBreakdowns = [];
                        this.creativeWorkspace.create.breakdown_id = '';
                        this.creativeWorkspace.error = e.message || '读取拆解版本失败';
                        return;
                    }
                    if (!this.creativeWorkspace.create.name) this.creativeWorkspace.create.name = `${topic.title}创作项目`;
                },

                async openCreativeProjectFromTopic(topic) {
                    if (topic.status !== 'adopted' || !topic.locked_breakdown_id) {
                        this.topicLibrary.error = '请先采纳选题并完成拆解版本';
                        return;
                    }
                    window.location.assign(`/planning/projects?topic_id=${encodeURIComponent(topic.id)}`);
                },

                async createCreativeProject() {
                    this.creativeWorkspace.error = '';
                    await this.syncCreativeProjectSource();
                    const source = this.creativeWorkspace.create;
                    if (!source.topic_id || !source.breakdown_id) return;
                    try {
                        if (!source.creation_request_id) {
                            source.creation_request_id = globalThis.crypto?.randomUUID?.()
                                || `project-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                        }
                        const res = await aiworkerApi.request('/api/creative/projects', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(source)
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '创建项目失败');
                        const isFree = data.workflow_mode === 'free';
                        this.creativeWorkspace.create = {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20, workflow_mode: 'sequential', creation_request_id: ''};
                        window.location.assign(`/planning/projects/${data.id}/brief${isFree ? '' : '?task_setup=1'}`);
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '创建项目失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    }
                },

                async deleteCreativeProject(project) {
                    if (!project?.id || !project.can_delete || this.creativeWorkspace.deletingProjectId) return;
                    const projectName = String(project.name || '未命名项目');
                    if (!window.confirm(`确定永久删除项目“${projectName}”吗？关联的 Brief、人物关系、大纲、Beats、场次剧本和审核数据也会删除，且无法恢复。`)) return;
                    this.creativeWorkspace.deletingProjectId = Number(project.id);
                    this.creativeWorkspace.error = '';
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}`, {
                            method: 'DELETE'
                        });
                        const data = await response.json().catch(() => ({}));
                        if (!response.ok) throw new Error(data.detail || data.message || '删除项目失败');
                        await this.fetchCreativeProjects();
                        this.showCreativeNotice(`项目“${projectName}”已删除`, 'success');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '删除项目失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    } finally {
                        this.creativeWorkspace.deletingProjectId = null;
                    }
                },

                setOriginalBoundaries(value) {
                    this.creativeWorkspace.originalCreate.content_boundaries = String(value || '')
                        .split(/[,，\n]/).map(item => item.trim()).filter(Boolean);
                },

                toggleOriginalReference(resourceType, resourceId, checked) {
                    const refs = this.creativeWorkspace.originalCreate.reference_refs;
                    const key = `${resourceType}:${Number(resourceId)}`;
                    const filtered = refs.filter(item => `${item.resource_type}:${Number(item.resource_id)}` !== key);
                    if (checked) filtered.push({resource_type: resourceType, resource_id: Number(resourceId)});
                    this.creativeWorkspace.originalCreate.reference_refs = filtered.slice(0, 10);
                },

                async createOriginalProject() {
                    const source = this.creativeWorkspace.originalCreate;
                    if (!String(source.seed_text || '').trim() || source.saving) return;
                    source.saving = true;
                    source.error = '';
                    try {
                        if (!source.creation_request_id) {
                            source.creation_request_id = globalThis.crypto?.randomUUID?.()
                                || `original-${Date.now()}-${Math.random().toString(16).slice(2)}`;
                        }
                        const payload = {
                            seed_text: String(source.seed_text).trim(), name: String(source.name || '').trim(),
                            genre: String(source.genre || '').trim(), target_platform: String(source.target_platform || ''),
                            target_audience: String(source.target_audience || '').trim(),
                            target_episode_count: Number(source.target_episode_count || 20),
                            episode_duration_seconds: Number(source.episode_duration_seconds || 120),
                            cost_level: String(source.cost_level || ''),
                            content_boundaries: [...source.content_boundaries],
                            reference_refs: source.reference_refs.map(item => ({resource_type: item.resource_type, resource_id: Number(item.resource_id)})),
                            workflow_mode: source.workflow_mode || 'sequential',
                            creation_request_id: source.creation_request_id,
                        };
                        const response = await aiworkerApi.request('/api/creative/projects/original', {
                            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)
                        });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建原创项目失败');
                        window.location.assign(
                            `/planning/projects/${data.id}/brief${data.workflow_mode === 'free' ? '' : '?generate_original=1'}`
                        );
                    } catch (e) {
                        source.error = e.message || '创建原创项目失败';
                    } finally {
                        source.saving = false;
                    }
                },

                openCreativeProject(project) {
                    if (!project?.id) return;
                    window.location.assign(this.creativeProjectStageUrl(project));
                },

                async openCreativeTaskSetup() {
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId) return;
                    await this.openProjectTaskWindow(projectId, 'brief');
                },

                async selectCreativeProject(projectId) {
                    try {
                        const res = await aiworkerApi.request(`/api/creative/projects/${projectId}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取项目失败');
                        this.creativeWorkspace.project = data;
                        this.creativeWorkspace.selectedCharacterId = null;
                        this.creativeWorkspace.characterEditorDraft = {};
                        this.creativeWorkspace.selectedRelationshipId = null;
                        this.creativeWorkspace.relationshipEditorDraft = {};
                        this.creativeWorkspace.selectedScriptVersionId = '';
                        await this.loadCreativeModuleCapabilities(data.id);
                        this.creativeWorkspace.selectedSeedId = data.seed?.id || null;
                        this.creativeWorkspace.preservedBlueprintIds = [];
                        this.reconcileCreativeGenerationState(data);
                        const candidates = this.latestProjectBlueprints();
                        const freeBriefHead = this.creativeHead('brief');
                        if (!candidates.some(item => item.id === this.creativeWorkspace.selectedBlueprintId)) {
                            this.creativeWorkspace.selectedBlueprintId = data.workflow_mode === 'free'
                                ? Number(freeBriefHead?.resource_ref || candidates[0]?.id || 0) || null
                                : data.locked_brief_id || candidates[0]?.id || null;
                        }
                        this.creativeWorkspace.selectedCharacterBriefId = data.locked_brief_id || null;
                        this.creativeWorkspace.selectedOutlineBriefId = data.locked_brief_id || null;
                        this.creativeWorkspace.selectedOutlineCharacterBatchId = data.character_batch_id || null;
                        const selectedEpisode = Number(this.creativeWorkspace.selectedEpisode);
                        this.creativeWorkspace.selectedEpisode = selectedEpisode === 0 ? 0 : Math.min(
                            Math.max(1, Number(selectedEpisode || 1)),
                            Number(data.target_episode_count || 1)
                        );
                        if (this.page === 'writing-scripts') {
                            await this.loadVideoParameterRecommendation();
                        }
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '读取项目失败';
                    }
                },

                async refreshCreativeTopicLibrary() {
                    if (typeof this.fetchTopicLibrary === 'function') {
                        await this.fetchTopicLibrary();
                    }
                },

                creativeProjectStatusLabel(project) {
                    const status = typeof project === 'string' ? project : project?.status;
                    if (status === 'free_active') {
                        return {
                            brief: 'Brief 已锁定',
                            characters: '人物已锁定',
                            outline: '大纲已锁定',
                            beats: 'Beats 制作中',
                            script: '场次剧本制作中',
                        }[project?.latest_locked_stage] || '自由创作中';
                    }
                    return {
                        created: '待生成方向', direction_review: '方向评审', brief_locked: 'Brief 已锁定',
                        characters_draft: '人物草稿', characters_locked: '人物已锁定', outline_draft: '大纲草稿',
                        outline_locked: '大纲已锁定', beats_in_progress: 'Beats 制作中',
                        script_in_progress: '场次剧本制作中', scripts_in_progress: '场次剧本制作中',
                        completed: '已完成', archived: '已归档'
                    }[status] || status || '-';
                },

                creativeProjectStageUrl(project) {
                    const projectId = encodeURIComponent(String(project?.id || ''));
                    if (!projectId) return '#';
                    const sequentialStage = {
                        created: 'brief',
                        direction_review: 'brief',
                        brief_locked: 'characters',
                        brief_approved: 'characters',
                        characters_draft: 'characters',
                        characters_locked: 'outline',
                        characters_approved: 'outline',
                        outline_draft: 'outline',
                        outline_locked: 'beats',
                        outline_approved: 'beats',
                        beats_in_progress: 'beats',
                        script_in_progress: 'script',
                        scripts_in_progress: 'script',
                        completed: 'script',
                    }[project?.status] || 'brief';
                    const allowedStages = ['brief', 'characters', 'outline', 'beats', 'script'];
                    const stage = project?.workflow_mode === 'free'
                        && allowedStages.includes(project?.latest_stage)
                        ? project.latest_stage
                        : sequentialStage;
                    const latestEpisode = Math.min(
                        Math.max(1, Number(project?.latest_episode_number || 1)),
                        Math.max(1, Number(project?.target_episode_count || 1))
                    );
                    const allEpisodes = project?.workflow_mode !== 'free';
                    return {
                        brief: `/planning/projects/${projectId}/brief`,
                        characters: `/planning/projects/${projectId}/characters`,
                        outline: `/writing/projects/${projectId}/outlines?episode=all`,
                        beats: `/writing/projects/${projectId}/episodes/${latestEpisode}/beats${allEpisodes ? '?episode=all' : ''}`,
                        script: `/writing/projects/${projectId}/episodes/${latestEpisode}/scripts${allEpisodes ? '?episode=all' : ''}`,
                    }[stage];
                },

                creativeProjectModeLabel(project) {
                    return project?.creation_mode === 'original' ? '原创' : '仿写';
                },

                creativeProjectWorkflowModeLabel(project) {
                    return project?.workflow_mode === 'free' ? '自由创作' : '标准流程';
                },

                isFreeCreativeProject() {
                    return this.creativeWorkspace.project?.workflow_mode === 'free';
                },

                sequentialGenerationPreferenceStorageKey() {
                    return 'aiworker.creative.sequential-generation-preference';
                },

                loadSequentialGenerationPreference() {
                    try {
                        const raw = window.localStorage?.getItem(
                            this.sequentialGenerationPreferenceStorageKey()
                        );
                        const value = raw ? JSON.parse(raw) : {};
                        if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
                        return {
                            modelRecordId: String(value.modelRecordId || ''),
                            reasoningEffort: String(value.reasoningEffort || ''),
                        };
                    } catch (_) {
                        return {};
                    }
                },

                persistSequentialGenerationPreference() {
                    const state = this.creativeWorkspace.sequentialGeneration;
                    try {
                        window.localStorage?.setItem(
                            this.sequentialGenerationPreferenceStorageKey(),
                            JSON.stringify({
                                modelRecordId: String(state.modelRecordId || ''),
                                reasoningEffort: String(state.reasoningEffort || ''),
                            })
                        );
                    } catch (_) {
                        // Storage can be unavailable in private or restricted browser contexts.
                    }
                },

                syncGenerationReasoningEfforts(state, defaultModelRecordId = null) {
                    if (defaultModelRecordId !== null) {
                        state.defaultModelRecordId = String(defaultModelRecordId || '');
                    }
                    const selectedId = String(
                        state.modelRecordId || state.defaultModelRecordId || ''
                    );
                    const selected = (state.modelOptions || []).find(
                        item => String(item.id) === selectedId
                    );
                    state.reasoningEffortOptions = Array.isArray(selected?.reasoning_effort_options)
                        ? selected.reasoning_effort_options : [];
                    const allowed = new Set(
                        state.reasoningEffortOptions.map(item => String(item.value))
                    );
                    if (!allowed.has(String(state.reasoningEffort || ''))) {
                        state.reasoningEffort = '';
                    }
                },

                selectedGenerationModelRecordId(state) {
                    return String(
                        state.modelRecordId || state.defaultModelRecordId || ''
                    ).trim();
                },

                async loadSequentialGenerationModels() {
                    const state = this.creativeWorkspace.sequentialGeneration;
                    state.loading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request('/api/llm/available-models');
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取可用模型失败');
                        state.modelOptions = Array.isArray(data.items) ? data.items : [];
                        const availableModelIds = new Set(
                            state.modelOptions.map(item => String(item.id))
                        );
                        if (state.modelRecordId && !availableModelIds.has(String(state.modelRecordId))) {
                            state.modelRecordId = '';
                            state.reasoningEffort = '';
                        }
                        this.syncGenerationReasoningEfforts(
                            state, data.default_model_record_id
                        );
                        this.persistSequentialGenerationPreference();
                    } catch (error) {
                        state.modelRecordId = '';
                        state.defaultModelRecordId = '';
                        state.reasoningEffort = '';
                        state.reasoningEffortOptions = [];
                        state.error = `${error.message || '读取可用模型失败'}；可继续使用全局默认模型`;
                    } finally {
                        state.loading = false;
                    }
                },

                async openSequentialGenerationModel(url, body, action, options) {
                    const state = this.creativeWorkspace.sequentialGeneration;
                    const preference = this.loadSequentialGenerationPreference();
                    Object.assign(state, {
                        pending: {url, body, action, options},
                        modelRecordId: preference.modelRecordId || '',
                        defaultModelRecordId: '', modelOptions: [],
                        reasoningEffort: preference.reasoningEffort || '',
                        reasoningEffortOptions: [], error: '', loading: true,
                    });
                    this.sequentialGenerationModelModal?.show();
                    await this.loadSequentialGenerationModels();
                },

                async submitSequentialGeneration() {
                    const state = this.creativeWorkspace.sequentialGeneration;
                    const pending = state.pending;
                    if (!pending || state.loading) return;
                    const reasoningEffort = String(state.reasoningEffort || '').trim();
                    const modelRecordId = reasoningEffort
                        ? this.selectedGenerationModelRecordId(state)
                        : String(state.modelRecordId || '').trim();
                    if (reasoningEffort && !modelRecordId) {
                        state.error = '选择推理档位前必须先选择一个可用模型';
                        return;
                    }
                    this.persistSequentialGenerationPreference();
                    state.pending = null;
                    this.sequentialGenerationModelModal?.hide();
                    const body = modelRecordId
                        ? {...pending.body, llm_model_record_id: modelRecordId} : {...pending.body};
                    if (reasoningEffort) body.reasoning_effort = reasoningEffort;
                    return this.startCreativeGeneration(
                        pending.url, body, pending.action, {...pending.options, modelConfirmed: true}
                    );
                },

                creativeHead(stage, episodeNumber = 0) {
                    return (this.creativeWorkspace.project?.resource_heads || []).find(item =>
                        item.stage === stage
                        && Number(item.episode_number || 0) === Number(episodeNumber || 0)
                    ) || null;
                },

                creativeHeadRevision(stage, episodeNumber = 0) {
                    return Number(this.creativeHead(stage, episodeNumber)?.revision || 0);
                },

                creativeGenerationModelLabel(meta = {}) {
                    const provider = String(
                        meta.llm_provider_name || meta.provider || ''
                    ).trim();
                    const model = String(
                        meta.llm_model_display_name || meta.llm_model_id || meta.model || ''
                    ).trim();
                    if (provider && model && provider !== model) return `${provider} / ${model}`;
                    return model || provider;
                },

                creativeTemplateProvenance(item) {
                    if (!item) return '';
                    const meta = item.generation_meta || {};
                    // The resource's frozen snapshot is authoritative; current project bindings can change.
                    const snapshot = meta.prompt_snapshot && typeof meta.prompt_snapshot === 'object'
                        ? meta.prompt_snapshot : {};
                    const key = String(snapshot.template_key || meta.prompt_template_key || '').trim();
                    const revision = Number(snapshot.revision || meta.prompt_template_revision || 0);
                    if (key && revision > 0) {
                        const title = String(snapshot.title || key).trim();
                        const prefix = meta.manual_revision || (item.direction_type && item.parent_version_id)
                            ? '基于模板版本修订' : '模板生成';
                        return `${prefix}：${title} · 模板 v${revision}`;
                    }
                    if (meta.manual_revision || (item.direction_type && item.parent_version_id && meta.job_id)) {
                        return meta.prompt_source === 'system'
                            ? '基于系统提示词版本修订 · 未使用模板库'
                            : '手动修订 · 原模板来源未记录';
                    }
                    if (meta.origin === 'manual') return '手动创建/修订 · 未按生成模板生成';
                    if (meta.prompt_source === 'system') return '系统提示词生成 · 未使用模板库';
                    if (meta.job_id || meta.generation_context_id || meta.source === 'outline_field_rewrite') {
                        return '历史生成 · 模板记录缺失';
                    }
                    return '模板来源未记录';
                },

                freeStageVersionOptions(stage, episodeNumber = 0) {
                    const project = this.creativeWorkspace.project;
                    if (!project || project.workflow_mode !== 'free') return [];
                    let items = [];
                    if (stage === 'brief') {
                        items = (project.blueprints || []).map(item => ({
                            resource_ref: String(item.id), version: item.version,
                            status: item.status, label: item.content?.title || '未命名 Brief',
                            model_label: this.creativeGenerationModelLabel(item.generation_meta)
                        }));
                    } else if (stage === 'characters') {
                        const batches = new Map();
                        (project.characters || []).forEach(item => {
                            const ref = String(item.batch_id || '');
                            if (ref && !batches.has(ref)) batches.set(ref, {
                                resource_ref: ref, version: item.version, status: item.status,
                                label: (project.characters || []).filter(value => value.batch_id === item.batch_id)
                                    .map(value => value.content?.name).filter(Boolean).slice(0, 3).join('、') || '人物版本',
                                model_label: this.creativeGenerationModelLabel(item.generation_meta)
                            });
                        });
                        items = [...batches.values()];
                    } else if (stage === 'outline') {
                        const batches = new Map();
                        (project.episodes || []).filter(item => item.stage === 'outline').forEach(item => {
                            const ref = String(item.batch_id || '');
                            if (ref && !batches.has(ref)) batches.set(ref, {
                                resource_ref: ref, version: item.version, status: item.status, label: '全剧大纲',
                                model_label: this.creativeGenerationModelLabel(item.generation_meta)
                            });
                        });
                        items = [...batches.values()];
                    } else {
                        items = (project.episodes || []).filter(item =>
                            item.stage === stage && Number(item.episode_number) === Number(episodeNumber)
                        ).map(item => ({
                            resource_ref: String(item.id), version: item.version,
                            status: item.status, label: `第 ${item.episode_number} 集`,
                            model_label: this.creativeGenerationModelLabel(item.generation_meta)
                        }));
                    }
                    return items.sort((left, right) => Number(right.version || 0) - Number(left.version || 0));
                },

                freeStageHeadRef(stage, episodeNumber = 0) {
                    return String(this.creativeHead(stage, episodeNumber)?.resource_ref || '');
                },

                async setFreeCreativeHead(stage, resourceRef, episodeNumber = 0) {
                    if (!this.isFreeCreativeProject() || !resourceRef) return;
                    const projectId = this.creativeWorkspace.project.id;
                    const suffix = ['beats', 'script'].includes(stage)
                        ? `?episode_number=${Number(episodeNumber)}` : '';
                    const plural = {brief: 'briefs', characters: 'characters', outline: 'outlines', beats: 'beats', script: 'scripts'}[stage];
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${projectId}/free/${plural}/head${suffix}`, {
                            method: 'PUT', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                resource_ref: String(resourceRef),
                                expected_head_revision: this.creativeHeadRevision(stage, episodeNumber)
                            })
                        });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '切换当前版本失败');
                        await this.selectCreativeProject(projectId);
                        if (stage === 'brief') this.creativeWorkspace.selectedBlueprintId = Number(resourceRef);
                        this.showCreativeNotice('已切换当前模块版本');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '切换当前版本失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                        await this.selectCreativeProject(projectId);
                    }
                },

                creativeModuleUrl(stage, episodeNumber = null) {
                    const projectId = this.creativeWorkspace.project?.id || '';
                    const requestedEpisode = episodeNumber === null
                        ? Number(this.creativeWorkspace.selectedEpisode)
                        : Number(episodeNumber);
                    const allEpisodes = requestedEpisode === 0;
                    const episode = Math.min(
                        Math.max(1, Number(requestedEpisode || 1)),
                        Number(this.creativeWorkspace.project?.target_episode_count || 1)
                    );
                    return {
                        brief: `/planning/projects/${projectId}/brief`,
                        characters: `/planning/projects/${projectId}/characters`,
                        outline: `/writing/projects/${projectId}/outlines?episode=${allEpisodes ? 'all' : episode}`,
                        beats: `/writing/projects/${projectId}/episodes/${episode}/beats${allEpisodes ? '?episode=all' : ''}`,
                        script: `/writing/projects/${projectId}/episodes/${episode}/scripts${allEpisodes ? '?episode=all' : ''}`,
                    }[stage] || '#';
                },

                beatsBackUrl() {
                    return Number(this.creativeWorkspace?.selectedEpisode || 0) > 0
                        ? this.creativeModuleUrl('beats', 0)
                        : this.creativeModuleUrl('outline', 0);
                },

                beatsBackLabel() {
                    return Number(this.creativeWorkspace?.selectedEpisode || 0) > 0
                        ? '返回全集 Beats'
                        : '返回分集大纲';
                },

                openCreativeEpisodeModule(stage) {
                    window.location.assign(this.creativeModuleUrl(stage, this.creativeWorkspace.selectedEpisode));
                },

                async loadCreativeModuleCapabilities(projectId) {
                    this.creativeWorkspace.moduleCapabilities = {};
                    if (!projectId) return false;
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${projectId}/module-capabilities`);
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取模块权限失败');
                        this.creativeWorkspace.moduleCapabilities = data.capabilities || {};
                        return true;
                    } catch (_error) {
                        // The project remains viewable when the optional capability request
                        // fails; free-mode write controls stay disabled until the next reload.
                        return false;
                    }
                },

                freeStageCan(stage, action) {
                    if (!this.isFreeCreativeProject()) return true;
                    const capability = this.creativeWorkspace.moduleCapabilities?.[stage] || {};
                    return Boolean(capability[action]);
                },

                creativeProjectSource(project) {
                    if (project?.creation_mode === 'original') {
                        return project.seed?.seed_text || project.seed_summary || '原创灵感';
                    }
                    return project?.topic_title || '-';
                },

                togglePreservedBlueprint(blueprintId, checked) {
                    const ids = this.creativeWorkspace.preservedBlueprintIds.filter(id => Number(id) !== Number(blueprintId));
                    if (checked) ids.push(Number(blueprintId));
                    this.creativeWorkspace.preservedBlueprintIds = ids;
                },

                async saveOriginalSeed() {
                    const project = this.creativeWorkspace.project;
                    const seed = project?.seed;
                    if (!project || project.creation_mode !== 'original' || !seed) return;
                    const body = {
                        seed_text: String(seed.seed_text || '').trim(),
                        constraints: seed.constraints || {},
                        reference_refs: seed.reference_refs || [],
                    };
                    const frozen = seed.status === 'frozen';
                    const url = frozen
                        ? `/api/creative/projects/${project.id}/seeds/versions`
                        : `/api/creative/seeds/${seed.id}`;
                    const response = await aiworkerApi.request(url, {
                        method: frozen ? 'POST' : 'PATCH',
                        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || '保存原创种子失败');
                    project.seed = data;
                    this.creativeWorkspace.selectedSeedId = data.id;
                },

                riskLabel(level) { return {low: '低', medium: '中', high: '高'}[level] || level || '-'; },
                riskBadgeClass(level) { return level === 'high' ? 'bg-danger' : level === 'medium' ? 'bg-warning text-dark' : 'bg-success'; },
                listText(value) { return Array.isArray(value) ? value.join('\n') : String(value || ''); },
                creativeFieldPreview(value, fallback = '未填写') {
                    const text = String(value || '').replace(/\s+/g, ' ').trim();
                    return text || fallback;
                },
                imitationReason(content) {
                    const reason = String(content?.imitation_reason || '').trim();
                    if (reason) return reason;
                    const audience = String(content?.audience || '目标受众').trim();
                    const borrowed = this.listText(content?.borrowed_elements).replace(/\n/g, '、').trim() || '已拆解的结构与爽点';
                    return `面向${audience}，借鉴${borrowed}。`;
                },
                setImitationReason(content, value) {
                    content.imitation_reason = String(value || '').trim();
                },
                continuityStatusLabel(status) {
                    const code = String(status || '').trim().toLowerCase();
                    return {
                        active: '有效', blocked: '受阻', weakened: '削弱', aborted: '已中止',
                        low: '低', discovered: '已发现', established: '已确立', disclosed: '已公开',
                        confirmed: '已确认', hidden: '隐藏', lost: '遗失', destroyed: '已毁坏',
                        transferred: '已转移', revoked: '已撤销', resolved: '已解决',
                    }[code] || status || '-';
                },
                setListField(target, key, value) { target[key] = String(value || '').split(/[,，\n]/).map(item => item.trim()).filter(Boolean); },
                textareaRows(value, charactersPerLine = 28) {
                    const lines = String(value || '').split('\n');
                    return Math.max(2, lines.reduce((total, line) => total + Math.max(1, Math.ceil(line.length / charactersPerLine)), 0));
                },
                creativeItemSaveLabel(item, saveKey) {
                    const state = this.creativeWorkspace.saveState[saveKey];
                    if (state === 'saving') return '保存中';
                    return item?.updated_at ? '已保存' : '未保存';
                },
                showCreativeNotice(message, type = 'success') {
                    clearTimeout(this.creativeWorkspace.noticeTimer);
                    this.creativeWorkspace.notice = message;
                    this.creativeWorkspace.noticeType = type;
                    this.creativeWorkspace.noticeTimer = setTimeout(() => {
                        this.creativeWorkspace.notice = '';
                    }, 3000);
                    if (window.aiworkerUi) {
                        window.aiworkerUi.showToast({kind: type === 'success' ? 'success' : 'danger', title: type === 'success' ? '消息' : '错误', message});
                    }
                },

                creativeWorkflowStatusLabel(status) {
                    return {
                        draft: '草稿', locked: '已完成', brief_locked: '已完成',
                        superseded: '已替代'
                    }[status] || status || '草稿';
                },

                creativeResourceEditable(item) {
                    return !item || item.status === 'draft';
                },

                async resolveCreativeWorkflowTask(resourceType, projectId = null, topicId = null) {
                    const response = await aiworkerApi.request('/api/workflow/tasks');
                    const tasks = await response.json();
                    if (!response.ok) throw new Error(tasks.detail || '读取创作任务失败');
                    const open = ['assigned', 'in_progress', 'returned'];
                    const matches = (tasks || []).filter(item =>
                        item.task_type === resourceType
                        && (projectId === null || String(item.local_project_id || item.project_id || '') === String(projectId))
                        && (topicId === null || Number(item.topic_id) === Number(topicId))
                    );
                    let task = matches.find(item => open.includes(item.status));
                    if (!task) {
                        task = matches.find(item => item.status === 'pool' && item.can_claim);
                        if (task) {
                            const claimResponse = await aiworkerApi.request(
                                `/api/workflow/tasks/${task.id}/claim`, {method: 'POST'}
                            );
                            const claimed = await claimResponse.json();
                            if (!claimResponse.ok) {
                                throw new Error(claimed.detail || '自动领取创作任务失败');
                            }
                            task = claimed;
                        }
                    }
                    if (!task && projectId !== null) {
                        const bootstrapResponse = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/workflow-tasks/${resourceType}/ensure`,
                            {method: 'POST'}
                        );
                        const bootstrap = await bootstrapResponse.json();
                        if (!bootstrapResponse.ok) {
                            throw new Error(bootstrap.detail || '初始化创作任务失败');
                        }
                        task = bootstrap;
                    }
                    if (!task) throw new Error('当前没有可提交的创作任务，请先领取或开始对应任务');
                    if (['assigned', 'returned'].includes(task.status)) {
                        const startResponse = await aiworkerApi.request(`/api/workflow/tasks/${task.id}/start`, {method: 'POST'});
                        const started = await startResponse.json();
                        if (!startResponse.ok) throw new Error(started.detail || '开始创作任务失败');
                        return started;
                    }
                    return task;
                },

                async resolveSelectionTopicByTitle(title) {
                    const topicsResponse = await aiworkerApi.request('/api/creative/topics?limit=500&include_lifecycle=false');
                    const payload = await topicsResponse.json();
                    if (!topicsResponse.ok) throw new Error(payload.detail || '读取选题库失败');
                    const titleKey = value => String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
                    const matches = (payload.items || []).filter(item =>
                        item.status === 'adopted' && titleKey(item.title) === titleKey(title)
                    );
                    if (matches.length !== 1) throw new Error('请先在选题库采纳该剧目，再开始下载');
                    return matches[0];
                },

                async completeCreativeResource(resourceType, resourceId, projectId, differenceNote = '') {
                    const task = await this.resolveCreativeWorkflowTask(resourceType, projectId);
                    const batchId = String(resourceId).split(':', 2)[1];
                    const endpoint = {
                        brief: `/api/creative/blueprints/${resourceId}/lock-brief`,
                        characters: `/api/creative/projects/${projectId}/characters/${batchId}/lock`,
                        outline: `/api/creative/projects/${projectId}/outlines/${batchId}/lock`,
                        beats: `/api/creative/episodes/${resourceId}/lock`,
                        script: `/api/creative/scripts/${resourceId}/lock`,
                    }[resourceType];
                    if (!endpoint) throw new Error('不支持完成该创作阶段');
                    const body = {workflow_task_id: task.id};
                    if (resourceType === 'brief') {
                        body.difference_note = differenceNote || '';
                        body.expected_project_revision = Number(this.creativeWorkspace.project?.revision);
                    }
                    const response = await aiworkerApi.request(endpoint, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(body),
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || '完成并锁定失败');
                    return data;
                },

                latestProjectBlueprints() {
                    const items = this.creativeWorkspace.project?.blueprints || [];
                    const latest = new Map();
                    items.forEach(item => {
                        const current = latest.get(item.candidate_group_id);
                        if (!current || Number(item.version) > Number(current.version)) latest.set(item.candidate_group_id, item);
                    });
                    return [...latest.values()];
                },

                selectedProjectBlueprint() {
                    return (this.creativeWorkspace.project?.blueprints || []).find(item => item.id === this.creativeWorkspace.selectedBlueprintId) || null;
                },

                openProjectBlueprintEditor(blueprintId) {
                    this.creativeWorkspace.selectedBlueprintId = blueprintId;
                    window.requestAnimationFrame(() => {
                        document.querySelector('[data-creative-blueprint-editor]')
                            ?.scrollIntoView({behavior: 'smooth', block: 'start'});
                    });
                },

                availableLockedBriefs() {
                    return (this.creativeWorkspace.project?.blueprints || [])
                        .filter(item => item.status === 'brief_locked')
                        .sort((left, right) => Number(right.version || 0) - Number(left.version || 0));
                },

                availableLockedCharacterBatches() {
                    const project = this.creativeWorkspace.project;
                    if (!project?.locked_brief_id) return [];
                    const batches = new Map();
                    (project.characters || []).forEach(item => {
                        const batchId = String(item.batch_id || '');
                        if (!batchId || !['locked', 'approved'].includes(item.status)) return;
                        if (Number(item.source_refs?.brief_id || 0) !== Number(project.locked_brief_id)) return;
                        const batch = batches.get(batchId) || {batch_id: batchId, version: 0, names: []};
                        batch.version = Math.max(batch.version, Number(item.version || 0));
                        const name = String(item.content?.name || '').trim();
                        if (name) batch.names.push(name);
                        batches.set(batchId, batch);
                    });
                    return [...batches.values()].sort((left, right) => Number(right.version) - Number(left.version));
                },

                async selectCharacterBrief() {
                    const project = this.creativeWorkspace.project;
                    const briefId = Number(this.creativeWorkspace.selectedCharacterBriefId || 0);
                    if (!project || !briefId || briefId === Number(project.locked_brief_id || 0)) return;
                    if (!window.confirm('切换创作方向会使当前人物关系和后续大纲不再作为当前版本使用。是否继续？')) {
                        this.creativeWorkspace.selectedCharacterBriefId = project.locked_brief_id || null;
                        return;
                    }
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}/briefs/${briefId}/select`, {method: 'POST'});
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '切换创作方向失败');
                        this.creativeWorkspace.project = data;
                        this.creativeWorkspace.selectedCharacterBriefId = data.locked_brief_id || null;
                        this.showCreativeNotice('已切换创作方向，请生成人物关系的新版本');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '切换创作方向失败';
                        this.creativeWorkspace.selectedCharacterBriefId = project.locked_brief_id || null;
                    }
                },

                async selectOutlineBrief() {
                    const project = this.creativeWorkspace.project;
                    const briefId = Number(this.creativeWorkspace.selectedOutlineBriefId || 0);
                    if (!project || !briefId || briefId === Number(project.locked_brief_id || 0)) return;
                    if (!window.confirm('切换创作方向会使当前人物关系和全剧大纲不再作为当前版本使用。是否继续？')) {
                        this.creativeWorkspace.selectedOutlineBriefId = project.locked_brief_id || null;
                        return;
                    }
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}/briefs/${briefId}/select`, {method: 'POST'});
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '切换创作方向失败');
                        this.creativeWorkspace.project = data;
                        this.creativeWorkspace.selectedCharacterBriefId = data.locked_brief_id || null;
                        this.creativeWorkspace.selectedOutlineBriefId = data.locked_brief_id || null;
                        this.creativeWorkspace.selectedOutlineCharacterBatchId = data.character_batch_id || null;
                        this.showCreativeNotice('已切换创作方向，请生成人物关系和全剧大纲的新版本');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '切换创作方向失败';
                        this.creativeWorkspace.selectedOutlineBriefId = project.locked_brief_id || null;
                    }
                },

                async selectOutlineCharacterBatch() {
                    const project = this.creativeWorkspace.project;
                    const batchId = String(this.creativeWorkspace.selectedOutlineCharacterBatchId || '');
                    if (!project || !batchId || batchId === String(project.character_batch_id || '')) return;
                    if (!window.confirm('切换人物关系版本会使当前全剧大纲不再作为当前版本使用。是否继续？')) {
                        this.creativeWorkspace.selectedOutlineCharacterBatchId = project.character_batch_id || null;
                        return;
                    }
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}/character-batches/${batchId}/select`, {method: 'POST'});
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '切换人物关系版本失败');
                        this.creativeWorkspace.project = data;
                        this.creativeWorkspace.selectedOutlineCharacterBatchId = data.character_batch_id || null;
                        this.showCreativeNotice('已切换人物关系版本，请生成全剧大纲的新版本');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '切换人物关系版本失败';
                        this.creativeWorkspace.selectedOutlineCharacterBatchId = project.character_batch_id || null;
                    }
                },

                normalizeBlueprintContent(content) {
                    const flatten = (value) => {
                        if (Array.isArray(value)) return value.map(flatten).filter(Boolean).join('；');
                        if (value && typeof value === 'object') return Object.values(value).map(flatten).filter(Boolean).join(' ');
                        return String(value || '').trim();
                    };
                    const normalized = {...(content || {})};
                    ['title', 'imitation_reason', 'protagonist', 'core_conflict', 'main_goal', 'background', 'worldview', 'audience', 'cost_level']
                        .forEach((key) => { normalized[key] = flatten(normalized[key]); });
                    ['borrowed_elements', 'different_elements', 'first_three_hooks', 'highlights', 'revision_suggestions', 'risk_reasons']
                        .forEach((key) => {
                            const values = Array.isArray(normalized[key]) ? normalized[key] : [normalized[key]];
                            normalized[key] = values.map(flatten).filter(Boolean);
                        });
                    return normalized;
                },

                latestCharacterBatchId() {
                    const project = this.creativeWorkspace.project;
                    if (!project) return '';
                    if (project.workflow_mode === 'free') {
                        return String(this.creativeHead('characters')?.resource_ref || '');
                    }
                    const drafts = (project.characters || [])
                        .filter(item => ['draft', 'submitted', 'returned'].includes(item.status))
                        .sort((a, b) => Number(b.version) - Number(a.version));
                    if (drafts.length) return drafts[0].batch_id;
                    if (project.character_batch_id) return project.character_batch_id;
                    const items = project.characters || [];
                    return items.length ? items[items.length - 1].batch_id : '';
                },

                currentProjectCharacters() {
                    const batch = this.latestCharacterBatchId();
                    return (this.creativeWorkspace.project?.characters || []).filter(item => item.batch_id === batch);
                },

                currentProjectRelationships() {
                    const batch = this.latestCharacterBatchId();
                    return (this.creativeWorkspace.project?.relationships || []).filter(item => item.batch_id === batch);
                },

                selectedProjectCharacter() {
                    const characters = this.currentProjectCharacters();
                    return characters.find(item => String(item.id) === String(this.creativeWorkspace.selectedCharacterId)) || characters[0] || null;
                },

                openCharacterEditor(item) {
                    if (!item?.id) return;
                    const selectedId = this.creativeWorkspace.selectedCharacterId;
                    if (String(selectedId) === String(item.id) && Object.keys(this.creativeWorkspace.characterEditorDraft || {}).length) return;
                    this.creativeWorkspace.selectedCharacterId = item.id;
                    this.creativeWorkspace.characterEditorDraft = this.cloneCreativeJson(item.content, {});
                    this.creativeWorkspace.characterEditorRevision = Number(this.creativeWorkspace.characterEditorRevision || 0) + 1;
                },

                syncCharacterEditorField(element, field, listField = false) {
                    if (!element) return;
                    const item = this.selectedProjectCharacter();
                    const revision = Number(this.creativeWorkspace.characterEditorRevision || 0);
                    const syncKey = `${item?.id || 'none'}:${revision}:${field}`;
                    if (element.dataset.characterEditorKey === syncKey) return;
                    const value = this.creativeWorkspace.characterEditorDraft?.[field];
                    element.textContent = listField ? this.listText(value) : String(value || '');
                    element.dataset.characterEditorKey = syncKey;
                },

                updateCharacterEditorField(field, value, listField = false) {
                    if (listField) {
                        this.setListField(this.creativeWorkspace.characterEditorDraft, field, value);
                        return;
                    }
                    this.creativeWorkspace.characterEditorDraft[field] = String(value || '').replace(/\r/g, '');
                },

                async saveCharacterEditor() {
                    const item = this.selectedProjectCharacter();
                    if (!item || !this.creativeResourceEditable(item)) return null;
                    item.content = this.cloneCreativeJson(this.creativeWorkspace.characterEditorDraft, item.content);
                    return this.saveProjectCharacter(item);
                },

                selectedProjectRelationship() {
                    const relationships = this.currentProjectRelationships();
                    return relationships.find(item => String(item.id) === String(this.creativeWorkspace.selectedRelationshipId)) || relationships[0] || null;
                },

                openRelationshipEditor(item) {
                    if (!item?.id) return;
                    const selectedId = this.creativeWorkspace.selectedRelationshipId;
                    if (String(selectedId) === String(item.id) && Object.keys(this.creativeWorkspace.relationshipEditorDraft || {}).length) return;
                    this.creativeWorkspace.selectedRelationshipId = item.id;
                    this.creativeWorkspace.relationshipEditorDraft = this.cloneCreativeJson(item.content, {});
                    this.creativeWorkspace.relationshipEditorRevision = Number(this.creativeWorkspace.relationshipEditorRevision || 0) + 1;
                },

                syncRelationshipEditorField(element, field) {
                    if (!element) return;
                    const item = this.selectedProjectRelationship();
                    const revision = Number(this.creativeWorkspace.relationshipEditorRevision || 0);
                    const syncKey = `${item?.id || 'none'}:${revision}:${field}`;
                    if (element.dataset.relationshipEditorKey === syncKey) return;
                    element.textContent = String(this.creativeWorkspace.relationshipEditorDraft?.[field] || '');
                    element.dataset.relationshipEditorKey = syncKey;
                },

                updateRelationshipEditorField(field, value) {
                    this.creativeWorkspace.relationshipEditorDraft[field] = String(value || '').replace(/\r/g, '');
                },

                async saveRelationshipEditor() {
                    const item = this.selectedProjectRelationship();
                    if (!item || !this.creativeResourceEditable(item)) return null;
                    item.content = this.cloneCreativeJson(this.creativeWorkspace.relationshipEditorDraft, item.content);
                    return this.saveProjectRelationship(item);
                },

                currentCharacterBatchStatus() {
                    return this.currentProjectCharacters()[0]?.status || 'draft';
                },

                creativeStageLabel(stage) {
                    return {outline: '分集大纲', beats: 'Beats', script: '场次剧本'}[stage] || stage;
                },

                freeStageAction(stage) {
                    return {brief: 'blueprints', characters: 'characters', outline: 'outlines', beats: 'beats', script: 'script'}[stage] || stage;
                },

                freeStageLabel(stage) {
                    return {brief: 'Brief', characters: '人物', outline: '大纲', beats: 'Beats', script: '场次剧本'}[stage] || stage || '';
                },

                cloneCreativeJson(value, fallback = {}) {
                    const source = value === null || value === undefined ? fallback : value;
                    return JSON.parse(JSON.stringify(source));
                },

                async openCreativeModeMigration() {
                    const project = this.creativeWorkspace.project;
                    if (!project || project.workflow_mode !== 'sequential') return;
                    const state = this.creativeWorkspace.modeMigration;
                    this.cancelCreativeModeMigrationPreview();
                    Object.assign(state, {preview: null, confirmed: false, submitting: false, error: ''});
                    this.creativeModeMigrationModal?.show();
                    await this.loadCreativeModeMigrationPreview(project.id);
                },

                cancelCreativeModeMigrationPreview() {
                    const state = this.creativeWorkspace.modeMigration;
                    state.requestSequence = Number(state.requestSequence || 0) + 1;
                    this.creativeModeMigrationPreviewController?.abort();
                    this.creativeModeMigrationPreviewController = null;
                    state.loading = false;
                },

                async retryCreativeModeMigrationPreview() {
                    const project = this.creativeWorkspace.project;
                    if (!project || project.workflow_mode !== 'sequential') return;
                    await this.loadCreativeModeMigrationPreview(project.id);
                },

                async loadCreativeModeMigrationPreview(projectId) {
                    const state = this.creativeWorkspace.modeMigration;
                    this.cancelCreativeModeMigrationPreview();
                    const requestSequence = Number(state.requestSequence || 0);
                    const controller = new AbortController();
                    this.creativeModeMigrationPreviewController = controller;
                    Object.assign(state, {preview: null, confirmed: false, loading: true, error: ''});
                    const timeoutId = window.setTimeout(() => controller.abort(), 10000);
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/mode-migration-preview`,
                            {signal: controller.signal}
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取迁移预检失败');
                        if (Number(state.requestSequence || 0) !== requestSequence) return;
                        state.preview = data;
                    } catch (error) {
                        if (Number(state.requestSequence || 0) !== requestSequence) return;
                        state.error = aiworkerApi.isAbortError(error)
                            ? '迁移预检超时，请重试'
                            : (error.message || '读取迁移预检失败');
                    } finally {
                        window.clearTimeout(timeoutId);
                        if (Number(state.requestSequence || 0) === requestSequence) {
                            state.loading = false;
                            if (this.creativeModeMigrationPreviewController === controller) {
                                this.creativeModeMigrationPreviewController = null;
                            }
                        }
                    }
                },

                async migrateCreativeProjectToFree() {
                    const project = this.creativeWorkspace.project;
                    const state = this.creativeWorkspace.modeMigration;
                    if (!project || !state.preview || !state.confirmed || state.submitting) return;
                    state.submitting = true;
                    state.error = '';
                    try {
                        const body = {
                            expected_mode_revision: Number(project.mode_revision || 0),
                            operation_id: globalThis.crypto?.randomUUID?.() || `free-migration-${Date.now()}`,
                        };
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}/migrate-to-free`, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
                        });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建自由创作副本失败');
                        this.creativeModeMigrationModal?.hide();
                        await this.selectCreativeProject(data.project.id);
                        this.showCreativeNotice('已创建并打开自由创作副本');
                    } catch (error) {
                        state.error = error.message || '创建自由创作副本失败';
                    } finally {
                        state.submitting = false;
                    }
                },

                creativeProjectHasJobResult(project, action, jobId) {
                    if (!project || !action || !jobId) return false;
                    const generatedByJob = item => item.generation_meta?.job_id === jobId;
                    if (action === 'blueprints') return (project.blueprints || []).some(generatedByJob);
                    if (action === 'characters') {
                        return (project.characters || []).some(generatedByJob)
                            || (project.relationships || []).some(generatedByJob);
                    }
                    if (action === 'outlines') {
                        const generatedForJob = (project.episodes || []).filter(item =>
                            item.stage === 'outline' && item.generation_meta?.job_id === jobId
                        );
                        return generatedForJob.length >= Number(project.target_episode_count || 0);
                    }
                    if (action === 'outline_overview') {
                        // Manual saves can populate the batch while this AI job is still
                        // running. Only the job terminal state can prove this job succeeded.
                        return false;
                    }
                    if (action === 'beats') {
                        const generated = (project.episodes || []).filter(item => item.stage === 'beats' && item.generation_meta?.job_id === jobId);
                        return this.creativeWorkspace.activeSeriesGeneration
                            ? generated.length >= Number(project.target_episode_count || 0)
                            : generated.length > 0;
                    }
                    if (action === 'script') {
                        const generated = (project.episodes || []).filter(item => item.stage === 'script' && item.generation_meta?.job_id === jobId);
                        return this.creativeWorkspace.activeSeriesGeneration
                            ? generated.length >= Number(project.target_episode_count || 0)
                            : generated.length > 0;
                    }
                    return false;
                },

                finishCreativeGeneration(message = '生成完成，结果已回填到当前项目') {
                    this.creativeWorkspace.jobStatus = 'succeeded';
                    this.creativeWorkspace.jobMessage = message;
                    this.creativeWorkspace.jobProgress = {completed: 1, total: 1};
                    this.creativeWorkspace.startingAction = '';
                    this.creativeWorkspace.activeAction = '';
                    this.creativeWorkspace.activeEpisode = 0;
                    this.creativeWorkspace.activeSeriesGeneration = false;
                    this.creativeWorkspace.activeBatchId = '';
                    this.creativeWorkspace.paused = false;
                    this.creativeWorkspace.notifiedStatus = 'succeeded';
                },

                async reconcileCreativeGenerationState(project) {
                    const action = this.creativeWorkspace.activeAction;
                    const jobId = this.creativeWorkspace.jobId;
                    if (!this.creativeProjectHasJobResult(project, action, jobId)) return false;
                    this.finishCreativeGeneration();
                    if (action === 'script') await this.refreshCreativeTopicLibrary();
                    this.showCreativeNotice('生成完成，结果已回填到当前项目。');
                    return true;
                },

}));
