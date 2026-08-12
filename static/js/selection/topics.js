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
                    await this.generateCreativeTopics();
                },

                async fetchCreativeTopics() {
                    this.creative.loading = true;
                    this.creative.error = '';
                    try {
                        const cacheKey = `creative:topics:hits:${this.creative.platform}`;
                        const cached = window.aiworkerUi?.readCache(cacheKey);
                        if (cached && !cached.stale) {
                            this.creative.items = cached.value;
                            this.creative.loading = false;
                            return;
                        }
                        const res = await window.aiworkerUi.requestKey(cacheKey, async (signal) => {
                            return aiworkerApi.request(`/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}&status=pending_review`, {signal});
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
                        const cacheKey = `creative:topics:library:${this.topicLibrary.platform}:${this.topicLibrary.lifecycleStatus}:${this.topicLibrary.category}:${this.topicLibrary.minScore}:${this.topicLibrary.hasLocked}`;
                        const cached = window.aiworkerUi?.readCache(cacheKey);
                        if (cached && !cached.stale) {
                            this.topicLibrary.items = cached.value;
                            this.topicLibrary.loading = false;
                            return;
                        }
                        const params = new URLSearchParams({status: 'adopted', all: 'true'});
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

                workflowAssignmentForStage(topic) {
                    return {
                        pending_character_relationships: {
                            task_type: 'characters',
                            role_code: 'creative_planner',
                            role_label: '创意策划',
                            task_label: '人物关系'
                        },
                        pending_episode_outlines: {
                            task_type: 'outline',
                            role_code: 'screenwriter',
                            role_label: '编剧',
                            task_label: '分集大纲'
                        },
                        pending_beats: {
                            task_type: 'beats',
                            role_code: 'screenwriter',
                            role_label: '编剧',
                            task_label: 'Beats 脚本'
                        },
                        pending_script: {
                            task_type: 'script',
                            role_code: 'screenwriter',
                            role_label: '编剧',
                            task_label: '场次剧本'
                        }
                    }[topic?.lifecycle_status || topic?.status] || null;
                },

                async openStageWorkflowAssignment(topic) {
                    const assignment = this.workflowAssignmentForStage(topic);
                    const projectId = topic?.lifecycle_project_id;
                    if (!assignment || !projectId) return;
                    this.topicLibrary.error = '';
                    try {
                        const response = await aiworkerApi.request('/api/workflow/tasks');
                        const tasks = await response.json();
                        if (!response.ok) throw new Error(tasks.detail || '读取项目任务失败');
                        const task = (tasks || []).find(item =>
                            String(item.project_id || '') === String(projectId)
                            && item.task_type === assignment.task_type
                            && item.role_code === assignment.role_code
                            && ['pool', 'assigned', 'returned'].includes(item.status)
                        );
                        if (!task) throw new Error(`当前项目未找到可分配的${assignment.task_label}任务，请刷新后重试`);
                        await this.openWorkflowTaskWindow(task);
                    } catch (error) {
                        this.topicLibrary.error = error.message || '读取项目任务失败';
                    }
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

                async openPlanningAssignment(topic) {
                    this.planningAssignment = {
                        topic,
                        candidates: [],
                        assigneeUserId: '',
                        name: `${topic.title}创作项目`,
                        targetPlatform: (topic.source_platforms || [topic.platform])[0] || '',
                        targetEpisodeCount: 20,
                        creationRequestId: globalThis.crypto?.randomUUID?.()
                            || `planning-${Date.now()}-${Math.random().toString(16).slice(2)}`,
                        loading: true,
                        saving: false,
                        error: '',
                        result: null
                    };
                    this.planningAssignmentModal = this.modal('planningAssignmentModal');
                    this.planningAssignmentModal?.show();
                    try {
                        const params = new URLSearchParams({
                            role_code: 'creative_planner',
                            task_type: 'brief'
                        });
                        const response = await aiworkerApi.request(
                            `/api/workflow/assignment-candidates?${params.toString()}`
                        );
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '读取策划用户失败');
                        this.planningAssignment.candidates = payload || [];
                        this.planningAssignment.assigneeUserId = payload[0]?.user_id || '';
                        if (!payload.length) throw new Error('暂无可分配的创意策划用户');
                    } catch (error) {
                        this.planningAssignment.error = error.message || '读取策划用户失败';
                    } finally {
                        this.planningAssignment.loading = false;
                    }
                },

                async submitPlanningAssignment() {
                    const state = this.planningAssignment;
                    if (!state.topic || !state.assigneeUserId || state.saving) return;
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/topics/${state.topic.id}/planning-assignment`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    assignee_user_id: state.assigneeUserId,
                                    name: state.name.trim(),
                                    target_platform: state.targetPlatform,
                                    target_episode_count: Number(state.targetEpisodeCount || 20),
                                    creation_request_id: state.creationRequestId
                                })
                            }
                        );
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '分配策划任务失败');
                        state.result = payload;
                        await this.fetchTopicLibrary();
                    } catch (error) {
                        state.error = error.message || '分配策划任务失败';
                    } finally {
                        state.saving = false;
                    }
                },

                openManagedPlanningProject() {
                    const projectId = this.planningAssignment.result?.project?.id;
                    if (!projectId) return;
                    window.location.assign(`/review/projects?project_id=${encodeURIComponent(projectId)}`);
                },

                async openAssignmentModal(topic, role) {
                    const a = this.assignment;
                    a.topic = topic;
                    a.role = role;
                    a.selectedUserId = "";
                    a.error = "";
                    a.candidates = [];
                    a.saving = false;
                    this.modal("assignmentModal")?.show();
                    try {
                        const params = new URLSearchParams({ role_code: role, task_type: role === "creative_planner" ? "brief" : "outline" });
                        const response = await aiworkerApi.request("/api/workflow/assignment-candidates?" + params.toString());
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || "读取用户列表失败");
                        a.candidates = payload || [];
                        a.selectedUserId = payload[0]?.user_id || "";
                        if (!payload.length) throw new Error("暂无可分配的" + (role === "creative_planner" ? "创意策划" : "编剧") + "用户");
                    } catch (e) {
                        a.error = e.message;
                    }
                },

                async submitAssignment() {
                    const a = this.assignment;
                    if (!a?.selectedUserId || !a?.topic) return;
                    a.saving = true;
                    a.error = "";
                    try {
                        const body = { topic_id: a.topic.id, assignee_user_id: a.selectedUserId, role_code: a.role, task_type: a.role === "creative_planner" ? "brief" : "outline" };
                        const res = await aiworkerApi.request("/api/workflow/create-assignment", { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body) });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || "分配失败");
                        this.modal("assignmentModal")?.hide();
                        await this.fetchTopicLibrary();
                    } catch (e) {
                        a.error = e.message;
                    } finally {
                        a.saving = false;
                    }
                },

}));

window.addEventListener("aiworker:permission-denied", (event) => {
    const target = document.querySelector("[x-data]");
    if (target) target.setAttribute("data-permission-message", event.detail.message);
});
