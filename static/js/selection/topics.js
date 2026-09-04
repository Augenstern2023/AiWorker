window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                rememberCreativePlatform() {
                    try {
                        window.localStorage.setItem('aiworker.selection.hits.platform', this.creative.platform === 'hg' ? 'hg' : 'dy');
                    } catch (_) {
                        // Private browsing can deny storage; the current page still works.
                    }
                },

                async changeCreativePlatform() {
                    this.rememberCreativePlatform();
                    const sources = this.creativeRankSourceOptions();
                    this.creative.rankSource = this.creativeRankSourcePreference(sources);
                    this.rememberCreativeRankSource();
                    await this.generateCreativeTopics();
                },

                creativeRankSourceOptions() {
                    return this.creative.platform === 'hg'
                        ? ['全部', '真人剧', '漫剧', 'AI剧']
                        : ['榜单', '热榜'];
                },

                creativeRankSourcePreference(sources = this.creativeRankSourceOptions()) {
                    try {
                        const platform = this.creative.platform === 'hg' ? 'hg' : 'dy';
                        const stored = window.localStorage.getItem(
                            `aiworker.selection.hits.rank-source.${platform}`
                        );
                        return sources.includes(stored) ? stored : sources[0];
                    } catch (_) {
                        return sources[0];
                    }
                },

                rememberCreativeRankSource() {
                    try {
                        const platform = this.creative.platform === 'hg' ? 'hg' : 'dy';
                        window.localStorage.setItem(
                            `aiworker.selection.hits.rank-source.${platform}`,
                            this.creative.rankSource
                        );
                    } catch (_) {
                        // Private browsing can deny storage; the current page still works.
                    }
                },

                async changeCreativeRankSource(rankSource) {
                    if (!this.creativeRankSourceOptions().includes(rankSource)) return;
                    if (this.creative.rankSource === rankSource) return;
                    this.creative.rankSource = rankSource;
                    this.rememberCreativeRankSource();
                    await this.generateCreativeTopics();
                },

                async fetchCreativeTopics() {
                    this.creative.loading = true;
                    this.creative.error = '';
                    try {
                        const cacheKey = `creative:topics:hits:${this.creative.platform}:${this.creative.rankSource}`;
                        const cached = window.aiworkerUi?.readCache(cacheKey);
                        if (cached && !cached.stale) {
                            this.creative.items = cached.value;
                            this.creative.loading = false;
                            return;
                        }
                        const res = await window.aiworkerUi.requestKey(cacheKey, async (signal) => {
                            return aiworkerApi.request(`/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}&rank_source=${encodeURIComponent(this.creative.rankSource)}&status=pending_review`, {signal});
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取选题失败');
                        const items = data.items || [];
                        this.creative.items = items;
                        window.aiworkerUi?.writeCache(cacheKey, items, {ttlMs: 30000});
                    } catch (e) {
                        if (window.aiworkerApi?.isAbortError(e)) return;
                        this.creative.error = e.message || '读取选题失败';
                    } finally {
                        this.creative.loading = false;
                    }
                },

                topicLibraryExportParams(format) {
                    const params = new URLSearchParams({status: 'adopted', format});
                    if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
                    if (this.topicLibrary.lifecycleStatus) params.set('lifecycle_status', this.topicLibrary.lifecycleStatus);
                    if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
                    if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
                    if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
                    return params;
                },

                exportTopicLibrary(format) {
                    const params = this.topicLibraryExportParams(format);
                    window.open(`/api/creative/topics/export?${params.toString()}`, '_blank', 'noopener');
                },

                async fetchTopicLibrary() {
                    this.topicLibrary.loading = true;
                    this.topicLibrary.error = '';
                    try {
                        const cacheKey = `creative:topics:library:adopted-archived:${this.topicLibrary.platform}:${this.topicLibrary.lifecycleStatus}:${this.topicLibrary.category}:${this.topicLibrary.minScore}:${this.topicLibrary.hasLocked}`;
                        const cached = window.aiworkerUi?.readCache(cacheKey);
                        if (cached && !cached.stale) {
                            this.topicLibrary.items = cached.value;
                            this.topicLibrary.loading = false;
                            return;
                        }
                        const params = new URLSearchParams({status: 'adopted,archived', all: 'true'});
                        if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
                        if (this.topicLibrary.lifecycleStatus) params.set('lifecycle_status', this.topicLibrary.lifecycleStatus);
                        if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
                        if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
                        if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
                        const res = await window.aiworkerUi.requestKey(cacheKey, async (signal) => {
                            return aiworkerApi.request(`/api/creative/topics?${params.toString()}`, {signal});
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取选题库失败');
                        const items = data.items || [];
                        this.topicLibrary.items = items;
                        window.aiworkerUi?.writeCache(cacheKey, items, {ttlMs: 30000});
                    } catch (e) {
                        if (window.aiworkerApi?.isAbortError(e)) return;
                        this.topicLibrary.error = e.message || '读取选题库失败';
                    } finally {
                        this.topicLibrary.loading = false;
                    }
                },

                topicStatusLabel(status) {
                    return {pending_review: '待评估', adopted: '已采纳', rejected: '不跟进', archived: '已归档'}[status] || status || '-';
                },

                topicStatusClass(status) {
                    return {adopted: 'bg-success', rejected: 'bg-danger', archived: 'bg-secondary', pending_review: 'bg-warning text-dark'}[status] || 'bg-secondary';
                },

                topicLifecycleLabel(topic) {
                    const status = topic?.lifecycle_status || topic?.status || 'pending_review';
                    if (status === 'downloaded') return `已下载 ${Number(topic?.downloaded_episode_count || 0)} 集`;
                    return {
                        pending_review: '待评估', adopted: '已采纳', pending_download: '待下载',
                        pending_breakdown: '待拆解', breakdown_completed: '已拆解',
                        pending_imitation: '待仿制', imitation_generated: '已仿制',
                        pending_character_relationships: '人物关系待生成', character_relationships_generated: '人物关系已生成',
                        pending_episode_outlines: '分集大纲待生成', episode_outlines_generated: '分集大纲已生成',
                        pending_beats: 'Beats脚本待生成', beats_generated: 'Beats脚本已生成',
                        beats_exported: 'Beats脚本已导出', pending_script: '场次剧本待生成', script_in_progress: '场次剧本生成中', script_generated: '场次剧本已生成', script_exported: '场次剧本已导出', rejected: '不跟进', archived: '已归档'
                    }[status] || status;
                },

                topicLifecycleClass(status) {
                    const value = status || 'pending_review';
                    return `lifecycle-${value.replaceAll('_', '-')}`;
                },

                generatedLifecycleStage(status) {
                    return {
                        imitation_generated: 'brief',
                        character_relationships_generated: 'characters',
                        episode_outlines_generated: 'outline',
                        beats_generated: 'beats',
                        beats_exported: 'beats',
                        script_in_progress: 'script',
                        script_generated: 'script',
                        script_exported: 'script'
                    }[status] || '';
                },

                async openTopicProject(topic) {
                    const projectId = topic?.lifecycle_project_id;
                    if (!projectId) return;
                    window.location.assign(`/planning/projects/${encodeURIComponent(projectId)}/brief`);
                },

                async openGeneratedContentPreview(topic) {
                    const stage = this.generatedLifecycleStage(topic?.lifecycle_status);
                    if (!stage || !topic?.lifecycle_project_id) return;
                    this.generatedContentPreview = {topic, project: null, stage, versions: [], selectedVersion: 0, selectedResourceId: '', snapshot: null, loading: true, error: ''};
                    this.modal('generatedContentPreviewModal')?.show();
                    await this.loadGeneratedContentVersion();
                },

                async loadGeneratedContentVersion(resourceId = '') {
                    const state = this.generatedContentPreview;
                    const projectId = state.topic?.lifecycle_project_id;
                    if (!projectId || !state.stage) return;
                    state.loading = true;
                    state.error = '';
                    try {
                        const params = new URLSearchParams({stage: state.stage});
                        if (resourceId) params.set('resource_id', resourceId);
                        const response = await aiworkerApi.request(`/api/creative/projects/${projectId}/generated-content?${params.toString()}`);
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '读取已生成内容失败');
                        state.project = payload.project;
                        state.versions = payload.versions || [];
                        state.selectedResourceId = payload.selected_resource_id || '';
                        state.snapshot = payload.snapshot || null;
                        this.renderGeneratedContentPreview();
                    } catch (error) {
                        state.error = error.message || '读取已生成内容失败';
                        state.snapshot = null;
                    } finally {
                        state.loading = false;
                    }
                },

                generatedContentPreviewVersionOptions() {
                    return [...new Set(this.generatedContentPreview.versions.map(item => Number(item.version) || 1))]
                        .sort((left, right) => right - left);
                },

                filteredGeneratedContentVersions() {
                    const selectedVersion = Number(this.generatedContentPreview.selectedVersion || 0);
                    return selectedVersion
                        ? this.generatedContentPreview.versions.filter(item => Number(item.version) === selectedVersion)
                        : this.generatedContentPreview.versions;
                },

                filterGeneratedContentPreviewByVersion() {
                    const versions = this.filteredGeneratedContentVersions();
                    const selectedResourceId = String(this.generatedContentPreview.selectedResourceId || '');
                    if (versions.some(item => String(item.resource_id) === selectedResourceId)) return;
                    this.loadGeneratedContentVersion(versions[0]?.resource_id || '');
                },

                renderGeneratedContentPreview() {
                    this.$nextTick(() => {
                        const target = document.getElementById('generatedContentPreviewBody');
                        if (!target) return;
                        target.replaceChildren();
                        if (this.generatedContentPreview.stage === 'script' && this.generatedContentPreview.snapshot) {
                            target.appendChild(this.buildScriptPreview(this.generatedContentPreview.snapshot));
                        } else if (this.generatedContentPreview.snapshot && window.aiworkerApprovalRenderer) {
                            target.appendChild(window.aiworkerApprovalRenderer.render(this.generatedContentPreview.stage, this.generatedContentPreview.snapshot));
                        }
                    });
                },

                buildScriptPreview(snapshot) {
                    const content = snapshot?.content || {};
                    const episode = Number(snapshot?.episode_number || 0);
                    const lines = [`第 ${episode} 集:${String(content.episode_title || '').trim()}`, ''];
                    for (const scene of content.scenes || []) {
                        const props = (scene.props || []).filter(Boolean).join('、') || '无';
                        const characters = (scene.characters || []).filter(Boolean).join('、') || '无';
                        lines.push(`[${scene.sequence_no}]-[${scene.scene_no}] [${scene.time_of_day || ''}] [${scene.interior_exterior || ''}] [${scene.location || ''}]`);
                        lines.push(`道具:${props} 出场人物:${characters}`, '');
                        for (const block of scene.blocks || []) {
                            const text = String(block.text || '').trim();
                            const speaker = String(block.speaker || '').trim();
                            const modifier = String(block.modifier || '').trim();
                            const rendered = {
                                action: `△ ${text}`,
                                dialogue: modifier ? `${speaker}(${modifier}):${text}` : `${speaker}:${text}`,
                                voiceover: speaker ? `画外音（${speaker}）：${text}` : `画外音：${text}`,
                                inner_voice: `${speaker}(内心OS):${text}`,
                                subtitle: `【字幕：${text}】`,
                                cut: '【切镜】',
                            }[block.type];
                            if (rendered) lines.push(rendered, '');
                        }
                    }
                    const preview = document.createElement('pre');
                    preview.className = 'mb-0 text-wrap';
                    preview.textContent = lines.join('\n').trimEnd();
                    return preview;
                },

                downloadGeneratedContent(format) {
                    const state = this.generatedContentPreview;
                    const projectId = state.project?.id || state.topic?.lifecycle_project_id;
                    if (!projectId || !state.stage || !state.selectedResourceId) return;
                    if (state.stage === 'script' && format === 'md') format = 'txt';
                    const params = new URLSearchParams({stage: state.stage, resource_id: state.selectedResourceId, format});
                    window.open(`/api/creative/projects/${projectId}/generated-content/download?${params.toString()}`, '_blank', 'noopener');
                },

                breakdownStatusLabel(topic) {
                    if (!topic?.latest_breakdown_id) return '未拆解';
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (topic.latest_breakdown_status === 'locked') return `已锁定${version}`;
                    return topic.locked_breakdown_id
                        ? `草稿${version} · 已有批准版`
                        : `草稿${version} · 待提交`;
                },

                breakdownStatusClass(topic) {
                    if (!topic?.latest_breakdown_id) return 'bg-secondary';
                    if (['locked', 'approved'].includes(topic.latest_breakdown_status)) return 'bg-success';
                    return topic.locked_breakdown_id ? 'bg-info text-dark' : 'bg-warning text-dark';
                },

                creativeTopicOptionLabel(topic) {
                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先完成拆解）`;
                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有完成版，最新草稿${version}）`;
                    return `${topic.title}（${this.breakdownStatusLabel(topic)}）`;
                },

                openCreateProject(topic) {
                    this.planningAssignment = {
                        topic,
                        name: `${topic.title}创作项目`,
                        targetPlatform: (topic.source_platforms || [topic.platform])[0] || '',
                        targetEpisodeCount: 20,
                        creationRequestId: globalThis.crypto?.randomUUID?.()
                            || `planning-${Date.now()}-${Math.random().toString(16).slice(2)}`,
                        saving: false,
                        error: '',
                        result: null
                    };
                    this.planningAssignmentModal = this.modal('planningAssignmentModal');
                    this.planningAssignmentModal?.show();
                },

                async submitCreateProject() {
                    const state = this.planningAssignment;
                    if (!state.topic || state.saving) return;
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/topics/${state.topic.id}/planning-assignment`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    name: state.name.trim(),
                                    target_platform: state.targetPlatform,
                                    target_episode_count: Number(state.targetEpisodeCount || 20),
                                    creation_request_id: state.creationRequestId
                                })
                            }
                        );
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '创建项目及任务失败');
                        state.result = payload;
                        await this.fetchTopicLibrary();
                    } catch (error) {
                        state.error = error.message || '创建项目及任务失败';
                    } finally {
                        state.saving = false;
                    }
                },

                openCreatedProject() {
                    const projectId = this.planningAssignment.result?.project?.id;
                    if (!projectId) return;
                    window.location.assign(`/planning/projects/${encodeURIComponent(projectId)}/brief`);
                },

}));

window.addEventListener("aiworker:permission-denied", (event) => {
    const target = document.querySelector("[x-data]");
    if (target) target.setAttribute("data-permission-message", event.detail.message);
});
