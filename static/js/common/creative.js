window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                creativeTopicOptionLabel(topic) {
                    if (!topic?.latest_breakdown_id) return `${topic.title}（未拆解）`;
                    const version = topic.latest_breakdown_version ? ` v${topic.latest_breakdown_version}` : '';
                    if (!topic.locked_breakdown_id) return `${topic.title}（草稿${version}，需先提交审批）`;
                    if (topic.latest_breakdown_status === 'draft') return `${topic.title}（已有批准版，最新草稿${version}）`;
                    return `${topic.title}（${this.creativeWorkflowStatusLabel(topic.latest_breakdown_status)}${version}）`;
                },

                async fetchCreativeProjects(selectId = null) {
                    this.creativeWorkspace.loading = true;
                    this.creativeWorkspace.error = '';
                    try {
                        const [projectsResponse, topicsResponse] = await Promise.all([
                            aiworkerApi.request('/api/creative/projects'),
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
                        this.topicLibrary.error = '请先采纳选题并批准拆解版本';
                        return;
                    }
                    window.location.assign(`/planning/projects?topic_id=${encodeURIComponent(topic.id)}`);
                },

                async createCreativeProject() {
                    await this.syncCreativeProjectSource();
                    const source = this.creativeWorkspace.create;
                    if (!source.topic_id || !source.breakdown_id) return;
                    try {
                        const task = await this.resolveCreativeWorkflowTask('brief', null, source.topic_id);
                        const res = await aiworkerApi.request('/api/creative/projects', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({...source, workflow_task_id: task.id})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '创建项目失败');
                        this.creativeWorkspace.create = {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20};
                        window.location.assign(`/planning/projects/${data.id}/directions`);
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '创建项目失败';
                    }
                },

                async selectCreativeProject(projectId) {
                    try {
                        const res = await aiworkerApi.request(`/api/creative/projects/${projectId}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取项目失败');
                        this.creativeWorkspace.project = data;
                        this.reconcileCreativeGenerationState(data);
                        const candidates = this.latestProjectBlueprints();
                        if (!candidates.some(item => item.id === this.creativeWorkspace.selectedBlueprintId)) {
                            this.creativeWorkspace.selectedBlueprintId = data.locked_brief_id || candidates[0]?.id || null;
                        }
                        this.creativeWorkspace.selectedEpisode = Math.min(
                            Math.max(1, Number(this.creativeWorkspace.selectedEpisode || 1)),
                            Number(data.target_episode_count || 1)
                        );
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '读取项目失败';
                    }
                },

                creativeProjectStatusLabel(status) {
                    return {
                        created: '待生成方向', direction_review: '方向评审', brief_locked: 'Brief 已锁定',
                        characters_draft: '人物草稿', characters_locked: '人物已锁定', outline_draft: '大纲草稿',
                        outline_locked: '大纲已锁定', beats_in_progress: 'Beats 制作中', archived: '已归档'
                    }[status] || status || '-';
                },

                riskLabel(level) { return {low: '低', medium: '中', high: '高'}[level] || level || '-'; },
                riskBadgeClass(level) { return level === 'high' ? 'bg-danger' : level === 'medium' ? 'bg-warning text-dark' : 'bg-success'; },
                listText(value) { return Array.isArray(value) ? value.join('\n') : String(value || ''); },
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
                },

                creativeWorkflowStatusLabel(status) {
                    return {
                        draft: '草稿', submitted: '待审批', approved: '已批准',
                        returned: '已退回', superseded: '已替代',
                        locked: '已批准', brief_locked: '已批准'
                    }[status] || status || '草稿';
                },

                creativeResourceEditable(item) {
                    return !item || item.status === 'draft';
                },

                async resolveCreativeWorkflowTask(resourceType, projectId = null, topicId = null) {
                    const response = await aiworkerApi.request('/api/workflow/tasks');
                    const tasks = await response.json();
                    if (!response.ok) throw new Error(tasks.detail || '读取审批任务失败');
                    const open = ['assigned', 'in_progress', 'returned'];
                    const task = (tasks || []).find(item =>
                        item.task_type === resourceType
                        && open.includes(item.status)
                        && (projectId === null || String(item.project_id || '') === String(projectId))
                        && (topicId === null || Number(item.topic_id) === Number(topicId))
                    );
                    if (!task) throw new Error('当前没有可提交的创作任务，请先领取或开始对应任务');
                    if (['assigned', 'returned'].includes(task.status)) {
                        const startResponse = await aiworkerApi.request(`/api/workflow/tasks/${task.id}/start`, {method: 'POST'});
                        const started = await startResponse.json();
                        if (!startResponse.ok) throw new Error(started.detail || '开始创作任务失败');
                        return started;
                    }
                    return task;
                },

                async resolveSourcePackageWorkflowTask(title) {
                    const [tasksResponse, topicsResponse] = await Promise.all([
                        aiworkerApi.request('/api/workflow/tasks'),
                        aiworkerApi.request('/api/creative/topics?limit=500'),
                    ]);
                    const tasks = await tasksResponse.json();
                    const topics = await topicsResponse.json();
                    if (!tasksResponse.ok) throw new Error(tasks.detail || '读取素材任务失败');
                    if (!topicsResponse.ok) throw new Error(topics.detail || '读取选题失败');
                    const titleKey = value => String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
                    const topicIds = new Set((topics.items || [])
                        .filter(item => titleKey(item.title) === titleKey(title))
                        .map(item => Number(item.id)));
                    const open = ['assigned', 'in_progress', 'returned'];
                    const matches = (tasks || []).filter(item =>
                        item.task_type === 'source_package'
                        && open.includes(item.status)
                        && topicIds.has(Number(item.topic_id))
                    );
                    if (matches.length !== 1) throw new Error('当前剧目需要且只能有一个已分配的素材任务');
                    const task = matches[0];
                    if (['assigned', 'returned'].includes(task.status)) {
                        const response = await aiworkerApi.request(`/api/workflow/tasks/${task.id}/start`, {method: 'POST'});
                        const started = await response.json();
                        if (!response.ok) throw new Error(started.detail || '开始素材任务失败');
                        return started;
                    }
                    return task;
                },

                async submitCreativeResource(resourceType, resourceId, projectId = null, topicId = null) {
                    const task = await this.resolveCreativeWorkflowTask(resourceType, projectId, topicId);
                    const response = await aiworkerApi.request('/api/creative/workflow/submit', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            workflow_task_id: task.id,
                            resource_type: resourceType,
                            resource_id: String(resourceId),
                        }),
                    });
                    const data = await response.json();
                    if (!response.ok) throw new Error(data.detail || '提交审批失败');
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

                latestCharacterBatchId() {
                    const project = this.creativeWorkspace.project;
                    if (!project) return '';
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
                    const drafts = (project.episodes || [])
                        .filter(item => item.stage === 'outline' && ['draft', 'submitted', 'returned'].includes(item.status))
                        .sort((a, b) => Number(b.version) - Number(a.version));
                    if (drafts.length) return drafts[0].batch_id;
                    if (project.outline_batch_id) return project.outline_batch_id;
                    const items = (project.episodes || []).filter(item => item.stage === 'outline');
                    return items.length ? items[items.length - 1].batch_id : '';
                },

                currentProjectOutlines() {
                    const batch = this.latestOutlineBatchId();
                    return (this.creativeWorkspace.project?.episodes || [])
                        .filter(item => item.stage === 'outline' && item.batch_id === batch)
                        .sort((a, b) => Number(a.episode_number) - Number(b.episode_number));
                },

                currentOutlineBatchStatus() {
                    return this.currentProjectOutlines()[0]?.status || 'draft';
                },

                selectedProjectBeats() {
                    const items = (this.creativeWorkspace.project?.episodes || [])
                        .filter(item => item.stage === 'beats'
                            && Number(item.episode_number) === Number(this.creativeWorkspace.selectedEpisode)
                            && item.upstream_refs?.outline_batch_id === this.creativeWorkspace.project?.outline_batch_id)
                        .sort((a, b) => Number(b.version) - Number(a.version));
                    return items[0] || null;
                },

                async startCreativeGeneration(url, body = {}, action = '') {
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
                    this.creativeWorkspace.activeAction = action;
                    this.creativeGenerationProgressModal.show();
                    try {
                        const res = await aiworkerApi.request(url, {
                            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.detail || data.message || '创建生成任务失败');
                        this.creativeWorkspace.jobId = data.job.job_id;
                        this.creativeWorkspace.jobStatus = data.job.status;
                        this.creativeWorkspace.jobMessage = this.creativeGenerationStartedMessage(action);
                        this.creativeWorkspace.jobProgress = data.job.progress || {};
                        if (Number(data.job.started_at || 0) > 0) {
                            this.creativeWorkspace.jobStartedAt = Number(data.job.started_at) * 1000;
                        }
                        await this.fetchJobs();
                    } catch (e) {
                        this.creativeWorkspace.error = e.message || '创建生成任务失败';
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    } finally {
                        this.creativeWorkspace.startingAction = '';
                    }
                },

                async generateProjectBlueprints() {
                    const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                    return this.startCreativeGeneration(
                        `/api/creative/projects/${this.creativeWorkspace.project.id}/blueprints/generate`,
                        {count: Number(this.creativeWorkspace.directionCount || 3), workflow_task_id: task.id},
                        'blueprints'
                    );
                },

                async generateProjectCharacters() {
                    const task = await this.resolveCreativeWorkflowTask('characters', this.creativeWorkspace.project.id);
                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/characters/generate`, {workflow_task_id: task.id}, 'characters');
                },

                async generateProjectOutlines() {
                    const task = await this.resolveCreativeWorkflowTask('outline', this.creativeWorkspace.project.id);
                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/outlines/generate`, {workflow_task_id: task.id}, 'outlines');
                },

                async generateProjectBeats() {
                    const episode = Number(this.creativeWorkspace.selectedEpisode || 0);
                    const task = await this.resolveCreativeWorkflowTask('beats', this.creativeWorkspace.project.id);
                    return this.startCreativeGeneration(`/api/creative/projects/${this.creativeWorkspace.project.id}/episodes/${episode}/beats/generate`, {workflow_task_id: task.id}, 'beats');
                },

                creativeActionLabel(action) {
                    return {
                        blueprints: '仿制方向', characters: '人物关系', outlines: '分集大纲', beats: 'Beats 脚本'
                    }[action] || '创作内容';
                },

                creativeGenerationStartedMessage(action) {
                    return {
                        blueprints: '仿制方向生成任务已发起，正在后台处理',
                        characters: '人物关系生成任务已发起，正在后台处理',
                        outlines: '分集大纲生成任务已发起，正在后台处理',
                        beats: 'Beats 脚本生成任务已发起，正在后台处理'
                    }[action] || '创作内容生成任务已发起，正在后台处理';
                },

                creativeActionRunning(action) {
                    return this.creativeWorkspace.startingAction === action
                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
                },

                creativeActionDisabled(action) {
                    return Boolean(this.creativeWorkspace.startingAction)
                        || (this.creativeWorkspace.activeAction === action && this.creativeJobRunning());
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
                        return (project.episodes || []).some(item => item.stage === 'outline' && item.generation_meta?.job_id === jobId);
                    }
                    if (action === 'beats') {
                        return (project.episodes || []).some(item => item.stage === 'beats' && item.generation_meta?.job_id === jobId);
                    }
                    return false;
                },

                finishCreativeGeneration(message = '生成完成，结果已回填到当前项目') {
                    this.creativeWorkspace.jobStatus = 'succeeded';
                    this.creativeWorkspace.jobMessage = message;
                    this.creativeWorkspace.jobProgress = {completed: 1, total: 1};
                    this.creativeWorkspace.startingAction = '';
                    this.creativeWorkspace.activeAction = '';
                    this.creativeWorkspace.paused = false;
                    this.creativeWorkspace.notifiedStatus = 'succeeded';
                },

                reconcileCreativeGenerationState(project) {
                    const action = this.creativeWorkspace.activeAction;
                    const jobId = this.creativeWorkspace.jobId;
                    if (!this.creativeProjectHasJobResult(project, action, jobId)) return false;
                    this.finishCreativeGeneration();
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
                    if (Number(job.started_at || 0) > 0) {
                        this.creativeWorkspace.jobStartedAt = Number(job.started_at) * 1000;
                    }
                    this.creativeWorkspace.jobElapsedSeconds = Math.max(
                        Number(this.creativeWorkspace.jobElapsedSeconds || 0),
                        Number(job.progress?.elapsed_seconds || 0)
                    );
                    // 先尝试通过项目数据尽早检测完成（job 可能还在 running 但结果已入库）
                    if (this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
                    // job 已到终态：先刷新项目数据（让后端生成的结果可见），再尝试 reconcile
                    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
                        if (this.creativeWorkspace.notifiedStatus === job.status) return;
                        this.creativeWorkspace.notifiedStatus = job.status;
                        if (this.creativeWorkspace.project?.id) {
                            await this.selectCreativeProject(this.creativeWorkspace.project.id);
                            if (this.reconcileCreativeGenerationState(this.creativeWorkspace.project)) return;
                        }
                        if (job.status === 'succeeded') {
                            this.finishCreativeGeneration(job.message || '生成完成，结果已回填到当前项目');
                            this.showCreativeNotice('生成完成，项目内容已刷新。');
                        } else {
                            this.creativeWorkspace.startingAction = '';
                            this.creativeWorkspace.activeAction = '';
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
                creativeJobRetryable() { return ['failed', 'cancelled'].includes(this.creativeWorkspace.jobStatus); },
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
                    return `${prefix} ${this.formatBreakdownGenerationElapsed(seconds)}`;
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
                    const res = await aiworkerApi.request(`/api/jobs/${this.creativeWorkspace.jobId}/retry`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') { this.creativeWorkspace.error = data.detail || data.message || '重试失败'; return; }
                    this.creativeWorkspace.jobId = data.job.job_id;
                    this.creativeWorkspace.jobStatus = data.job.status;
                    this.creativeWorkspace.jobMessage = `${this.creativeActionLabel(this.creativeWorkspace.activeAction)}重试任务已发起，正在后台处理`;
                    this.creativeWorkspace.jobProgress = {indeterminate: true};
                    this.creativeWorkspace.jobStartedAt = Number(data.job.started_at || 0) > 0 ? Number(data.job.started_at) * 1000 : Date.now();
                    this.creativeWorkspace.jobElapsedSeconds = 0;
                    this.creativeWorkspace.paused = false;
                    this.creativeWorkspace.notifiedStatus = '';
                    this.creativeGenerationProgressModal.show();
                    await this.fetchJobs();
                },

                async saveCreativeProjectSettings() {
                    const project = this.creativeWorkspace.project;
                    if (!project) return;
                    const payload = {
                        name: project.name, genre: project.genre || '', target_platform: project.target_platform || '',
                        target_audience: project.target_audience || '', target_episode_count: Number(project.target_episode_count),
                        episode_duration_seconds: Number(project.episode_duration_seconds)
                    };
                    const res = await aiworkerApi.request(`/api/creative/projects/${project.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存 Brief 参数失败'; return; }
                    this.creativeWorkspace.project = data;
                    await this.fetchCreativeProjects(data.id);
                },

                async saveProjectBlueprint() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}`, {method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content: item.content, difference_note: item.difference_note || ''})});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '保存方向失败'; return; }
                    Object.assign(item, data);
                },

                async createProjectBlueprintVersion() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    const task = await this.resolveCreativeWorkflowTask('brief', this.creativeWorkspace.project.id);
                    const res = await aiworkerApi.request(`/api/creative/blueprints/${item.id}/versions`, {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({workflow_task_id: task.id})
                    });
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建新版本失败'; return; }
                    await this.selectCreativeProject(this.creativeWorkspace.project.id);
                    this.creativeWorkspace.selectedBlueprintId = data.id;
                },

                async submitProjectBrief() {
                    const item = this.selectedProjectBlueprint();
                    if (!item) return;
                    try {
                        await this.saveProjectBlueprint();
                        await this.submitCreativeResource('brief', item.id, this.creativeWorkspace.project.id);
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('Brief 已提交审批');
                    } catch (error) {
                        this.creativeWorkspace.error = error.message || '提交 Brief 失败';
                    }
                },

                async createBlankCharacterBatch() {
                    const projectId = this.creativeWorkspace.project.id;
                    const task = await this.resolveCreativeWorkflowTask('characters', projectId);
                    const blank = {name: '新角色', age_range: '', identity: '', external_tags: [], desire: '', secret: '', growth_arc: '', dialogue_style: '', appearance_prompt: '', voice_prompt: ''};
                    const res = await aiworkerApi.request(`/api/creative/projects/${projectId}/characters`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({characters: [blank], relationships: [], workflow_task_id: task.id})});
                    const data = await res.json();
                    if (!res.ok) { this.creativeWorkspace.error = data.detail || '创建空白人物版本失败'; return; }
                    await this.selectCreativeProject(projectId);
                },

                async saveProjectCharacter(item) { await this.saveCreativeItem(`/api/creative/characters/${item.id}`, item, '人物卡已保存', `character-${item.id}`); },
                async saveProjectRelationship(item) { await this.saveCreativeItem(`/api/creative/relationships/${item.id}`, item, '人物关系已保存', `relationship-${item.id}`); },
                async saveProjectEpisode(item) { await this.saveCreativeItem(`/api/creative/episodes/${item.id}`, item, '分集内容已保存', `episode-${item.id}`); },
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
                    } catch (e) {
                        this.creativeWorkspace.saveState[saveKey] = '';
                        this.creativeWorkspace.error = `${successMessage || '保存'}失败：${e.message || '请稍后重试'}`;
                        this.showCreativeNotice(this.creativeWorkspace.error, 'error');
                    }
                },

                async submitProjectCharacters() {
                    const projectId = this.creativeWorkspace.project.id;
                    const batchId = this.latestCharacterBatchId();
                    if (!batchId) return;
                    try {
                        await this.submitCreativeResource('characters', `${projectId}:${batchId}`, projectId);
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice('人物关系已提交审批');
                    } catch (error) { this.creativeWorkspace.error = error.message || '提交人物关系失败'; }
                },

                async submitProjectOutlines() {
                    const projectId = this.creativeWorkspace.project.id;
                    const batchId = this.latestOutlineBatchId();
                    if (!batchId) return;
                    try {
                        await this.submitCreativeResource('outline', `${projectId}:${batchId}`, projectId);
                        await this.selectCreativeProject(projectId);
                        this.showCreativeNotice('分集大纲已提交审批');
                    } catch (error) { this.creativeWorkspace.error = error.message || '提交分集大纲失败'; }
                },

                async submitProjectBeats() {
                    const item = this.selectedProjectBeats();
                    if (!item) return;
                    try {
                        await this.submitCreativeResource('beats', item.id, this.creativeWorkspace.project.id);
                        await this.selectCreativeProject(this.creativeWorkspace.project.id);
                        this.showCreativeNotice('Beats 已提交审批');
                    } catch (error) { this.creativeWorkspace.error = error.message || '提交 Beats 失败'; }
                },

                creativeExportUrl(stage, format = 'md') {
                    const id = this.creativeWorkspace.project?.id || '';
                    return `/api/creative/projects/${id}/export?stage=${encodeURIComponent(stage)}&format=${encodeURIComponent(format)}`;
                },

}));
