window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async fetchCreativeTopics() {
                    this.creative.loading = true;
                    this.creative.error = '';
                    try {
                        const res = await aiworkerApi.request(`/api/creative/topics?platform=${encodeURIComponent(this.creative.platform)}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取选题失败');
                        this.creative.items = data.items || [];
                    } catch (e) {
                        this.creative.error = e.message || '读取选题失败';
                    } finally {
                        this.creative.loading = false;
                    }
                },

                async fetchTopicLibrary() {
                    this.topicLibrary.loading = true;
                    this.topicLibrary.error = '';
                    try {
                        const params = new URLSearchParams({limit: '500'});
                        if (this.topicLibrary.platform) params.set('platform', this.topicLibrary.platform);
                        if (this.topicLibrary.lifecycleStatus) params.set('lifecycle_status', this.topicLibrary.lifecycleStatus);
                        if (this.topicLibrary.category.trim()) params.set('category', this.topicLibrary.category.trim());
                        if (this.topicLibrary.minScore !== '') params.set('min_score', this.topicLibrary.minScore);
                        if (this.topicLibrary.hasLocked !== '') params.set('has_locked_breakdown', this.topicLibrary.hasLocked);
                        const res = await aiworkerApi.request(`/api/creative/topics?${params.toString()}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取选题库失败');
                        this.topicLibrary.items = data.items || [];
                    } catch (e) {
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
                        beats_exported: 'Beats脚本已导出', rejected: '不跟进', archived: '已归档'
                    }[status] || status;
                },

                topicLifecycleClass(status) {
                    const value = status || 'pending_review';
                    return `lifecycle-${value.replaceAll('_', '-')}`;
                },

                breakdownStatusLabel(topic) {
                    if (!topic?.latest_breakdown_id) return '未拆解';
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (topic.latest_breakdown_status === 'locked') return `已锁定${version}`;
                    if (topic.latest_breakdown_status === 'submitted') return `待审批${version}`;
                    if (topic.latest_breakdown_status === 'approved') return `已批准${version}`;
                    if (topic.latest_breakdown_status === 'returned') return `已退回${version}`;
                    return topic.locked_breakdown_id
                        ? `草稿${version} · 已有批准版`
                        : `草稿${version} · 待提交`;
                },

                breakdownStatusClass(topic) {
                    if (!topic?.latest_breakdown_id) return 'bg-secondary';
                    if (['locked', 'approved'].includes(topic.latest_breakdown_status)) return 'bg-success';
                    if (topic.latest_breakdown_status === 'returned') return 'bg-danger';
                    if (topic.latest_breakdown_status === 'submitted') return 'bg-warning text-dark';
                    return topic.locked_breakdown_id ? 'bg-info text-dark' : 'bg-warning text-dark';
                },

                async bootstrapTopicWorkflow(topic) {
                    const response = await aiworkerApi.request(`/api/creative/topics/${topic.id}/workflow`, {method: 'POST'});
                    const data = await response.json();
                    if (!response.ok) {
                        this.topicLibrary.error = data.detail || '创建工作流失败';
                        return;
                    }
                    topic.status = 'adopted';
                    topic.workflow_task_id = data.task?.id || '';
                },

                creativeTopicOptionLabel(topic) {
                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先提交审批）`;
                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有批准版，最新草稿${version}）`;
                    return `${topic.title}（${this.breakdownStatusLabel(topic)}）`;
                },

}));

window.addEventListener('aiworker:permission-denied', (event) => {
    const target = document.querySelector('[x-data]');
    if (target) target.setAttribute('data-permission-message', event.detail.message);
});
