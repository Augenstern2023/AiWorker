window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async startCreativeGeneration(url, body = {}, action = '', options = {}) {
                    if (!this.isFreeCreativeProject() && !options.modelConfirmed) {
                        return this.openSequentialGenerationModel(url, body, action, options);
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
                    this.creativeWorkspace.showJobLogs = true;
                    this.creativeWorkspace.activeAction = action;
                    this.creativeWorkspace.activeSeriesGeneration = Boolean(options.series);
                    this.creativeWorkspace.activeBatchId = String(options.batchId || '');
                    this.creativeWorkspace.activeEpisode = ['beats', 'script'].includes(action)
                        && !this.creativeWorkspace.activeSeriesGeneration
                        ? Number(this.creativeWorkspace.selectedEpisode || 0)
                        : 0;
                    this.schedulePoller?.('creative-elapsed', () => this.tickCreativeGenerationElapsed(), 1000);
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
                        const job = data.job || {};
                        const jobId = job.job_id || job.job?.job_id;
                        if (!jobId) throw new Error('创建生成任务失败：响应缺少任务 ID');
                        const promptStage = this.promptStageForGenerationAction(action);
                        if (promptStage && body.prompt_template_key) {
                            this.clearPromptRunSelection(promptStage);
                        }
                        this.creativeWorkspace.jobId = jobId;
                        this.creativeWorkspace.jobStatus = job.status;
                        this.creativeWorkspace.jobMessage = this.creativeGenerationStartedMessage(action);
                        this.creativeWorkspace.jobProgress = job.progress || {};
                        this.creativeWorkspace.jobLogCount = Number(job.log_count || 0);
                        if (Number(job.started_at || 0) > 0) {
                            this.creativeWorkspace.jobStartedAt = Number(job.started_at) * 1000;
                        }
                        await this.syncCreativeJobLogs();
                        await this.fetchJobs();
                    } catch (e) {
                        if (window.aiworkerApi?.isAbortError(e)) {
                            this.stopPoller?.('creative-elapsed');
                            return;
                        }
                        this.creativeWorkspace.error = e.message || '创建生成任务失败';
                        this.stopPoller?.('creative-elapsed');
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    } finally {
                        this.creativeWorkspace.startingAction = '';
                    }
                },

                async openFreeCreativeGeneration(stage, options = {}) {
                    if (!this.isFreeCreativeProject() || !this.freeStageCan(stage, 'can_generate')) return;
                    const state = this.creativeWorkspace.freeGeneration;
                    const requestSequence = Number(state.requestSequence || 0) + 1;
                    Object.assign(state, {
                        stage, series: Boolean(options.series), instruction: '', contextOptions: {},
                        count: Number(this.creativeWorkspace.directionCount || 3),
                        modelOptions: [], modelRecordId: '', defaultModelRecordId: '',
                        reasoningEffortOptions: [], reasoningEffort: '',
                        contextRefs: {brief_id: null, character_batch_id: '', outline_batch_id: '', beats_id: null},
                        loading: true, submitting: false, error: '', requestSequence
                    });
                    this.freeCreativeGenerationModal?.show();
                    try {
                        const query = ['beats', 'script'].includes(stage) && !state.series
                            ? `?episode_number=${Number(this.creativeWorkspace.selectedEpisode || 1)}` : '';
                        const projectId = this.creativeWorkspace.project.id;
                        const requestKey = `creative:free-generation-config:${projectId}`;
                        const runRequest = window.aiworkerUi?.requestKey
                            || (async (_key, factory) => factory());
                        const [contextResponse, modelResponse, contextData, modelData] = await runRequest(
                            requestKey,
                            async (cancellationSignal) => {
                                const controller = new AbortController();
                                let timedOut = false;
                                const cancel = () => controller.abort();
                                if (cancellationSignal?.aborted) cancel();
                                else cancellationSignal?.addEventListener('abort', cancel, {once: true});
                                const timeoutId = globalThis.setTimeout(() => {
                                    timedOut = true;
                                    controller.abort();
                                }, 10000);
                                try {
                                    const [contextResult, modelResult] = await Promise.all([
                                        aiworkerApi.request(
                                            `/api/creative/projects/${projectId}/free/context-options${query}`,
                                            {signal: controller.signal}
                                        ),
                                        aiworkerApi.request('/api/llm/available-models', {
                                            signal: controller.signal,
                                        }),
                                    ]);
                                    const [contextPayload, modelPayload] = await Promise.all([
                                        contextResult.json(), modelResult.json(),
                                    ]);
                                    return [contextResult, modelResult, contextPayload, modelPayload];
                                } catch (error) {
                                    if (timedOut && error?.name === 'AbortError') {
                                        throw new Error('读取生成配置超时，请检查服务状态后重试');
                                    }
                                    throw error;
                                } finally {
                                    globalThis.clearTimeout(timeoutId);
                                    cancellationSignal?.removeEventListener('abort', cancel);
                                }
                            }
                        );
                        if (requestSequence !== state.requestSequence) return;
                        if (!contextResponse.ok) throw new Error(contextData.detail || '读取可引用版本失败');
                        if (!modelResponse.ok) throw new Error(modelData.detail || '读取可用模型失败');
                        state.contextOptions = contextData;
                        state.modelOptions = Array.isArray(modelData.items) ? modelData.items : [];
                        state.modelRecordId = String(modelData.default_model_record_id || '');
                        this.syncGenerationReasoningEfforts(
                            state, modelData.default_model_record_id
                        );
                    } catch (error) {
                        if (requestSequence !== state.requestSequence) return;
                        if (error?.name === 'AbortError') return;
                        state.error = error.message || '读取生成配置失败';
                    } finally {
                        if (requestSequence === state.requestSequence) state.loading = false;
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
                        Object.assign(payload, this.promptGenerationPayload(stage));
                        const reasoningEffort = String(state.reasoningEffort || '').trim();
                        const modelRecordId = reasoningEffort
                            ? this.selectedGenerationModelRecordId(state)
                            : String(state.modelRecordId || '').trim();
                        if (modelRecordId) payload.llm_model_record_id = modelRecordId;
                        if (reasoningEffort) payload.reasoning_effort = reasoningEffort;
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

                async generateProjectBlueprints() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('brief');
                    try {
                        const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                        const body = this.promptGenerationPayload('brief', {
                            count: Number(this.creativeWorkspace.directionCount || 3),
                            workflow_task_id: task.id,
                        });
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
                            this.promptGenerationPayload('brief', {
                                instruction, workflow_task_id: task.id,
                            }),
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
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/characters/generate`,
                            this.promptGenerationPayload('characters', {workflow_task_id: task.id}),
                            'characters'
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectOutlines() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('outline');
                    try {
                        const task = await this.resolveCreativeWorkflowTask('outline', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/outlines/generate`,
                            this.promptGenerationPayload('outline', {workflow_task_id: task.id}),
                            'outlines'
                        );
                    } catch (e) {
                        window.dispatchEvent(new CustomEvent('aiworker:toast', {detail: {message: e.message, type: 'error'}}));
                    }
                },

                async generateProjectBeats() {
                    if (this.isFreeCreativeProject()) return this.openFreeCreativeGeneration('beats');
                    try {
                        const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                        const task = await this.resolveCreativeWorkflowTask('beats', this.creativeWorkspace.project.id);
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/beats/generate`,
                            this.promptGenerationPayload('beats', {workflow_task_id: task.id}),
                            'beats'
                        );
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
                            this.promptGenerationPayload('beats', {workflow_task_id: task.id}), 'beats', {series: true}
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
                        return this.startCreativeGeneration(
                            `/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/scripts/generate`,
                            this.promptGenerationPayload('script', {workflow_task_id: task.id}),
                            'script'
                        );
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
                        return {
                            required, locked: locked.size,
                            missing: Array.from({length: required}, (_, index) => index + 1)
                                .filter(number => !locked.has(number)),
                        };
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
                            this.promptGenerationPayload('script', {workflow_task_id: task.id}), 'script', {series: true}
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
                        blueprints: this.creativeWorkspace.project?.creation_mode === 'original' ? '原创方向' : '仿制方向',
                        blueprint_variant: '派生方向', characters: '人物关系', outlines: '分集大纲',
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

                async syncCreativeJobProgress() {
                    if (!this.creativeWorkspace.jobId) return;
                    if (this.creativeWorkspace.showJobLogs) await this.syncCreativeJobLogs();
                    const job = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
                    if (!job) return;
                    this.creativeWorkspace.jobStatus = job.status;
                    this.creativeWorkspace.jobMessage = job.message || this.creativeWorkspace.jobMessage;
                    this.creativeWorkspace.jobProgress = job.progress || {};
                    this.creativeWorkspace.paused = Boolean(job.paused);
                    this.creativeWorkspace.jobLogCount = Number(job.log_count || 0);
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
                            this.stopPoller?.('creative-elapsed');
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
                    return status === 'failed';
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

                finishCreativeGeneration(message = '生成完成，结果已回填到当前项目') {
                    this.stopPoller?.('creative-elapsed');
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
                    this.stopPoller?.('creative-elapsed');
                    await this.fetchJobs();
                },

                async retryCreativeJob() {
                    if (this.creativeWorkspace.retrying) return;
                    const currentJob = this.jobs.find(item => item.job_id === this.creativeWorkspace.jobId);
                    const currentExtra = currentJob?.job?.extra || {};
                    const operationBound = Boolean(String(currentExtra.operation_id || '').trim());
                    if (operationBound && this.creativeWorkspace.jobStatus !== 'failed') return;
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
                        this.creativeWorkspace.showJobLogs = true;
                        this.schedulePoller?.('creative-elapsed', () => this.tickCreativeGenerationElapsed(), 1000);
                        this.creativeGenerationProgressModal.show();
                        await this.syncCreativeJobLogs();
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
                    if (item?.stage === 'script') {
                        const current = this.projectEpisodeScript(item.episode_number);
                        if (!current || String(current.id) !== String(item.id)) {
                            this.showCreativeNotice('历史版本仅供查看，不能保存', 'error');
                            return false;
                        }
                    }
                    const url = this.isFreeCreativeProject()
                        ? `/api/creative/projects/${this.creativeWorkspace.project.id}/free/items/episode/${item.id}`
                        : `/api/creative/episodes/${item.id}`;
                    const saved = await this.saveCreativeItem(url, item, '分集内容已保存', `episode-${item.id}`);
                    if (saved && item.stage === 'script') {
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        await this.refreshCreativeTopicLibrary();
                    }
                    return saved;
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

                async createProjectBeatsRevision() {
                    const item = this.selectedProjectBeats();
                    if (!item || this.isFreeCreativeProject()) return;
                    if (!['locked', 'approved'].includes(item.status)) return;
                    const project = this.creativeWorkspace.project;
                    const projectId = project?.id;
                    const episodeNumber = Number(item.episode_number);
                    if (!projectId || !episodeNumber) return;
                    const content = JSON.parse(JSON.stringify(item.content || {}));
                    const sceneCount = Array.isArray(content.beats) ? content.beats.length : 0;
                    try {
                        const task = await this.resolveCreativeWorkflowTask('beats', projectId);
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/episodes/${episodeNumber}/beats`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    content,
                                    replaces_resource_id: String(item.id),
                                    workflow_task_id: task.id,
                                    expected_project_revision: Number(project.revision || 0),
                                    allow_scene_limit_overflow: sceneCount > 3,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建 Beats 可编辑版本失败');
                        this.creativeWorkspace.selectedEpisode = episodeNumber;
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice(
                            sceneCount > 3
                                ? `已创建第 ${episodeNumber} 集可编辑草稿；当前有 ${sceneCount} 个场次，请删除或合并至 3 个以内后再保存。`
                                : `已基于第 ${episodeNumber} 集 Beats 创建可编辑草稿`
                        );
                        return data;
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '创建 Beats 可编辑版本失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                        return null;
                    }
                },

                async completeProjectScript() {
                    const item = this.selectedProjectScript();
                    if (!item) return;
                    if (this.selectedProjectScriptIsHistorical()) {
                        this.showCreativeNotice('历史版本仅供查看，不能完成并锁定', 'error');
                        return;
                    }
                    try {
                        if (this.isFreeCreativeProject()) {
                            await this.lockFreeCreativeResource(
                                'script', String(item.id), item.episode_number
                            );
                        } else {
                            await this.completeCreativeResource(
                                'script', item.id, this.creativeWorkspace.project.id
                            );
                        }
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('场次剧本已完成并锁定');
                    } catch (error) { this.creativeWorkspace.error = error.message || '完成场次剧本失败'; }
                },

                async completeProjectScriptsBatch() {
                    if (this.isFreeCreativeProject()) {
                        return this.completeFreeProjectEpisodeSeries('script');
                    }
                    const items = this.batchLockableProjectEpisodes('script');
                    if (!items.length || !this.canBatchLockProjectEpisodes('script')) return;
                    const episodes = items
                        .map(item => Number(item.episode_number))
                        .sort((a, b) => a - b);
                    const scope = episodes.length === 1
                        ? `第 ${episodes[0]} 集`
                        : `第 ${episodes[0]}-${episodes.at(-1)} 集`;
                    if (!window.confirm(
                        `将完成并锁定 ${scope}，共 ${episodes.length} 集当前场次剧本草稿。`
                        + '已锁定集会跳过，锁定后不能直接编辑。是否继续？'
                    )) return;
                    try {
                        const projectId = this.creativeWorkspace.project.id;
                        const task = await this.resolveCreativeWorkflowTask(
                            'script', projectId
                        );
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/scripts/lock`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({workflow_task_id: task.id}),
                            }
                        );
                        const payload = await response.json();
                        if (!response.ok) {
                            throw new Error(payload.detail || '批量锁定场次剧本失败');
                        }
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice(
                            `已完成并锁定 ${payload.resources?.length || episodes.length} 集场次剧本`
                        );
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '批量锁定场次剧本失败';
                    }
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
