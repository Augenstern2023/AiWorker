window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
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

                projectEpisodeScriptVersions(episodeNumber) {
                    return (this.creativeWorkspace.project?.episodes || [])
                        .filter(item => item.stage === 'script'
                            && Number(item.episode_number) === Number(episodeNumber))
                        .sort((a, b) => Number(b.version) - Number(a.version)
                            || Number(b.id) - Number(a.id));
                },

                projectEpisodeHistoricalScripts(episodeNumber) {
                    const current = this.projectEpisodeScript(episodeNumber);
                    return this.projectEpisodeScriptVersions(episodeNumber)
                        .filter(item => !current || String(item.id) !== String(current.id));
                },

                projectScriptVersionOptionLabel(item) {
                    const beatsVersion = item?.upstream_refs?.beats_version;
                    const beatsLabel = beatsVersion ? `Beats v${beatsVersion}` : 'Beats 版本未知';
                    return `历史 · v${item?.version || 1} · ${beatsLabel} · ${this.creativeWorkflowStatusLabel(item?.status)}`;
                },

                projectScriptEpisodeLabel(episodeNumber) {
                    const current = this.projectEpisodeScript(episodeNumber);
                    if (current) return `第 ${episodeNumber} 集 · 当前 v${current.version}`;
                    if (this.projectEpisodeScriptVersions(episodeNumber).length) {
                        return `第 ${episodeNumber} 集 · 有历史版本`;
                    }
                    return `第 ${episodeNumber} 集 · 未生成`;
                },

                resetProjectScriptVersionSelection() {
                    this.creativeWorkspace.selectedScriptVersionId = '';
                },

                selectedProjectScript() {
                    const episodeNumber = this.creativeWorkspace.selectedEpisode;
                    const selectedId = String(this.creativeWorkspace.selectedScriptVersionId || '');
                    if (selectedId) {
                        const selected = this.projectEpisodeScriptVersions(episodeNumber)
                            .find(item => String(item.id) === selectedId);
                        if (selected) return selected;
                    }
                    return this.projectEpisodeScript(episodeNumber);
                },

                selectedProjectScriptIsHistorical() {
                    const selected = this.selectedProjectScript();
                    const current = this.projectEpisodeScript(this.creativeWorkspace.selectedEpisode);
                    return Boolean(selected && (!current || String(selected.id) !== String(current.id)));
                },

                selectedProjectScriptEditable() {
                    const selected = this.selectedProjectScript();
                    return Boolean(selected
                        && !this.selectedProjectScriptIsHistorical()
                        && this.creativeResourceEditable(selected));
                },

                videoParameterModelKeys() {
                    return ['minimax_h3', 'minimax_h3_max', 'seedance_2_5'];
                },

                videoParameterScenarioKeys() {
                    return ['platform_fit', 'content_optimal'];
                },

                videoParameterScenarioLabel(scenario) {
                    return {platform_fit: '当前平台适配', content_optimal: '内容最优'}[scenario] || scenario;
                },

                videoParameterOrientationLabel(profile) {
                    return profile?.orientation === 'portrait' ? '竖屏' : '横屏';
                },

                videoParameterModel(modelKey) {
                    return this.creativeWorkspace.videoParameters.record?.recommendation?.models?.[modelKey] || null;
                },

                videoParameterProfile(modelKey, scenario) {
                    return this.videoParameterModel(modelKey)?.scenarios?.[scenario] || null;
                },

                confirmedVideoParameter(modelKey) {
                    return this.creativeWorkspace.videoParameters.record?.confirmed?.[modelKey] || null;
                },

                videoParameterIsConfirmed(modelKey, scenario) {
                    return this.confirmedVideoParameter(modelKey)?.scenario === scenario;
                },

                videoParameterCanWrite() {
                    return !Boolean(this.creativeWorkspace.project?.preview_only);
                },

                videoParameterAnalysisRunning() {
                    return ['queued', 'running'].includes(
                        this.creativeWorkspace.videoParameters.record?.status
                    );
                },

                videoParameterElapsedSeconds() {
                    if (!this.videoParameterAnalysisRunning()) return 0;
                    const startedAt = Number(
                        this.creativeWorkspace.videoParameters.record?.updated_at || 0
                    );
                    if (!startedAt) return 0;
                    return Math.max(0, Math.floor(Date.now() / 1000 - startedAt));
                },

                videoParameterRemainingSeconds() {
                    const deadline = Math.max(
                        15,
                        Number(this.creativeWorkspace.videoParameters.analysisDeadlineSeconds || 60)
                    );
                    return Math.max(0, deadline - this.videoParameterElapsedSeconds());
                },

                videoParameterAnalysisProgress() {
                    const state = this.creativeWorkspace.videoParameters;
                    const completed = Number(state.job?.progress?.completed);
                    const total = Number(state.job?.progress?.total);
                    if (Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
                        return Math.max(8, Math.min(95, Math.round(completed / total * 100)));
                    }
                    const deadline = Math.max(15, Number(state.analysisDeadlineSeconds || 60));
                    return Math.max(
                        8,
                        Math.min(95, Math.round(this.videoParameterElapsedSeconds() / deadline * 100))
                    );
                },

                videoParameterAnalysisMessage() {
                    return this.creativeWorkspace.videoParameters.job?.message
                        || '正在读取全剧构图、场景规模和镜头节奏';
                },

                videoParameterSourceCount() {
                    return this.projectStageEpisodeItems('script').length;
                },

                videoParameterSourceComplete() {
                    const total = Number(this.creativeWorkspace.project?.target_episode_count || 0);
                    return total > 0 && this.videoParameterSourceCount() >= total;
                },

                scheduleVideoParameterPoll() {
                    const state = this.creativeWorkspace.videoParameters;
                    if (state.pollTimer) window.clearTimeout(state.pollTimer);
                    if (!this.videoParameterAnalysisRunning()) {
                        state.pollTimer = null;
                        return;
                    }
                    state.pollTimer = window.setTimeout(
                        () => this.loadVideoParameterRecommendation({allowAuto: false}),
                        2000
                    );
                },

                async loadVideoParameterRecommendation({allowAuto = true} = {}) {
                    const projectId = Number(this.creativeWorkspace.project?.id || 0);
                    if (!projectId || this.page !== 'writing-scripts') return;
                    const state = this.creativeWorkspace.videoParameters;
                    if (state.loading) return;
                    state.loading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/video-parameter-recommendation`
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取视频参数建议失败');
                        state.record = data.record || null;
                        state.capabilities = data.capabilities || {};
                        state.scenarios = data.scenarios || [];
                        state.job = data.job || null;
                        state.analysisDeadlineSeconds = Number(
                            data.analysis_deadline_seconds || 60
                        );
                        const shouldAuto = allowAuto
                            && !state.autoRequested
                            && !state.record
                            && this.videoParameterSourceComplete()
                            && this.videoParameterCanWrite();
                        if (shouldAuto) {
                            state.autoRequested = true;
                            await this.analyzeVideoParameters({automatic: true});
                            return;
                        }
                    } catch (error) {
                        state.error = error.message || '读取视频参数建议失败';
                    } finally {
                        state.loading = false;
                        this.scheduleVideoParameterPoll();
                    }
                },

                async analyzeVideoParameters({automatic = false} = {}) {
                    const projectId = Number(this.creativeWorkspace.project?.id || 0);
                    const state = this.creativeWorkspace.videoParameters;
                    if (!projectId || !this.videoParameterSourceCount() || state.analyzing) return;
                    state.analyzing = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/video-parameter-recommendation/analyze`,
                            {method: 'POST'}
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '启动视频参数分析失败');
                        state.record = data.record || null;
                        state.capabilities = data.capabilities || state.capabilities;
                        state.job = data.job || null;
                        state.analysisDeadlineSeconds = Number(
                            data.analysis_deadline_seconds || state.analysisDeadlineSeconds || 60
                        );
                        this.showCreativeNotice(
                            automatic ? '全剧已完成，正在自动分析视频规格' : '已启动视频参数分析'
                        );
                    } catch (error) {
                        state.error = error.message || '启动视频参数分析失败';
                    } finally {
                        state.analyzing = false;
                        this.scheduleVideoParameterPoll();
                    }
                },

                async confirmVideoParameter(modelKey, scenario) {
                    const projectId = Number(this.creativeWorkspace.project?.id || 0);
                    const state = this.creativeWorkspace.videoParameters;
                    const fingerprint = String(state.record?.source_fingerprint || '');
                    if (!projectId || !fingerprint || state.confirming) return;
                    state.confirming = `${modelKey}:${scenario}`;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/video-parameter-recommendation/confirm`,
                            {
                                method: 'POST', headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({model_key: modelKey, scenario, source_fingerprint: fingerprint})
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '确认视频参数失败');
                        state.record = data.record || null;
                        this.showCreativeNotice('视频参数建议已确认');
                    } catch (error) {
                        state.error = error.message || '确认视频参数失败';
                    } finally {
                        state.confirming = '';
                    }
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

                videoParameterExportDefaults() {
                    const record = this.creativeWorkspace.videoParameters?.record;
                    if (!record?.recommendation?.models) return [];
                    return this.videoParameterModelKeys().map(modelKey => {
                        const model = record.recommendation.models[modelKey];
                        const confirmed = record.confirmed?.[modelKey];
                        const scenario = confirmed?.scenario || 'platform_fit';
                        const profile = confirmed?.profile || model?.scenarios?.[scenario];
                        if (!profile) return null;
                        return {
                            modelKey,
                            displayName: model.capability?.display_name || modelKey,
                            enabled: true,
                            scenario,
                            ratio: profile.ratio,
                            resolutionTier: profile.resolution_tier,
                            resolutionOptions: [...(model.capability?.resolution_tiers || [])],
                        };
                    }).filter(Boolean);
                },

                openEpisodeDownload(stage, {forceAll = false} = {}) {
                    if (this.creativeWorkspace.episodeDownload?.previewUrl) {
                        this.clearScreenplayExportPreview();
                    }
                    const available = this.projectStageEpisodeItems(stage)
                        .map(item => Number(item.episode_number))
                        .filter(number => number > 0);
                    if (!available.length) {
                        this.showCreativeNotice(`尚无可下载的${this.creativeStageLabel(stage)}`, 'error');
                        return;
                    }
                    const selected = Number(this.creativeWorkspace.selectedEpisode);
                    const initial = !forceAll && selected > 0 && available.includes(selected) ? [selected] : [...available];
                    this.creativeWorkspace.episodeDownload = {
                        stage,
                        selectedEpisodes: initial,
                        allEpisodes: initial.length === available.length,
                        format: stage === 'script' ? 'docx' : 'md',
                        templateId: 'classic',
                        templateOptions: [],
                        templatesLoading: false,
                        templatesError: '',
                        previewUrl: '',
                        previewLoading: false,
                        previewError: '',
                        videoModels: stage === 'script' ? this.videoParameterExportDefaults() : [],
                    };
                    this.modal('creativeEpisodeDownloadModal')?.show();
                    if (stage === 'script') void this.loadScreenplayExportTemplates();
                },

                async loadScreenplayExportTemplates() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || state.stage !== 'script' || !window.aiworkerApi?.request) return;
                    state.templatesLoading = true;
                    state.templatesError = '';
                    try {
                        const response = await window.aiworkerApi.request(
                            `/api/creative/projects/${projectId}/episodes/export/templates`
                        );
                        const payload = await response.json().catch(() => ({}));
                        if (!response.ok) throw new Error(payload.detail || '读取剧本模板失败');
                        const items = Array.isArray(payload) ? payload : (payload.items || []);
                        state.templateOptions = items.filter(item => item?.previewable);
                        const availableIds = new Set(state.templateOptions.map(item => String(item.id)));
                        const defaultId = String(payload.default_template_id || 'classic');
                        if (!availableIds.has(String(state.templateId || ''))) {
                            state.templateId = availableIds.has(defaultId)
                                ? defaultId : String(state.templateOptions[0]?.id || '');
                        }
                        if (!state.templateOptions.length) throw new Error('当前没有可用的剧本导出模板');
                    } catch (error) {
                        if (window.aiworkerApi?.isAbortError?.(error)) return;
                        state.templatesError = error.message || '读取剧本模板失败';
                    } finally {
                        state.templatesLoading = false;
                    }
                },

                clearScreenplayExportPreview() {
                    const state = this.creativeWorkspace.episodeDownload;
                    if (String(state.previewUrl || '').startsWith('blob:')) {
                        URL.revokeObjectURL(state.previewUrl);
                    }
                    state.previewUrl = '';
                    state.previewError = '';
                    state.previewLoading = false;
                },

                selectScreenplayExportTemplate(templateId) {
                    const normalized = String(templateId || '');
                    if (normalized === this.creativeWorkspace.episodeDownload.templateId) return;
                    this.clearScreenplayExportPreview();
                    this.creativeWorkspace.episodeDownload.templateId = normalized;
                },

                episodeExportSelectionParams({includeFormat = false} = {}) {
                    const state = this.creativeWorkspace.episodeDownload;
                    const selected = [...new Set((state.selectedEpisodes || []).map(Number))]
                        .filter(number => number > 0)
                        .sort((left, right) => left - right);
                    const params = new URLSearchParams({
                        stage: state.stage,
                        episodes: state.allEpisodes ? 'all' : selected.join(','),
                    });
                    if (state.stage === 'script') {
                        state.templateId = String(state.templateId || 'classic');
                        params.set('template', state.templateId);
                    }
                    if (includeFormat) params.set('format', state.format);
                    return params;
                },

                async previewSelectedEpisodes() {
                    const state = this.creativeWorkspace.episodeDownload;
                    if (state.stage !== 'script' || !state.templateId || !state.selectedEpisodes.length) return;
                    this.clearScreenplayExportPreview();
                    state.previewLoading = true;
                    const params = this.episodeExportSelectionParams();
                    const previewEndpoint = `/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/export/preview?${params.toString()}`;
                    try {
                        const response = await window.aiworkerApi.request(previewEndpoint);
                        if (!response.ok) {
                            const payload = await response.json().catch(() => ({}));
                            throw new Error(payload.detail || payload.message || '预览失败，请稍后重试');
                        }
                        const html = await response.text();
                        state.previewUrl = URL.createObjectURL(
                            new Blob([html], {type: 'text/html;charset=utf-8'})
                        );
                    } catch (error) {
                        if (window.aiworkerApi?.isAbortError?.(error)) {
                            state.previewError = '预览请求已取消，请重试';
                        } else {
                            state.previewError = error.message || '预览失败，请稍后重试';
                        }
                    } finally {
                        state.previewLoading = false;
                    }
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
                    this.clearScreenplayExportPreview();
                },

                syncEpisodeDownloadAll() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const available = this.episodeDownloadAvailableNumbers();
                    const selected = new Set((state.selectedEpisodes || []).map(Number));
                    state.allEpisodes = available.length > 0 && available.every(number => selected.has(number));
                    this.clearScreenplayExportPreview();
                },

                episodeDownloadActionLabel() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const count = (state.selectedEpisodes || []).length;
                    return state.allEpisodes ? `下载全集（${count} 集）` : `下载已选 ${count} 集`;
                },

                async downloadSelectedEpisodes() {
                    const state = this.creativeWorkspace.episodeDownload;
                    const selected = [...new Set((state.selectedEpisodes || []).map(Number))]
                        .filter(number => number > 0)
                        .sort((left, right) => left - right);
                    if (!state.stage || !selected.length) return;
                    const params = this.episodeExportSelectionParams({includeFormat: true});
                    if (state.stage === 'script' && (state.videoModels || []).length) {
                        params.set('video_parameters', JSON.stringify({
                            models: state.videoModels
                                .filter(item => item.enabled)
                                .map(item => ({
                                    model_key: item.modelKey,
                                    scenario: item.scenario,
                                    ratio: item.ratio,
                                    resolution_tier: item.resolutionTier,
                                })),
                        }));
                    }
                    const modalElement = document.getElementById('creativeEpisodeDownloadModal');
                    if (modalElement?.contains(document.activeElement)) {
                        document.activeElement.blur();
                    }
                    this.modal('creativeEpisodeDownloadModal')?.hide();
                    const downloadUrl = `/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/download?${params.toString()}`;
                    try {
                        const response = await window.aiworkerApi.request(downloadUrl);
                        if (!response.ok) {
                            const payload = await response.json().catch(() => ({}));
                            throw new Error(payload.detail || payload.message || '下载失败，请稍后重试');
                        }
                        const disposition = response.headers.get('content-disposition') || '';
                        const encodedFilename = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
                        let filename = `${this.creativeStageLabel(state.stage)}.${state.format}`;
                        if (encodedFilename) {
                            try {
                                filename = decodeURIComponent(encodedFilename);
                            } catch (_) {}
                        }
                        const objectUrl = URL.createObjectURL(await response.blob());
                        const link = document.createElement('a');
                        link.href = objectUrl;
                        link.download = filename;
                        document.body.appendChild(link);
                        link.click();
                        link.remove();
                        URL.revokeObjectURL(objectUrl);
                    } catch (error) {
                        if (window.aiworkerApi?.isAbortError?.(error)) return;
                        this.showCreativeNotice(error.message || '下载失败，请稍后重试', 'error');
                        this.modal('creativeEpisodeDownloadModal')?.show();
                    }
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
}));
