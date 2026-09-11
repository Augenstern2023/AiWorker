window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                dramaReviewStatusLabel() {
                    return {queued: '等待采集', running: '采集中', completed: '已完成', failed: '失败'}[this.dramaReviews.status] || '未开始';
                },

                dramaReviewPlatformForTopic(topic) {
                    const platforms = topic?.source_platforms || [topic?.platform];
                    return platforms.includes('hg') ? 'hg' : 'dy';
                },

                isDouyinDramaReview(state) {
                    return state?.platform === 'dy';
                },

                dramaReviewTopicKey(topic) {
                    return String(topic?.id || topic?.title || '');
                },

                dramaReviewJobForTopic(topic) {
                    return this.dramaReviewJobs[this.dramaReviewTopicKey(topic)] || null;
                },

                dramaReviewRunningForTopic(topic) {
                    return ['queued', 'running'].includes(this.dramaReviewJobForTopic(topic)?.status);
                },

                dramaReviewActionTitle(topic) {
                    const job = this.dramaReviewJobForTopic(topic);
                    if (['queued', 'running'].includes(job?.status)) return '查看剧评下载进度';
                    if (job?.status === 'completed') return '下载剧评（已完成）';
                    return '下载剧评';
                },

                persistDramaReviewJob(state) {
                    const key = this.dramaReviewTopicKey(state?.topic);
                    if (!key || !state?.jobId) return;
                    this.dramaReviewJobs[key] = state;
                },

                syncDramaReviewModal(state) {
                    if (this.dramaReviewTopicKey(this.dramaReviews.topic) === this.dramaReviewTopicKey(state?.topic)) {
                        Object.assign(this.dramaReviews, state);
                    }
                },

                finishDisplayedDramaReviewJob() {
                    const state = this.dramaReviews;
                    if (!state?.jobId || !['completed', 'failed'].includes(state.status)) return;
                    state.polling = false;
                    this.persistDramaReviewJob(state);
                },

                async parseDramaReviewResponse(response) {
                    const body = await response.text();
                    try {
                        return body ? JSON.parse(body) : {};
                    } catch (_) {
                        return {detail: body.trim() || `服务器返回错误（${response.status}）`};
                    }
                },

                async startDramaReviewCapture() {
                    const state = this.dramaReviews;
                    const title = String(state.title || '').trim();
                    const limit = Number(state.limit || 0);
                    if (!title) {
                        state.error = '请输入剧名';
                        return;
                    }
                    if (!state.allReviews && (!Number.isInteger(limit) || limit < 1 || limit > 10000)) {
                        state.error = '数量限制需为 1 到 10000 的整数';
                        return;
                    }
                    state.loading = true;
                    state.error = '';
                    state.items = [];
                    const topicKey = this.dramaReviewTopicKey(state.topic);
                    const requestState = {...state};
                    try {
                        const endpoint = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/capture';
                        const payload = this.isDouyinDramaReview(state)
                            ? {topic_id: state.topic?.id, title, limit, all_reviews: Boolean(state.allReviews)}
                            : {title, limit, all_reviews: Boolean(state.allReviews)};
                        const response = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        const data = await this.parseDramaReviewResponse(response);
                        if (!response.ok) throw new Error(data.detail || data.message || '剧评检索失败');
                        const jobState = {...requestState, loading: false, polling: false};
                        this.applyDramaReviewCapture(data, jobState);
                        await this.pollDramaReviewCapture(jobState);
                    } catch (error) {
                        if (this.dramaReviewTopicKey(this.dramaReviews.topic) === topicKey) this.dramaReviews.error = error.message || '剧评检索失败';
                    } finally {
                        if (this.dramaReviewTopicKey(this.dramaReviews.topic) === topicKey) this.dramaReviews.loading = false;
                    }
                },

                applyDramaReviewCapture(data, state = this.dramaReviews) {
                    const job = data?.job || {};
                    state.jobId = job.job_id || state.jobId;
                    state.status = job.status || state.status;
                    state.reviewCount = Number(job.review_count || 0);
                    state.sourceTotal = Number(job.source_total || 0);
                    state.items = data?.reviews || [];
                    state.error = job.status === 'failed' ? String(job.error || '剧评采集失败') : '';
                    this.persistDramaReviewJob(state);
                    this.syncDramaReviewModal(state);
                },

                async pollDramaReviewCapture(state = this.dramaReviews) {
                    const jobId = state.jobId;
                    if (!jobId || state.polling) return;
                    state.polling = true;
                    try {
                        const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                        const response = await aiworkerApi.request(`${root}/${encodeURIComponent(jobId)}`);
                        const data = await this.parseDramaReviewResponse(response);
                        if (!response.ok) throw new Error(data.detail || data.message || '读取剧评检索进度失败');
                        if (state.jobId !== jobId) return;
                        this.applyDramaReviewCapture(data, state);
                        if (['queued', 'running'].includes(state.status)) {
                            window.setTimeout(() => this.pollDramaReviewCapture(state), 2000);
                        }
                    } catch (error) {
                        if (state.jobId === jobId) {
                            state.error = error.message || '读取剧评检索进度失败';
                            this.persistDramaReviewJob(state);
                        }
                    } finally {
                        state.polling = false;
                        this.persistDramaReviewJob(state);
                        this.syncDramaReviewModal(state);
                    }
                },

                downloadDramaReviews() {
                    if (!this.dramaReviews.jobId || this.dramaReviews.status !== 'completed') return;
                    const state = this.dramaReviews;
                    const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                    window.open(`${root}/${encodeURIComponent(state.jobId)}/download?format=${encodeURIComponent(state.format || 'csv')}`, '_blank', 'noopener');
                },

                async openTopicReviewCapture(topic) {
                    const state = this.dramaReviews;
                    const existing = this.dramaReviewJobForTopic(topic);
                    Object.assign(state, {
                        topic,
                        title: String(topic?.title || '').trim(),
                        platform: this.dramaReviewPlatformForTopic(topic),
                        limit: 100,
                        allReviews: false,
                        format: 'csv',
                        jobId: '',
                        status: '',
                        reviewCount: 0,
                        sourceTotal: 0,
                        items: [],
                        loading: false,
                        polling: false,
                        error: ''
                    }, existing || {});
                    state.topic = topic;
                    state.title = String(topic?.title || '').trim();
                    state.platform = existing?.platform || this.dramaReviewPlatformForTopic(topic);
                    state.loading = false;
                    state.polling = false;
                    this.dramaReviewCaptureModal?.show();
                    if (['queued', 'running'].includes(existing?.status)) this.pollDramaReviewCapture(existing);
                },

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
                            aiworkerApi.request('/api/creative/topics?status=adopted&limit=500')
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
                    window.location.assign(`/planning/projects/${project.id}/brief`);
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
                        await this.loadCreativeModuleCapabilities(data.id);
                        if (data.workflow_mode === 'sequential') {
                            await this.loadSequentialGenerationModels(data.id);
                        }
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
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '读取项目失败';
                    }
                },

                async refreshCreativeTopicLibrary() {
                    if (typeof this.fetchTopicLibrary === 'function') {
                        await this.fetchTopicLibrary();
                    }
                },

                creativeProjectStatusLabel(status) {
                    return {
                        created: '待生成方向', direction_review: '方向评审', brief_locked: 'Brief 已锁定',
                        characters_draft: '人物草稿', characters_locked: '人物已锁定', outline_draft: '大纲草稿',
                        outline_locked: '大纲已锁定', beats_in_progress: 'Beats 制作中', archived: '已归档'
                    }[status] || status || '-';
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

                sequentialModelStorageKey(projectId = null) {
                    const id = projectId || this.creativeWorkspace.project?.id || '';
                    return `creative:sequential-model:${id}`;
                },

                async loadSequentialGenerationModels(projectId) {
                    const state = this.creativeWorkspace.sequentialGeneration;
                    if (state.loaded || state.loading) return;
                    state.loading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request('/api/llm/available-models');
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取可用模型失败');
                        state.modelOptions = Array.isArray(data.items) ? data.items : [];
                        let saved = null;
                        try {
                            saved = sessionStorage.getItem(this.sequentialModelStorageKey(projectId));
                        } catch (_) {}
                        const validIds = new Set(state.modelOptions.map(item => String(item.id)));
                        state.modelRecordId = saved !== null && (saved === '' || validIds.has(saved))
                            ? saved : String(data.default_model_record_id || '');
                        state.loaded = true;
                    } catch (error) {
                        state.error = error.message || '读取可用模型失败';
                    } finally {
                        state.loading = false;
                    }
                },

                selectSequentialGenerationModel(value) {
                    const selected = String(value || '');
                    this.creativeWorkspace.sequentialGeneration.modelRecordId = selected;
                    try {
                        sessionStorage.setItem(this.sequentialModelStorageKey(), selected);
                    } catch (_) {}
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
                    const topicsResponse = await aiworkerApi.request('/api/creative/topics?limit=500');
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

                currentCharacterBatchStatus() {
                    return this.currentProjectCharacters()[0]?.status || 'draft';
                },

                latestOutlineBatchId() {
                    const project = this.creativeWorkspace.project;
                    if (!project) return '';
                    if (project.workflow_mode === 'free') {
                        return String(this.creativeHead('outline')?.resource_ref || '');
                    }
                    const compatible = (project.episodes || []).filter(item =>
                        item.stage === 'outline'
                        && Number(item.upstream_refs?.brief_id || 0) === Number(project.locked_brief_id || 0)
                        && String(item.upstream_refs?.character_batch_id || '') === String(project.character_batch_id || '')
                    );
                    const drafts = compatible
                        .filter(item => ['draft', 'submitted', 'returned'].includes(item.status))
                        .sort((a, b) => Number(b.version) - Number(a.version));
                    if (project.status === 'outline_draft' && drafts.length) {
                        return drafts[0].batch_id;
                    }
                    if (project.outline_batch_id && compatible.some(item => item.batch_id === project.outline_batch_id)) {
                        return project.outline_batch_id;
                    }
                    if (drafts.length) return drafts[0].batch_id;
                    return '';
                },

                currentProjectOutlines() {
                    const batch = this.latestOutlineBatchId();
                    return (this.creativeWorkspace.project?.episodes || [])
                        .filter(item => item.stage === 'outline' && item.batch_id === batch)
                        .sort((a, b) => Number(a.episode_number) - Number(b.episode_number));
                },

                projectStageEpisodeItems(stage) {
                    const total = Number(this.creativeWorkspace.project?.target_episode_count || 0);
                    if (stage === 'outline') return this.currentProjectOutlines();
                    const resolve = stage === 'beats'
                        ? episodeNumber => this.projectBeatsForEpisode(episodeNumber)
                        : episodeNumber => this.projectEpisodeScript(episodeNumber);
                    return Array.from({length: total}, (_, index) => resolve(index + 1)).filter(Boolean);
                },

                displayedProjectStageItems(stage) {
                    const selected = Number(this.creativeWorkspace.selectedEpisode);
                    const items = this.projectStageEpisodeItems(stage);
                    return selected === 0
                        ? items
                        : items.filter(item => Number(item.episode_number) === selected);
                },

                hasResumableProjectOutlines() {
                    const project = this.creativeWorkspace.project;
                    const outlines = this.currentProjectOutlines();
                    return Boolean(project && outlines.length > 0
                        && outlines.length < Number(project.target_episode_count || 0)
                        && this.creativeResourceEditable(outlines[0]));
                },

                outlineGenerationActionLabel() {
                    if (this.hasResumableProjectOutlines()) return '继续生成';
                    if (['locked', 'approved'].includes(this.currentOutlineBatchStatus())) return '重新生成';
                    return '生成大纲';
                },

                currentOutlineBatchStatus() {
                    return this.currentProjectOutlines()[0]?.status || 'draft';
                },

                projectBeatsForEpisode(episodeNumber) {
                    const project = this.creativeWorkspace.project;
                    const headRef = project?.workflow_mode === 'free'
                        ? String(this.creativeHead('beats', episodeNumber)?.resource_ref || '')
                        : '';
                    const items = (project?.episodes || [])
                        .filter(item => item.stage === 'beats'
                            && Number(item.episode_number) === Number(episodeNumber)
                            && (project?.workflow_mode === 'free'
                                ? String(item.id) === headRef
                                : item.upstream_refs?.outline_batch_id === project?.outline_batch_id))
                        .sort((a, b) => Number(b.version) - Number(a.version));
                    return items[0] || null;
                },

                selectedProjectBeats() {
                    return this.projectBeatsForEpisode(this.creativeWorkspace.selectedEpisode);
                },

                projectBeatEpisodeLabel(episodeNumber) {
                    const item = this.projectBeatsForEpisode(episodeNumber);
                    if (!item) return `第 ${episodeNumber} 集 · 未生成`;
                    return `第 ${episodeNumber} 集 · v${item.version} · ${this.creativeWorkflowStatusLabel(item.status)}`;
                },

                projectBeatGenerationActionLabel() {
                    return this.selectedProjectBeats() ? '重新生成 Beats' : '生成 Beats';
                },

                projectBeatsGenerationProgress() {
                    const project = this.creativeWorkspace.project;
                    if (project?.workflow_mode === 'free') {
                        const generated = new Set((project.resource_heads || [])
                            .filter(item => item.stage === 'beats')
                            .map(item => Number(item.episode_number)));
                        return {generated: generated.size, required: Number(project.target_episode_count || 0)};
                    }
                    const generated = new Set((project?.episodes || []).filter(item =>
                        item.stage === 'beats'
                        && item.upstream_refs?.outline_batch_id === project?.outline_batch_id
                    ).map(item => Number(item.episode_number)));
                    return {generated: generated.size, required: Number(project?.target_episode_count || 0)};
                },

                batchLockableProjectBeats() {
                    return this.batchLockableProjectEpisodes('beats');
                },

                batchLockableProjectEpisodes(stage) {
                    const total = Number(this.creativeWorkspace.project?.target_episode_count || 0);
                    const resolve = stage === 'beats'
                        ? episodeNumber => this.projectBeatsForEpisode(episodeNumber)
                        : episodeNumber => this.projectEpisodeScript(episodeNumber);
                    return Array.from({length: total}, (_, index) => index + 1)
                        .map(resolve)
                        .filter((item) => item?.status === 'draft');
                },

                canBatchLockProjectBeats() {
                    return this.canBatchLockProjectEpisodes('beats');
                },

                canBatchLockProjectEpisodes(stage) {
                    const total = Number(this.creativeWorkspace.project?.target_episode_count || 0);
                    if (!total) return false;
                    const resolve = stage === 'beats'
                        ? episodeNumber => this.projectBeatsForEpisode(episodeNumber)
                        : episodeNumber => this.projectEpisodeScript(episodeNumber);
                    const items = Array.from({length: total}, (_, index) => resolve(index + 1));
                    return items.every((item) => item && ['draft', 'locked', 'approved'].includes(item.status))
                        && this.batchLockableProjectEpisodes(stage).length > 0;
                },

                selectedProjectScript() {
                    return this.projectEpisodeScript(this.creativeWorkspace.selectedEpisode);
                },

                projectScriptGenerationActionLabel() {
                    const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                    const hasPriorScript = (this.creativeWorkspace.project?.episodes || []).some(item =>
                        item.stage === 'script' && Number(item.episode_number) === episode
                    );
                    return this.selectedProjectScript() || hasPriorScript
                        ? '重新生成场次剧本'
                        : '生成场次剧本';
                },

                scriptWorkspaceEmptyMessage() {
                    if (this.isFreeCreativeProject()) return '当前集尚未生成场次剧本';
                    const currentBeats = this.lockedProjectEpisodeBeats(
                        this.creativeWorkspace.selectedEpisode
                    );
                    if (!currentBeats) {
                        return '请先生成并完成当前集 Beats，再生成场次剧本';
                    }
                    const episode = Number(this.creativeWorkspace.selectedEpisode);
                    const olderScript = (this.creativeWorkspace.project?.episodes || [])
                        .filter(item => item.stage === 'script'
                            && Number(item.episode_number) === episode
                            && (Number(item.upstream_refs?.beats_id) !== Number(currentBeats.id)
                                || Number(item.upstream_refs?.beats_version) !== Number(currentBeats.version)))
                        .sort((a, b) => Number(b.version) - Number(a.version))[0];
                    const olderScriptBeatsVersion = olderScript?.upstream_refs?.beats_version;
                    return olderScript
                        ? `当前仅有基于 Beats v${olderScriptBeatsVersion || '未知'} 的旧场次剧本；当前 Beats 为 v${currentBeats.version}，请重新生成后再完成并锁定`
                        : '当前集尚未生成场次剧本';
                },

                lockedProjectEpisodeBeats(episodeNumber) {
                    const project = this.creativeWorkspace.project;
                    if (project?.workflow_mode === 'free') {
                        const item = this.projectBeatsForEpisode(episodeNumber);
                        return item && ['locked', 'approved'].includes(item.status) ? item : null;
                    }
                    return (project?.episodes || [])
                        .filter(item => item.stage === 'beats'
                            && Number(item.episode_number) === Number(episodeNumber)
                            && item.upstream_refs?.outline_batch_id === project?.outline_batch_id
                            && ['locked', 'approved'].includes(item.status))
                        .sort((a, b) => Number(b.version) - Number(a.version))[0] || null;
                },

                addContinuityUpdate(beat) {
                    if (!Array.isArray(beat.continuity_updates)) beat.continuity_updates = [];
                    beat.continuity_updates.push({
                        kind: 'information', name: '', event: '', holder: '', location: '', known_by: [], status: 'active',
                    });
                },

                removeContinuityUpdate(beat, updateIndex) {
                    if (!Array.isArray(beat.continuity_updates)) return;
                    beat.continuity_updates.splice(Number(updateIndex), 1);
                },

                projectContinuityLedger() {
                    const latestByEpisode = new Map();
                    const project = this.creativeWorkspace.project;
                    (project?.episodes || [])
                        .filter(item => item.stage === 'beats'
                            && (project?.workflow_mode === 'free'
                                ? String(this.creativeHead('beats', item.episode_number)?.resource_ref || '') === String(item.id)
                                : item.upstream_refs?.outline_batch_id === project?.outline_batch_id))
                        .forEach(item => {
                            const current = latestByEpisode.get(Number(item.episode_number));
                            if (!current || Number(item.version) > Number(current.version)) {
                                latestByEpisode.set(Number(item.episode_number), item);
                            }
                        });
                    const ledger = new Map();
                    [...latestByEpisode.values()]
                        .sort((a, b) => Number(a.episode_number) - Number(b.episode_number))
                        .forEach(item => (item.content?.beats || [])
                            .sort((a, b) => Number(a.order) - Number(b.order))
                            .forEach(beat => (beat.continuity_updates || []).forEach(update => {
                                if (update?.kind && update?.name) ledger.set(`${update.kind}:${update.name}`, update);
                            })));
                    return [...ledger.values()].map(item => ({
                        ...item,
                        status: this.continuityStatusLabel(item.status),
                    }));
                },

                currentEpisodeHasLockedBeats() {
                    if (this.isFreeCreativeProject()) return true;
                    return Boolean(
                        this.lockedProjectEpisodeBeats(
                            this.creativeWorkspace.selectedEpisode
                        )
                    );
                },

                projectEpisodeScript(episodeNumber) {
                    const project = this.creativeWorkspace.project;
                    if (project?.workflow_mode === 'free') {
                        const headRef = String(this.creativeHead('script', episodeNumber)?.resource_ref || '');
                        return (project.episodes || []).find(item =>
                            item.stage === 'script'
                            && Number(item.episode_number) === Number(episodeNumber)
                            && String(item.id) === headRef
                        ) || null;
                    }
                    const currentBeats = this.lockedProjectEpisodeBeats(episodeNumber);
                    if (!currentBeats) return null;
                    return (project?.episodes || [])
                        .filter(item => item.stage === 'script'
                            && Number(item.episode_number) === Number(episodeNumber)
                            && Number(item.upstream_refs?.beats_id) === Number(currentBeats.id)
                            && Number(item.upstream_refs?.beats_version) === Number(currentBeats.version))
                        .sort((a, b) => Number(b.version) - Number(a.version))[0] || null;
                },

                projectEpisodeScriptLabel(episodeNumber) {
                    const script = this.projectEpisodeScript(episodeNumber);
                    if (!script) return '';
                    return script.status === 'locked' ? '剧本已完成' : '剧本已生成';
                },

                projectEpisodeUpdateRecords(episodeNumber) {
                    const project = this.creativeWorkspace.project;
                    if (!project) return [];
                    const outlineBatchId = project.outline_batch_id;
                    return (project.episodes || [])
                        .filter(item => ['beats', 'script'].includes(item.stage)
                            && Number(item.episode_number) === Number(episodeNumber)
                            && item.upstream_refs?.outline_batch_id === outlineBatchId)
                        .map(item => {
                            const createdAt = Number(item.created_at || 0);
                            const updatedAt = Number(item.updated_at || 0);
                            const locked = ['locked', 'approved'].includes(item.status);
                            const versioned = Number(item.version) > 1;
                            const event = versioned && locked ? '新版本已锁定'
                                : versioned ? '新版本生成'
                                    : locked && updatedAt > createdAt ? '完成并锁定'
                                        : updatedAt > createdAt ? '内容已更新' : '首次生成';
                            return {
                                key: `${item.stage}-${item.id}`,
                                stage: item.stage,
                                label: item.stage === 'beats' ? 'Beats' : '场次剧本',
                                version: Number(item.version || 1),
                                status: item.status,
                                event,
                                timestamp: updatedAt || createdAt,
                            };
                        })
                        .sort((left, right) => Number(right.timestamp) - Number(left.timestamp));
                },

                hasProjectEpisodeUpdates(episodeNumber) {
                    return this.projectEpisodeUpdateRecords(episodeNumber)
                        .some(record => record.version > 1 || record.event !== '首次生成');
                },

                projectEpisodeUpdateSummary(episodeNumber) {
                    const updates = this.projectEpisodeUpdateRecords(episodeNumber)
                        .filter(record => record.version > 1 || record.event !== '首次生成');
                    const stages = [...new Set(updates.map(record => record.stage))];
                    const labels = stages.map(stage => stage === 'beats' ? 'Beats 更新' : '剧本更新');
                    return labels.join(' / ');
                },

                openOutlineEpisodeUpdates(episodeNumber) {
                    this.creativeWorkspace.outlineEpisodeUpdates = {
                        episodeNumber: Number(episodeNumber),
                        records: this.projectEpisodeUpdateRecords(episodeNumber),
                    };
                    this.modal('outlineEpisodeUpdatesModal')?.show();
                },

                projectHasGeneratedScripts() {
                    const episodes = this.creativeWorkspace.project?.episodes || [];
                    return episodes.some(item => item.stage === 'script'
                        && this.projectEpisodeScript(item.episode_number));
                },

                mergedScriptDownloadUrl() {
                    const projectId = this.creativeWorkspace.project?.id || '';
                    return `/api/creative/projects/${projectId}/scripts/download`;
                },

                creativeStageLabel(stage) {
                    return {outline: '分集大纲', beats: 'Beats', script: '场次剧本'}[stage] || stage;
                },

                openEpisodeDownload(stage) {
                    const available = this.projectStageEpisodeItems(stage)
                        .map(item => Number(item.episode_number))
                        .filter(number => number > 0);
                    if (!available.length) {
                        this.showCreativeNotice(`尚无可下载的${this.creativeStageLabel(stage)}`, 'error');
                        return;
                    }
                    const selected = Number(this.creativeWorkspace.selectedEpisode);
                    const initial = selected > 0 && available.includes(selected) ? [selected] : [...available];
                    this.creativeWorkspace.episodeDownload = {
                        stage,
                        selectedEpisodes: initial,
                        allEpisodes: initial.length === available.length,
                        format: stage === 'script' ? 'txt' : 'md',
                    };
                    this.modal('creativeEpisodeDownloadModal')?.show();
                },

                episodeDownloadAvailableNumbers() {
                    const stage = this.creativeWorkspace.episodeDownload?.stage;
                    return this.projectStageEpisodeItems(stage)
                        .map(item => Number(item.episode_number))
                        .filter(number => number > 0);
                },

                toggleEpisodeDownloadAll() {
                    const state = this.creativeWorkspace.episodeDownload;
                    state.selectedEpisodes = state.allEpisodes
                        ? [...this.episodeDownloadAvailableNumbers()]
                        : [];
                },

                syncEpisodeDownloadAll() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const available = this.episodeDownloadAvailableNumbers();
                    const selected = new Set((state.selectedEpisodes || []).map(Number));
                    state.allEpisodes = available.length > 0 && available.every(number => selected.has(number));
                },

                episodeDownloadActionLabel() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const count = (state.selectedEpisodes || []).length;
                    return state.allEpisodes ? `下载全集（${count} 集）` : `下载已选 ${count} 集`;
                },

                downloadSelectedEpisodes() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const selected = [...new Set((state.selectedEpisodes || []).map(Number))]
                        .filter(number => number > 0)
                        .sort((left, right) => left - right);
                    if (!state.stage || !selected.length) return;
                    const params = new URLSearchParams({
                        stage: state.stage,
                        episodes: state.allEpisodes ? 'all' : selected.join(','),
                        format: state.format,
                    });
                    this.modal('creativeEpisodeDownloadModal')?.hide();
                    window.location.assign(
                        `/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/download?${params.toString()}`
                    );
                },

                scriptBlockDisplayText(block) {
                    const type = String(block?.type || '');
                    const text = String(block?.text || '');
                    const speaker = String(block?.speaker || '');
                    const modifier = String(block?.modifier || '');
                    if (type === 'dialogue') return `${speaker}${modifier ? `（${modifier}）` : ''}：${text}`;
                    if (type === 'voiceover') return `画外音${speaker ? `（${speaker}）` : ''}：${text}`;
                    if (type === 'inner_voice') return `${speaker || '人物'}（内心）：${text}`;
                    if (type === 'cut') return `【切镜】${text}`;
                    return `△ ${text}`;
                },

                async startCreativeGeneration(url, body = {}, action = '', options = {}) {
                    if (!this.isFreeCreativeProject()) {
                        const modelRecordId = String(
                            this.creativeWorkspace.sequentialGeneration?.modelRecordId || ''
                        ).trim();
                        if (modelRecordId) body = {...body, llm_model_record_id: modelRecordId};
                    }
                    this.creativeWorkspace.error = '';
                    this.creativeWorkspace.startingAction = action;
                    this.creativeWorkspace.jobMessage = '正在创建生成任务';
                    this.creativeWorkspace.jobId = '';
                    this.creativeWorkspace.jobStatus = '';
                    this.creativeWorkspace.jobProgress = {indeterminate: true};
                    this.creativeWorkspace.jobStartedAt = Date.now();
                    this.creativeWorkspace.jobElapsedSeconds = 0;
                    this.creativeWorkspace.paused = false;
                    this.creativeWorkspace.notifiedStatus = '';
                    this.creativeWorkspace.jobLogs = [];
                    this.creativeWorkspace.jobLogCount = 0;
                    this.creativeWorkspace.showJobLogs = false;
                    this.creativeWorkspace.activeAction = action;
                    this.creativeWorkspace.activeSeriesGeneration = Boolean(options.series);
                    this.creativeWorkspace.activeBatchId = String(options.batchId || '');
                    this.creativeWorkspace.activeEpisode = ['beats', 'script'].includes(action)
                        && !this.creativeWorkspace.activeSeriesGeneration
                        ? Number(this.creativeWorkspace.selectedEpisode || 0)
                        : 0;
                    this.creativeGenerationProgressModal.show();
                    try {
                        const projectId = this.creativeWorkspace.project?.id;
                        const requestKey = `creative:generate:${projectId || 'unknown'}:${action}`;
                        const res = await (window.aiworkerUi?.requestKey || (async (_, fn) => fn()))(requestKey, async (signal) => {
                            return aiworkerApi.request(url, {
                                method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal
                            });
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '创建生成任务失败');
                        this.creativeWorkspace.jobId = data.job.job_id;
                        this.creativeWorkspace.jobStatus = data.job.status;
                        this.creativeWorkspace.jobMessage = this.creativeGenerationStartedMessage(action);
                        this.creativeWorkspace.jobProgress = data.job.progress || {};
                        this.creativeWorkspace.jobLogCount = Number(data.job.log_count || 0);
                        if (Number(data.job.started_at || 0) > 0) {
                            this.creativeWorkspace.jobStartedAt = Number(data.job.started_at) * 1000;
                        }
                        await this.fetchJobs();
                    } catch (e) {
                        if (window.aiworkerApi?.isAbortError(e)) return;
                        this.creativeWorkspace.error = e.message || '创建生成任务失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    } finally {
                        this.creativeWorkspace.startingAction = '';
                    }
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

                async openFreeCreativeGeneration(stage, options = {}) {
                    if (!this.isFreeCreativeProject() || !this.freeStageCan(stage, 'can_generate')) return;
                    const state = this.creativeWorkspace.freeGeneration;
                    Object.assign(state, {
                        stage, series: Boolean(options.series), instruction: '', contextOptions: {},
                        count: Number(this.creativeWorkspace.directionCount || 3),
                        modelOptions: [], modelRecordId: '',
                        contextRefs: {brief_id: null, character_batch_id: '', outline_batch_id: '', beats_id: null},
                        loading: true, submitting: false, error: ''
                    });
                    this.freeCreativeGenerationModal?.show();
                    try {
                        const query = ['beats', 'script'].includes(stage) && !state.series
                            ? `?episode_number=${Number(this.creativeWorkspace.selectedEpisode || 1)}` : '';
                        const [contextResponse, modelResponse] = await Promise.all([
                            aiworkerApi.request(
                                `/api/creative/projects/${this.creativeWorkspace.project.id}/free/context-options${query}`
                            ),
                            aiworkerApi.request('/api/llm/available-models'),
                        ]);
                        const [contextData, modelData] = await Promise.all([
                            contextResponse.json(), modelResponse.json(),
                        ]);
                        if (!contextResponse.ok) throw new Error(contextData.detail || '读取可引用版本失败');
                        if (!modelResponse.ok) throw new Error(modelData.detail || '读取可用模型失败');
                        state.contextOptions = contextData;
                        state.modelOptions = Array.isArray(modelData.items) ? modelData.items : [];
                        state.modelRecordId = String(modelData.default_model_record_id || '');
                    } catch (error) {
                        state.error = error.message || '读取生成配置失败';
                    } finally {
                        state.loading = false;
                    }
                },

                freeContextFieldAllowed(field) {
                    const state = this.creativeWorkspace.freeGeneration;
                    const stage = state.stage;
                    if (state.series && field === 'beats_id') return false;
                    return {
                        brief_id: [],
                        character_batch_id: ['outline', 'beats', 'script'],
                        outline_batch_id: ['beats', 'script'],
                        beats_id: ['script'],
                    }[field]?.includes(stage) || (field === 'brief_id' && ['characters', 'outline', 'beats', 'script'].includes(stage));
                },

                async submitFreeCreativeGeneration() {
                    const state = this.creativeWorkspace.freeGeneration;
                    const stage = state.stage;
                    if (!stage || state.submitting) return;
                    state.submitting = true;
                    state.error = '';
                    const episode = Number(this.creativeWorkspace.selectedEpisode || 1);
                    const series = ['beats', 'script'].includes(stage) && Boolean(state.series);
                    const plural = {brief: 'briefs', characters: 'characters', outline: 'outlines', beats: 'beats', script: 'scripts'}[stage];
                    const query = ['beats', 'script'].includes(stage) && !series ? `?episode_number=${episode}` : '';
                    const refs = {};
                    Object.entries(state.contextRefs || {}).forEach(([key, value]) => {
                        if (this.freeContextFieldAllowed(key) && value !== null && value !== '') refs[key] = value;
                    });
                    try {
                        this.freeCreativeGenerationModal?.hide();
                        const operationId = globalThis.crypto?.randomUUID?.() || `free-generate-${Date.now()}`;
                        const payload = {
                            operation_id: operationId,
                            generation_instruction: String(state.instruction || '').trim(),
                            context_refs: refs,
                        };
                        const modelRecordId = String(state.modelRecordId || '').trim();
                        if (modelRecordId) payload.llm_model_record_id = modelRecordId;
                        if (stage === 'brief') payload.count = Number(state.count || 3);
                        if (series) {
                            payload.expected_head_revisions = Object.fromEntries(
                                Array.from(
                                    {length: Number(this.creativeWorkspace.project.target_episode_count || 0)},
                                    (_, index) => index + 1
                                ).map(number => [number, this.creativeHeadRevision(stage, number)])
                            );
                        } else {
                            payload.expected_head_revision = this.creativeHeadRevision(
                                stage, ['beats', 'script'].includes(stage) ? episode : 0
                            );
                        }
                        await this.startCreativeGeneration(
                            series
                                ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/${plural}/generate-series`
                                : `/api/creative/projects/${this.creativeWorkspace.project.id}/free/${plural}/generate${query}`,
                            payload,
                            this.freeStageAction(stage),
                            series ? {series: true} : {}
                        );
                    } catch (error) {
                        state.error = error.message || '启动自由生成失败';
                        this.freeCreativeGenerationModal?.show();
                    } finally {
                        state.submitting = false;
                    }
                },

                async createFreeBlankResource(stage) {
                    if (!this.isFreeCreativeProject() || !this.freeStageCan(stage, 'can_create_blank')) return;
                    const project = this.creativeWorkspace.project;
                    const episode = Number(this.creativeWorkspace.selectedEpisode || 1);
                    const expected = this.creativeHeadRevision(
                        stage, ['beats', 'script'].includes(stage) ? episode : 0
                    );
                    const endpoints = {
                        brief: [`/api/creative/projects/${project.id}/free/briefs`, {content: {title: project.name || '', logline: ''}, expected_head_revision: expected}],
                        characters: [`/api/creative/projects/${project.id}/free/characters`, {characters: [], relationships: [], expected_head_revision: expected}],
                        outline: [`/api/creative/projects/${project.id}/free/outlines`, {episodes: [], expected_head_revision: expected}],
                        beats: [`/api/creative/projects/${project.id}/free/episodes/${episode}/beats`, {content: {beats: [], updates: []}, expected_head_revision: expected}],
                        script: [`/api/creative/projects/${project.id}/free/episodes/${episode}/scripts`, {content: {episode_title: '', scenes: []}, expected_head_revision: expected}],
                    };
                    const target = endpoints[stage];
                    if (!target) return;
                    try {
                        const response = await aiworkerApi.request(target[0], {
                            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(target[1])
                        });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建空白版本失败');
                        await this.selectCreativeProject(project.id);
                        this.showCreativeNotice('已创建空白版本');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '创建空白版本失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    }
                },

                async createFreeResourceRevision(stage) {
                    if (!this.isFreeCreativeProject() || !this.freeStageCan(stage, 'can_create_revision')) return;
                    try {
                        const project = this.creativeWorkspace.project;
                        const episode = Number(this.creativeWorkspace.selectedEpisode || 1);
                        const episodeScoped = ['beats', 'script'].includes(stage);
                        const headRef = this.freeStageHeadRef(stage, episodeScoped ? episode : 0);
                        const lockedStatuses = new Set(['brief_locked', 'locked', 'approved']);
                        let sourceItems = [];
                        let target = null;
                        if (stage === 'brief') {
                            const item = (project.blueprints || []).find(value => String(value.id) === headRef);
                            if (item) sourceItems = [item];
                            target = [`/api/creative/projects/${project.id}/free/briefs`, {
                                content: this.cloneCreativeJson(item?.content || {}),
                            }];
                        } else if (stage === 'characters') {
                            const characters = (project.characters || []).filter(item => String(item.batch_id) === headRef);
                            const relationships = (project.relationships || []).filter(item => String(item.batch_id) === headRef);
                            sourceItems = [...characters, ...relationships];
                            target = [`/api/creative/projects/${project.id}/free/characters`, {
                                characters: characters.map(item => ({
                                    character_key: item.character_key,
                                    content: this.cloneCreativeJson(item.content || {}),
                                })),
                                relationships: relationships.map(item => ({
                                    relationship_key: item.relationship_key,
                                    content: this.cloneCreativeJson(item.content || {}),
                                })),
                            }];
                        } else if (stage === 'outline') {
                            const outlines = (project.episodes || [])
                                .filter(item => item.stage === 'outline' && String(item.batch_id) === headRef)
                                .sort((left, right) => Number(left.episode_number) - Number(right.episode_number));
                            const firstContent = outlines[0]?.content || {};
                            sourceItems = outlines;
                            target = [`/api/creative/projects/${project.id}/free/outlines`, {
                                episodes: outlines.map(item => this.cloneCreativeJson(item.content || {})),
                                series_arc: String(firstContent.series_arc || ''),
                                series_overview: firstContent.series_overview
                                    ? this.cloneCreativeJson(firstContent.series_overview) : null,
                            }];
                        } else {
                            const item = (project.episodes || []).find(value =>
                                value.stage === stage
                                && Number(value.episode_number) === episode
                                && String(value.id) === headRef
                            );
                            if (item) sourceItems = [item];
                            const suffix = stage === 'beats' ? 'beats' : 'scripts';
                            target = [`/api/creative/projects/${project.id}/free/episodes/${episode}/${suffix}`, {
                                content: this.cloneCreativeJson(item?.content || {}),
                            }];
                        }
                        if (!target || !sourceItems.length || !sourceItems.every(item => lockedStatuses.has(item.status))) {
                            this.showCreativeNotice('只有当前已锁定版本可以创建修订版', 'error');
                            return;
                        }
                        target[1].expected_head_revision = this.creativeHeadRevision(
                            stage, episodeScoped ? episode : 0
                        );
                        const response = await aiworkerApi.request(target[0], {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(target[1])
                        });
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建修订版失败');
                        await this.selectCreativeProject(project.id);
                        if (stage === 'brief') {
                            this.creativeWorkspace.selectedBlueprintId = Number(data.resource?.id || 0) || null;
                        }
                        this.showCreativeNotice(`已创建${this.freeStageLabel(stage)}修订版，原锁定版本保持不变`);
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '创建修订版失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    }
                },

                async openCreativeModeMigration() {
                    const project = this.creativeWorkspace.project;
                    if (!project || project.workflow_mode !== 'sequential') return;
                    const state = this.creativeWorkspace.modeMigration;
                    Object.assign(state, {preview: null, confirmed: false, loading: true, submitting: false, error: ''});
                    this.creativeModeMigrationModal?.show();
                    try {
                        const response = await aiworkerApi.request(`/api/creative/projects/${project.id}/mode-migration-preview`);
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取迁移预检失败');
                        state.preview = data;
                    } catch (error) {
                        state.error = error.message || '读取迁移预检失败';
                    } finally {
                        state.loading = false;
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

                async generateProjectBlueprints() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('brief');
                    try {
                        const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                        const body = {count: Number(this.creativeWorkspace.directionCount || 3), workflow_task_id: task.id};
                        if (this.creativeWorkspace.project.creation_mode === 'original') {
                            body.preserve_blueprint_ids = [...this.creativeWorkspace.preservedBlueprintIds];
                        }
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/blueprints/generate`,
                            body,
                            'blueprints'
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async deriveOriginalDirection(item = null) {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('brief');
                    const parent = item || this.selectedProjectBlueprint();
                    const instruction = String(this.creativeWorkspace.variantInstruction || '').trim();
                    if (!parent || !instruction) return;
                    try {
                        const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(
                            `/api/creative/blueprints/${parent.id}/derive`,
                            {instruction, workflow_task_id: task.id},
                            'blueprint_variant'
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectCharacters() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('characters');
                    try {
                        const task = await this.resolveCreativeWorkflowTask('characters', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/characters/generate`, {workflow_task_id: task.id}, 'characters');
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectOutlines() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('outline');
                    try {
                        const task = await this.resolveCreativeWorkflowTask('outline', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/outlines/generate`, {workflow_task_id: task.id}, 'outlines');
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectBeats() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('beats');
                    try {
                        const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                        const task = await this.resolveCreativeWorkflowTask('beats', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/beats/generate`, {workflow_task_id: task.id}, 'beats');
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                isCompatibleProjectSeriesEpisode(stage, item) {
                    const project = this.creativeWorkspace.project;
                    const refs = item?.upstream_refs || {};
                    const episode = Number(item?.episode_number || 0);
                    if (!project || !episode || refs.outline_batch_id !== project.outline_batch_id) return false;
                    if (stage === 'beats') {
                        const outline = (project.episodes || []).find(candidate =>
                            candidate.stage === 'outline'
                            && candidate.batch_id === project.outline_batch_id
                            && Number(candidate.episode_number) === episode
                            && ['locked', 'approved'].includes(candidate.status)
                        );
                        return Boolean(outline
                            && Number(refs.outline_id) === Number(outline.id)
                            && Number(refs.outline_version) === Number(outline.version));
                    }
                    if (stage === 'script') {
                        const beats = this.lockedProjectEpisodeBeats(episode);
                        return Boolean(beats
                            && Number(refs.beats_id) === Number(beats.id)
                            && Number(refs.beats_version) === Number(beats.version));
                    }
                    return false;
                },

                hasResumableProjectSeries(stage) {
                    return (this.creativeWorkspace.project?.episodes || []).some(item =>
                        item.stage === stage
                        && item.status === 'draft'
                        && item.generation_meta?.series_generation
                        && this.isCompatibleProjectSeriesEpisode(stage, item)
                    );
                },

                seriesGenerationActionLabel(stage) {
                    return this.hasResumableProjectSeries(stage) ? '继续全剧生成' : '全剧批量生成';
                },

                async generateProjectBeatsSeries() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('beats', {series: true});
                    try {
                        const task = await this.resolveCreativeWorkflowTask('beats', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/beats/generate-series`,
                            {workflow_task_id: task.id}, 'beats', {series: true}
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectScript() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('script');
                    try {
                        if (!this.currentEpisodeHasLockedBeats()) {
                            throw new Error('请先生成并完成当前集 Beats，再生成场次剧本');
                        }
                        const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                        const task = await this.resolveCreativeWorkflowTask('script', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/scripts/generate`, {workflow_task_id: task.id}, 'script');
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                lockedProjectBeatsProgress() {
                    const project = this.creativeWorkspace.project;
                    const required = Number(project?.target_episode_count || 0);
                    if (project?.workflow_mode === 'free') {
                        const locked = new Set((project.episodes || []).filter(item =>
                            item.stage === 'beats'
                            && ['locked', 'approved'].includes(item.status)
                            && String(this.creativeHead('beats', item.episode_number)?.resource_ref || '') === String(item.id)
                        ).map(item => Number(item.episode_number)));
                        return {required, locked: locked.size, missing: []};
                    }
                    const locked = new Set((project?.episodes || []).filter(item =>
                        item.stage === 'beats' && ['locked', 'approved'].includes(item.status)
                        && item.upstream_refs?.outline_batch_id === project?.outline_batch_id
                    ).map(item => Number(item.episode_number)));
                    return {
                        required, locked: locked.size,
                        missing: Array.from({length: required}, (_, index) => index + 1)
                            .filter(number => !locked.has(number)),
                    };
                },

                canGenerateProjectScriptsSeries() {
                    const progress = this.lockedProjectBeatsProgress();
                    return progress.required > 0 && progress.missing.length === 0;
                },

                projectScriptsSeriesBlockReason() {
                    if (this.isFreeCreativeProject()) return '';
                    const progress = this.lockedProjectBeatsProgress();
                    if (!progress.required) return '请先锁定分集大纲';
                    return progress.missing.length
                        ? `需先锁定全部 Beats，缺少第 ${progress.missing.join('、')} 集`
                        : '';
                },

                async generateProjectScriptsSeries() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('script', {series: true});
                    try {
                        if (!this.canGenerateProjectScriptsSeries()) throw new Error(this.projectScriptsSeriesBlockReason());
                        const task = await this.resolveCreativeWorkflowTask('script', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/scripts/generate-series`,
                            {workflow_task_id: task.id}, 'script', {series: true}
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                openProjectScriptWorkspace() {
                    if (Number(this.creativeWorkspace.selectedEpisode) === 0) {
                        window.location.assign(this.creativeModuleUrl('script', 0));
                        return;
                    }
                    if (!this.currentEpisodeHasLockedBeats()) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {
                            detail: {message: '请先生成并完成当前集 Beats，再生成场次剧本', type: 'error'}
                        }));
                        return;
                    }
                    const projectId = this.creativeWorkspace.project?.id;
                    const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                    window.location.assign(`/writing/projects/${projectId}/episodes/${episode}/scripts`);
                },

                creativeActionLabel(action) {
                    return {
                        blueprints: '仿制方向', characters: '人物关系', outlines: '分集大纲',
                        outline_overview: '全集剧情概览', beats: 'Beats 脚本', script: '场次剧本'
                    }[action] || '创作内容';
                },

                creativeGenerationStartedMessage(action) {
                    return {
                        blueprints: '仿制方向生成任务已发起，正在后台处理',
                        characters: '人物关系生成任务已发起，正在后台处理',
                        outlines: '分集大纲生成任务已发起，正在后台处理',
                        outline_overview: '全集剧情概览生成任务已发起，正在后台处理',
                        beats: 'Beats 脚本生成任务已发起，正在后台处理', script: '场次剧本生成任务已发起，正在后台处理'
                    }[action] || '创作内容生成任务已发起，正在后台处理';
                },

                creativeServerRunningJob(action, options = {}) {
                    const projectId = String(this.creativeWorkspace.project?.id || '');
                    const stage = {
                        blueprints: 'brief', blueprint_variant: 'brief',
                        characters: 'characters',
                        outlines: 'outline', outline_overview: 'outline',
                        beats: 'beats', script: 'script',
                    }[action];
                    if (!projectId || !stage) return null;
                    const selectedEpisode = Number(this.creativeWorkspace.selectedEpisode || 0);
                    return (this.jobs || []).find(job => {
                        const extra = job.job?.extra || {};
                        const jobStage = {
                            blueprints: 'brief', blueprint_variant: 'brief',
                            characters: 'characters',
                            outlines: 'outline', outline_overview: 'outline',
                            beats: 'beats', script: 'script',
                        }[String(extra.action || '')];
                        if (
                            job.platform !== 'creative'
                            || job.crawler_type !== 'phase4_generate'
                            || !['pending', 'running', 'stopping'].includes(job.status)
                            || String(job.project_id ?? extra.project_id ?? '') !== projectId
                            || jobStage !== stage
                        ) return false;
                        if (!['beats', 'script'].includes(stage)) return true;
                        if (options.series) return true;
                        const start = Number(extra.episode_start ?? extra.episode_number ?? 0);
                        const end = Number(extra.episode_end ?? extra.episode_number ?? start);
                        return selectedEpisode > 0 && start <= selectedEpisode && selectedEpisode <= end;
                    }) || null;
                },

                creativeActionRunning(action) {
                    if (this.creativeWorkspace.startingAction === action) return true;
                    if (this.creativeServerRunningJob(action)) return true;
                    const isActiveRunning = this.creativeWorkspace.activeAction === action && this.creativeJobRunning();
                    if (!isActiveRunning) return false;
                    if (this.creativeWorkspace.activeSeriesGeneration) return true;
                    return !['beats', 'script'].includes(action)
                        || Number(this.creativeWorkspace.activeEpisode) === Number(this.creativeWorkspace.selectedEpisode);
                },

                creativeActionDisabled(action, options = {}) {
                    if (this.creativeWorkspace.startingAction) return true;
                    if (this.isFreeCreativeProject()) {
                        const freeStage = {
                            blueprints: 'brief',
                            characters: 'characters',
                            outlines: 'outline',
                            beats: 'beats',
                            script: 'script',
                        }[action];
                        if (freeStage && !this.freeStageCan(freeStage, 'can_generate')) return true;
                    }
                    if (this.creativeServerRunningJob(action, options)) return true;
                    const isActiveRunning = this.creativeWorkspace.activeAction === action && this.creativeJobRunning();
                    if (!isActiveRunning) return false;
                    if (options.series) return true;
                    if (this.creativeWorkspace.activeSeriesGeneration) return true;
                    return !['beats', 'script'].includes(action)
                        || Number(this.creativeWorkspace.activeEpisode) === Number(this.creativeWorkspace.selectedEpisode);
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

                async syncCreativeJobProgress() {
                    if (!this.creativeWorkspace.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
                    if (!job) return;
                    this.creativeWorkspace.jobStatus = job.status;
                    this.creativeWorkspace.jobMessage = job.message || this.creativeWorkspace.jobMessage;
                    this.creativeWorkspace.jobProgress = job.progress || {};
                    this.creativeWorkspace.paused = Boolean(job.paused);
                    this.creativeWorkspace.jobLogCount = Number(job.log_count || 0);
                    if (this.creativeWorkspace.showJobLogs) await this.syncCreativeJobLogs();
                    if (Number(job.started_at || 0) > 0) {
                        this.creativeWorkspace.jobStartedAt = Number(job.started_at) * 1000;
                    }
                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
                        Number(job.progress?.elapsed_seconds || 0)
                    );
                    // 先尝试通过项目数据尽早检测完成（job 可能还在 running 但结果已入库）
                    if (await this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
                    // job 已到终态：先刷新项目数据（让后端生成的结果可见），再尝试 reconcile
                    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
                        if (this.creativeWorkspace.notifiedStatus === job.status) return;
                        this.creativeWorkspace.notifiedStatus = job.status;
                        if (this.creativeWorkspace.project?.id) {
                            await this.selectCreativeProject(this.creativeWorkspace.project.id);
                            if (await this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
                        }
                        if (job.status === 'succeeded') {
                            this.finishCreativeGeneration(job.message || '生成完成，结果已回填到当前项目');
                            this.showCreativeNotice('生成完成，项目内容已刷新。');
                        } else {
                            this.creativeWorkspace.startingAction = '';
                            this.creativeWorkspace.activeAction = '';
                            this.creativeWorkspace.activeEpisode = 0;
                            this.creativeWorkspace.activeSeriesGeneration = false;
                            this.creativeWorkspace.activeBatchId = '';
                            this.creativeWorkspace.paused = false;
                            alert(`创作任务${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
                        }
                    }
                },

                creativeJobStatusRunning() {
                    return ['pending', 'running', 'stopping'].includes(this.creativeWorkspace.jobStatus);
                },
                creativeJobRunning() {
                    if (!this.creativeJobStatusRunning()) return false;
                    return !this.creativeProjectHasJobResult(
                        this.creativeWorkspace.project,
                        this.creativeWorkspace.activeAction,
                        this.creativeWorkspace.jobId
                    );
                },
                creativeJobRetryable() {
                    const status = this.creativeWorkspace.jobStatus;
                    if (!['failed', 'cancelled'].includes(status)) return false;
                    const currentJob = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
                    const extra = currentJob?.job?.extra || {};
                    if (!String(extra.operation_id || '').trim()) return true;
                    return status === 'failed' && String(extra.workflow_mode || 'sequential') === 'sequential';
                },
                creativeProgressIndeterminate() {
                    const progress = this.creativeWorkspace.jobProgress || {};
                    return Boolean(this.creativeWorkspace.startingAction || this.creativeJobRunning())
                        && (progress.indeterminate || (Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1));
                },
                creativeProgressPercent() {
                    const progress = this.creativeWorkspace.jobProgress || {};
                    const total = Number(progress.total || 0);
                    return total ? Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total)) : 0;
                },
                creativeProgressText() {
                    const progress = this.creativeWorkspace.jobProgress || {};
                    if (this.creativeWorkspace.startingAction && !this.creativeWorkspace.jobId) return '正在启动';
                    if (this.creativeProgressIndeterminate()) return this.creativeWorkspace.paused ? '已暂停' : '处理中';
                    return progress.total ? `${progress.completed || 0}/${progress.total}` : this.creativeWorkspace.jobStatus;
                },
                creativeProgressElapsedText() {
                    const seconds = Number(this.creativeWorkspace.jobElapsedSeconds || 0);
                    const prefix = this.creativeWorkspace.startingAction || this.creativeJobRunning() ? '已等待' : '用时';
                    return `${prefix} ${this.formatCreativeGenerationElapsed(seconds)}`;
                },
                creativeBatchElapsedSeconds() {
                    if (!this.creativeWorkspace.jobProgress?.batch_in_flight) return 0;
                    const startedAt = Number(this.creativeWorkspace.jobProgress?.batch_started_at || 0) * 1000;
                    if (!startedAt) return 0;
                    return Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
                },
                creativeBatchProgressText() {
                    if (!this.creativeWorkspace.jobProgress?.batch_in_flight) return '';
                    const progress = this.creativeWorkspace.jobProgress || {};
                    const start = Number(progress.batch_start || 0);
                    const end = Number(progress.batch_end || 0);
                    const scope = progress.request_kind === 'series_arc'
                        ? '全剧阶段弧线'
                        : (start && end ? `第 ${start}–${end} 集` : '当前批次');
                    const effort = String(progress.reasoning_effort || '').trim();
                    const effortText = effort ? ` · ${effort}` : '';
                    return `${scope}${effortText} · 已等待 ${this.formatCreativeGenerationElapsed(this.creativeBatchElapsedSeconds())}`;
                },
                creativeBatchSlow() {
                    return Boolean(this.creativeWorkspace.jobProgress?.batch_in_flight)
                        && this.creativeBatchElapsedSeconds() >= 720;
                },
                formatCreativeGenerationElapsed(seconds) {
                    const value = Math.max(0, Math.floor(Number(seconds || 0)));
                    const minutes = Math.floor(value / 60);
                    const remaining = value % 60;
                    return minutes ? `${minutes}分${remaining}秒` : `${remaining}秒`;
                },
                tickCreativeGenerationElapsed() {
                    if (!this.creativeWorkspace.startingAction && !this.creativeJobRunning()) return;
                    const startedAt = Number(this.creativeWorkspace.jobStartedAt || 0);
                    if (!startedAt) return;
                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
                        Math.floor((Date.now() - startedAt) / 1000)
                    );
                },

                async openCreativeTaskLog() {
                    if (!this.creativeWorkspace.jobId) return;
                    this.creativeWorkspace.showJobLogs = true;
                    this.creativeGenerationProgressModal.show();
                    await this.syncCreativeJobLogs();
                    this.$nextTick(() => {
                        this.$refs.creativeGenerationLogPane?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
                    });
                },

                async syncCreativeJobLogs() {
                    const jobId = this.creativeWorkspace.jobId;
                    if (!jobId) return;
                    try {
                        const res = await aiworkerApi.request(`/api/jobs/${jobId}/logs`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取任务日志失败');
                        this.creativeWorkspace.jobLogs = data.logs || [];
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '读取任务日志失败';
                    }
                },

                async pauseCreativeJob() {
                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/pause`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '暂停失败'; return; }
                    this.creativeWorkspace.paused = true;
                },

                async resumeCreativeJob() {
                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/resume`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.message || data.detail || '继续失败'; return; }
                    this.creativeWorkspace.paused = false;
                },

                async cancelCreativeJob() {
                    if (!confirm('确定取消当前创作任务吗？尚未完成的版本不会写入项目。')) return;
                    await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/stop`, {method: 'POST'});
                    await this.fetchJobs();
                },

                async retryCreativeJob() {
                    if (this.creativeWorkspace.retrying) return;
                    const currentJob = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
                    const currentExtra = currentJob?.job?.extra || {};
                    const operationBound = Boolean(String(currentExtra.operation_id || '').trim());
                    if (operationBound && (
                        this.creativeWorkspace.jobStatus !== 'failed'
                        || String(currentExtra.workflow_mode || 'sequential') !== 'sequential'
                    )) return;
                    const projectId = Number(currentExtra.project_id || this.creativeWorkspace.project?.id || 0);
                    const retryUrl = operationBound
                        ? `/api/creative/projects/${projectId}/generation-jobs/${this.creativeWorkspace.jobId}/retry`
                        : `/api/jobs/${this.creativeWorkspace.jobId}/retry`;
                    this.creativeWorkspace.error = '';
                    this.creativeWorkspace.retrying = true;
                    try {
                        const res = await aiworkerApi.request(retryUrl, {method: 'POST'});
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '重试失败');
                        const retryExtra = data.job?.job?.extra || currentExtra;
                        this.creativeWorkspace.jobId = data.job.job_id;
                        this.creativeWorkspace.jobStatus = data.job.status;
                        this.creativeWorkspace.activeAction = String(retryExtra.action || this.creativeWorkspace.activeAction || '');
                        this.creativeWorkspace.activeSeriesGeneration = Boolean(retryExtra.series);
                        this.creativeWorkspace.activeEpisode = this.creativeWorkspace.activeSeriesGeneration
                            ? 0
                            : Number(retryExtra.episode_number || 0);
                        this.creativeWorkspace.jobMessage = `${this.creativeActionLabel(this.creativeWorkspace.activeAction)}重试任务已发起，正在后台处理`;
                        this.creativeWorkspace.jobProgress = {indeterminate: true};
                        this.creativeWorkspace.jobStartedAt = Number(data.job.started_at || 0) > 0 ? Number(data.job.started_at) * 1000 : Date.now();
                        this.creativeWorkspace.jobElapsedSeconds = 0;
                        this.creativeWorkspace.paused = false;
                        this.creativeWorkspace.notifiedStatus = '';
                        this.creativeWorkspace.jobLogs = [];
                        this.creativeWorkspace.jobLogCount = Number(data.job.log_count || 0);
                        this.creativeWorkspace.showJobLogs = false;
                        this.creativeGenerationProgressModal.show();
                        await this.fetchJobs();
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '重试失败';
                    } finally {
                        this.creativeWorkspace.retrying = false;
                    }
                },

                async saveCreativeProjectSettings() {
                    const project = this.creativeWorkspace.project;
                    if (!project) return;
                    const brief = this.selectedProjectBlueprint();
                    const payload = {
                        name: project.name, genre: project.genre || '', target_platform: project.target_platform || '',
                        target_audience: project.target_audience || '', target_episode_count: Number(project.target_episode_count),
                        episode_duration_seconds: Number(project.episode_duration_seconds),
                        expected_project_revision: Number(project.revision),
                    };
                    if (!this.isFreeCreativeProject() && project.locked_brief_id && brief?.status === 'draft') {
                        payload.draft_brief_id = Number(brief.id);
                    }
                    const res = await aiworkerApi.request(`/api/creative/projects/${project.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存 Brief 参数失败'; return false; }
                        data.blueprints = (data.blueprints || []).map((item) => ({
                            ...item, content: this.normalizeBlueprintContent(item.content),
                        }));
                        this.creativeWorkspace.project = data;
                    await this.fetchCreativeProjects(data.id);
                    return true;
                },

                async saveProjectBlueprint() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    const url = this.isFreeCreativeProject()
                        ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/items/brief/${item.id}`
                        : `/api/creative/blueprints/${item.id}`;
                    const body = this.isFreeCreativeProject()
                        ? {content: item.content}
                        : {content: item.content, difference_note: item.difference_note || ''};
                    const res = await aiworkerApi.request(url, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存方向失败'; return false; }
                    Object.assign(item, data);
                    return true;
                },

                async createProjectBlueprintVersion() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    if (this.isFreeCreativeProject()) {
                        return this.createFreeResourceRevision('brief');
                    }
                    const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}/versions`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            workflow_task_id: task.id,
                            expected_project_revision: Number(this.creativeWorkspace.project.revision),
                        })
                    });
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建新版本失败'; return; }
                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
                    this.creativeWorkspace.selectedBlueprintId = data.id;
                },

                async completeProjectBrief() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    try {
                        if (!await this.saveCreativeProjectSettings()) {
                            throw new Error(this.creativeWorkspace.error || '保存项目参数失败');
                        }
                        if (!await this.saveProjectBlueprint()) {
                            throw new Error(this.creativeWorkspace.error || '保存方向失败');
                        }
                        if (this.isFreeCreativeProject()) {
                            await this.lockFreeCreativeResource('brief', String(item.id));
                        } else {
                            await this.completeCreativeResource('brief', item.id, this.creativeWorkspace.project.id, item.difference_note);
                        }
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('Brief 已完成并锁定');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '完成 Brief 失败';
                    }
                },

                async createBlankCharacterBatch() {
                    const projectId = this.creativeWorkspace.project.id;
                    if (this.isFreeCreativeProject()) return this.createFreeBlankResource('characters');
                    const task = await this.resolveCreativeWorkflowTask('characters', projectId);
                    const blank = {name: '新角色', age_range: '', identity: '', external_tags: [], desire: '', secret: '', growth_arc: '', dialogue_style: '', appearance_prompt: '', voice_prompt: '', action_goal: '', fear_of_loss: '', behavioral_contradiction: '', pressure_response: '', bottom_line: ''};
                    const res = await aiworkerApi.request(`/api/creative/projects/${projectId}/characters`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({characters: [blank], relationships: [], workflow_task_id: task.id})});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建空白人物版本失败'; return; }
                    await this.selectCreativeProject(projectId);
                },

                async saveProjectCharacter(item) {
                    const url = this.isFreeCreativeProject()
                        ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/items/character/${item.id}`
                        : `/api/creative/characters/${item.id}`;
                    await this.saveCreativeItem(url, item, '人物卡已保存', `character-${item.id}`);
                },
                async saveProjectRelationship(item) {
                    const url = this.isFreeCreativeProject()
                        ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/items/relationship/${item.id}`
                        : `/api/creative/relationships/${item.id}`;
                    await this.saveCreativeItem(url, item, '人物关系已保存', `relationship-${item.id}`);
                },
                async saveProjectEpisode(item) {
                    const url = this.isFreeCreativeProject()
                        ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/items/episode/${item.id}`
                        : `/api/creative/episodes/${item.id}`;
                    const saved = await this.saveCreativeItem(url, item, '分集内容已保存', `episode-${item.id}`);
                    if (saved && item.stage === 'script') {
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        await this.refreshCreativeTopicLibrary();
                    }
                },
                async saveCreativeItem(url, item, successMessage, saveKey) {
                    this.creativeWorkspace.error = '';
                    this.creativeWorkspace.saveState[saveKey] = 'saving';
                    try {
                        const res = await aiworkerApi.request(url, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content})});
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '保存失败');
                        Object.assign(item, data);
                        this.creativeWorkspace.saveState[saveKey] = 'saved';
                        this.showCreativeNotice(successMessage || '保存成功');
                        setTimeout(() => {
                            if (this.creativeWorkspace.saveState[saveKey] === 'saved') this.creativeWorkspace.saveState[saveKey] = '';
                        }, 1800);
                        return data;
                    } catch (e) {
                        this.creativeWorkspace.saveState[saveKey] = '';
                        this.creativeWorkspace.error = `${successMessage || '保存'}失败：${e.message || '请稍后重试'}`;
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                        return null;
                    }
                },

                async completeProjectCharacters() {
                    const projectId = this.creativeWorkspace.project.id;
                    const batchId = this.latestCharacterBatchId();
                    if (!batchId) return;
                    try {
                        if (this.isFreeCreativeProject()) await this.lockFreeCreativeResource('characters', batchId);
                        else await this.completeCreativeResource('characters', `${projectId}:${batchId}`, projectId);
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice('人物关系已完成并锁定');
                    } catch (error) { this.creativeWorkspace.error = error.message || '完成人物关系失败'; }
                },

                async completeProjectOutlines() {
                    const projectId = this.creativeWorkspace.project.id;
                    const batchId = this.latestOutlineBatchId();
                    if (!batchId) return;
                    try {
                        if (this.isFreeCreativeProject()) await this.lockFreeCreativeResource('outline', batchId);
                        else await this.completeCreativeResource('outline', `${projectId}:${batchId}`, projectId);
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice('分集大纲已完成并锁定');
                    } catch (error) { this.creativeWorkspace.error = error.message || '完成分集大纲失败'; }
                },

                async completeProjectBeats() {
                    const item = this.selectedProjectBeats();
                    if (!item) return;
                    try {
                        if (this.isFreeCreativeProject()) await this.lockFreeCreativeResource('beats', String(item.id), item.episode_number);
                        else await this.completeCreativeResource('beats', item.id, this.creativeWorkspace.project.id);
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('Beats 已完成并锁定');
                    } catch (error) { this.creativeWorkspace.error = error.message || '完成 Beats 失败'; }
                },

                async completeProjectScript() {
                    const item = this.selectedProjectScript();
                    if (!item || !this.isFreeCreativeProject()) return;
                    try {
                        await this.lockFreeCreativeResource('script', String(item.id), item.episode_number);
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('场次剧本已完成并锁定');
                    } catch (error) { this.creativeWorkspace.error = error.message || '完成场次剧本失败'; }
                },

                async lockFreeCreativeResource(stage, resourceRef, episodeNumber = 0) {
                    const projectId = this.creativeWorkspace.project.id;
                    const plural = {brief: 'briefs', characters: 'characters', outline: 'outlines', beats: 'beats', script: 'scripts'}[stage];
                    const suffix = ['beats', 'script'].includes(stage) ? `?episode_number=${Number(episodeNumber)}` : '';
                    const response = await aiworkerApi.request(
                        `/api/creative/projects/${projectId}/free/${plural}/${encodeURIComponent(resourceRef)}/lock${suffix}`,
                        {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({expected_head_revision: this.creativeHeadRevision(stage, episodeNumber)})
                        }
                    );
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || '完成并锁定失败');
                    return data;
                },

                async completeProjectBeatsBatch() {
                    if (this.isFreeCreativeProject()) return this.completeFreeProjectEpisodeSeries('beats');
                    const items = this.batchLockableProjectBeats();
                    if (!items.length) return;
                    const episodes = items.map((item) => Number(item.episode_number)).sort((a, b) => a - b);
                    const scope = episodes.length === 1 ? `第 ${episodes[0]} 集` : `第 ${episodes[0]}-${episodes.at(-1)} 集`;
                    if (!window.confirm(`将完成并锁定 ${scope}，共 ${episodes.length} 集当前 Beats 草稿。已锁定集会跳过，锁定后不能直接编辑。是否继续？`)) return;
                    try {
                        const projectId = this.creativeWorkspace.project.id;
                        const task = await this.resolveCreativeWorkflowTask('beats', projectId);
                        const response = await aiworkerApi.request(`/api/creative/projects/${projectId}/beats/lock`, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({workflow_task_id: task.id}),
                        });
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '批量锁定 Beats 失败');
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice(`已完成并锁定 ${payload.resources?.length || episodes.length} 集 Beats`);
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '批量锁定 Beats 失败';
                    }
                },

                async completeFreeProjectEpisodeSeries(stage) {
                    if (!this.isFreeCreativeProject() || !['beats', 'script'].includes(stage)) return;
                    const items = this.batchLockableProjectEpisodes(stage);
                    if (!items.length || !this.canBatchLockProjectEpisodes(stage)) return;
                    const label = stage === 'beats' ? 'Beats' : '场次剧本';
                    const total = Number(this.creativeWorkspace.project.target_episode_count || 0);
                    if (!window.confirm(`将完成并锁定全剧 ${total} 集当前 ${label}。已锁定集会跳过，锁定后不能直接编辑。是否继续？`)) return;
                    const expectedHeadRevisions = Object.fromEntries(
                        Array.from({length: total}, (_, index) => index + 1)
                            .map(number => [number, this.creativeHeadRevision(stage, number)])
                    );
                    const plural = stage === 'beats' ? 'beats' : 'scripts';
                    try {
                        const projectId = this.creativeWorkspace.project.id;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/free/${plural}/lock-series`,
                            {
                                method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({expected_head_revisions: expectedHeadRevisions}),
                            }
                        );
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || `全集${label}锁定失败`);
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice(`全集 ${total} 集${label}已完成并锁定`);
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || `全集${label}锁定失败`;
                    }
                },

                currentGeneratedResourceId(stage) {
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId) return '';
                    if (stage === 'brief') return String(this.selectedProjectBlueprint()?.id || '');
                    if (stage === 'characters') {
                        const batchId = this.latestCharacterBatchId();
                        return batchId ? `${projectId}:${batchId}` : '';
                    }
                    if (stage === 'outline') {
                        const batchId = this.latestOutlineBatchId();
                        return batchId ? `${projectId}:${batchId}` : '';
                    }
                    if (stage === 'beats') return String(this.selectedProjectBeats()?.id || '');
                    if (stage === 'script') return String(this.selectedProjectScript()?.id || '');
                    return '';
                },

                creativeGeneratedDownloadUrl(stage, format = 'md') {
                    const projectId = this.creativeWorkspace.project?.id;
                    const resourceId = this.currentGeneratedResourceId(stage);
                    if (!projectId || !resourceId) return '#';
                    const params = new URLSearchParams({stage, resource_id: resourceId, format});
                    return `/api/creative/projects/${projectId}/generated-content/download?${params.toString()}`;
                },

}));
