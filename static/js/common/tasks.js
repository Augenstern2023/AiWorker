function localDateString(date = new Date()) {
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        }

        function creativePlatformPreference() {
            try {
                return window.localStorage.getItem('aiworker.selection.hits.platform') === 'hg' ? 'hg' : 'dy';
            } catch (_) {
                return 'dy';
            }
        }

        function appData() {
            const workspace = {
                page: document.body.dataset.workspacePage || '',
                workflowTasks: [],
                workflowTaskError: '',
                workflowTaskWindow: {
                    task: null,
                    candidates: [],
                    selectedAssigneeId: '',
                    loading: false,
                    saving: false,
                    error: ''
                },
                planningAssignment: {
                    topic: null,
                    candidates: [],
                    assigneeUserId: '',
                    name: '',
                    targetPlatform: '',
                    targetEpisodeCount: 20,
                    creationRequestId: '',
                    loading: false,
                    saving: false,
                    error: '',
                    result: null
                },
                isRunning: false,
                jobs: [],
                selectedJobId: '',
                logs: [],
                batchProgress: '',
                fileFilter: 'all',
                fileSearch: '',
                files: [],
                filteredFiles: [],
                selectedFilePaths: [],
                filePage: {total: 0, currentCursor: null, nextCursor: null, cursorHistory: [], loading: false, searchTimer: null, selectionMode: 'explicit', selectionFilter: 'all', selectionQuery: '', excludedPaths: [], operation: null},
                dramaPlayer: {dramas: [], selectedDramaId: '', selectedEpisodeId: '', loading: false, error: '', playbackError: '', autoNext: true, speed: 1, lastSavedAt: 0},
                batchDeleteMode: false,
                filePreviewModal: null,
                previewFile: {status: 'idle', file: null, kind: '', url: '', content: '', message: ''},
                topicTranscriptPreviewModal: null,
                topicTranscriptPreview: {topic: null, episodes: [], selectedEpisode: null, loading: false, error: ''},
                configForm: {root: {}, items: []},
                configRootFields: [
                    {key: 'host', label: 'host', type: 'text'},
                    {key: 'video_filter', label: 'video_filter', type: 'checkbox'},
                    {key: 'sleep', label: 'sleep', type: 'number'},
                    {key: 'autoRun', label: 'autoRun', type: 'checkbox'},
                    {key: '默认平台', label: '默认平台', type: 'text'},
                    {key: '默认爬取类型', label: '默认爬取类型', type: 'text'},
                    {key: '默认起始页码', label: '默认起始页码', type: 'number'},
                    {key: '默认登录方式', label: '默认登录方式', type: 'text'},
                    {key: '默认保存方式', label: '默认保存方式', type: 'text'}
                ],
                backups: [],
                projectItems: [],
                serverIp: '',
                qrcodeStatus: { has_state: false, message: '' },
                qrcodeLoggingIn: false,
                audioModal: null,
                dramaDownloadModal: null,
                assignment: { topic: null, role: '', candidates: [], selectedUserId: '', saving: false, error: '' },
                audioAnalysis: {status: 'idle'},
                dramaAnalysis: {
                    title: '',
                    platform: 'hg',
                    candidates: [],
                    selected: null,
                    episodes: [],
                    episode: 0,
                    searchSource: '',
                    fallbackReason: '',
                    selectedAwemeIds: [],
                    allEpisodes: false,
                    relatedVideos: [],
                    missingEpisodes: [],
                    seriesRangeLabel: '',
                    seriesWarnings: [],
                    seriesLoading: false,
                    loading: false,
                    error: '',
                    jobId: '',
                    notifiedStatus: '',
                    interfaceTest: null,
                    settings: {config_url: '', has_activation_code: false},
                    activationCode: ''
                },
                creative: {platform: creativePlatformPreference(), items: [], loading: false, error: '', breakdown: null},
                topicLibrary: {platform: '', lifecycleStatus: '', category: '', minScore: '', hasLocked: '', items: [], loading: false, error: '', breakdown: null},
                adoptionNotice: {success: true, title: '', message: ''},
                adoptionNoticeModal: null,
                generatedContentPreview: {topic: null, project: null, stage: '', versions: [], selectedResourceId: '', snapshot: null, loading: false, error: ''},
                breakdownEditor: {source: '', title: '', record: null, content: {}, fieldGuide: [], saving: false, error: ''},
                breakdownGeneration: {topic: null, source: '', jobId: '', status: '', message: '', progress: {}, coverage: [], requiresTranscription: false, notifiedStatus: '', error: '', startedAt: 0, elapsedSeconds: 0},
                breakdownGenerationJobs: {},
                breakdownJobStatuses: {},
                dramaDownloadJobStatuses: {},
                hiddenDramaDownloadJobs: {},
                creativeWorkspace: {
                    projects: [], eligibleTopics: [], lockedBreakdowns: [], project: null, loading: false, error: '', step: 'blueprints',
                    create: {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20, creation_request_id: ''},
                    directionCount: 3, selectedBlueprintId: null, selectedEpisode: 1,
                    jobId: '', jobMessage: '', jobProgress: {}, jobStatus: '', paused: false, notifiedStatus: '',
                    jobLogs: [], jobLogCount: 0, showJobLogs: false,
                    jobStartedAt: 0, jobElapsedSeconds: 0,
                    startingAction: '', activeAction: '',
                    saveState: {}, notice: '', noticeType: 'success', noticeTimer: null
                },
                dramaDownload: {title: '', topic: null, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], relatedVideos: [], missingEpisodes: [], seriesRangeLabel: '', seriesWarnings: [], selectedEpisodes: [], allEpisodes: false, episodesExpanded: true, searching: false, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false, showFiles: false, logs: [], logCount: 0, videoPreviewPath: '', videoPreviewUrl: '', videoPreviewError: ''},
                dramaReviews: {topic: null, title: '', platform: 'hg', limit: 100, allReviews: false, format: 'csv', jobId: '', status: '', reviewCount: 0, sourceTotal: 0, items: [], loading: false, polling: false, error: ''},
                dramaReviewJobs: {},
                analyticsOverview: {},
                analyticsTags: {categories: [], selling_points: [], hooks: [], plot_tags: []},
                analyticsTrends: {points: []},
                analyticsSentiment: [],
                analyticsDramas: [],
                analyticsPlatform: '',
                analyticsDate: localDateString(),
                analyticsLoading: false,
                analyticsError: '',
                analyticsSyncResult: null,
                todayRankPlatform: 'dy',
                todayRankDate: '',
                todayRankSectionKey: '榜单',
                todayRankRankingKey: '',
                todayRankData: null,
                todayRankLoading: false,
                todayRankError: '',
                todayRankRefreshing: false,
                todayRankRefreshJobId: '',
                todayRankRefreshMessage: '',
                rankingActiveTables: {},
                rankAdoptingRecordIds: {},
                rankAdoptedTitleKeys: {},
                modalMode: 'crawl',
                modalFile: '',
                modalProjects: [],
                selectedModalProjects: [],
                projectModal: null,
                autoUpload: true,
                config: {
                    platform: 'dy',
                    login_type: 'qrcode',
                    crawler_type: 'search',
                    keywords: '',
                    start_page: 1,
                    headless: false,
                    save_option: 'json',
                    get_comment: false,
                    get_sub_comment: false,
                    specified_ids: '',
                    creator_ids: '',
                    cookie: '',
                    dy_channel: 'video',
                    dy_sort: 'latest',
                    dy_publish_time: '1_week',
                    dy_rank_limit: 50,
                    dy_rank_comment_limit: 10,
                    dy_rank_login_wait_seconds: 0,
                    autoRun: false,
                    projects: {}
                },

                modal(id) {
                    const element = document.getElementById(id);
                    return element ? bootstrap.Modal.getOrCreateInstance(element) : null;
                },

                showAdoptionNotice(success, title, message) {
                    this.adoptionNotice = {success: Boolean(success), title: title || '', message: message || ''};
                    this.adoptionNoticeModal = this.modal('adoptionNoticeModal');
                    this.adoptionNoticeModal?.show();
                },

                async init() {
                    this.projectModal = this.modal('projectModal');
                    this.audioModal = this.modal('audioModal');
                    this.filePreviewModal = this.modal('filePreviewModal');
                    document.getElementById('filePreviewModal')?.addEventListener('hidden.bs.modal', () => this.closeFilePreview());
                    this.topicTranscriptPreviewModal = this.modal('topicTranscriptPreviewModal');
                    this.dramaReviewCaptureModal = this.modal('dramaReviewCaptureModal');
                    document.getElementById('dramaReviewCaptureModal')?.addEventListener(
                        'hidden.bs.modal',
                        () => this.finishDisplayedDramaReviewJob?.()
                    );
                    this.dramaDownloadModal = this.modal('dramaDownloadModal');
                    document.getElementById('dramaDownloadModal')?.addEventListener('hide.bs.modal', () => {
                        document.activeElement?.blur();
                    });
                    document.getElementById('dramaDownloadModal')?.addEventListener('hidden.bs.modal', () => {
                        if (this.dramaDownload.jobId && !this.dramaDownload.finished) this.dramaDownload.background = true;
                    });
                    this.breakdownEditorModal = this.modal('breakdownEditorModal');
                    this.transcriptRequiredModal = this.modal('transcriptRequiredModal');
                    this.breakdownGenerationProgressModal = this.modal('breakdownGenerationProgressModal');
                    this.creativeGenerationProgressModal = this.modal('creativeGenerationProgressModal');
                    document.getElementById('creativeGenerationProgressModal')?.addEventListener('hide.bs.modal', () => {
                        document.activeElement?.blur();
                    });
                    this.workflowTaskModal = this.modal('workflowTaskModal');
                    this.planningAssignmentModal = this.modal('planningAssignmentModal');
                    this.adoptionNoticeModal = this.modal('adoptionNoticeModal');

                    if (['tasks', 'review-tasks'].includes(this.page)) {
                        await this.fetchWorkflowTasks();
                        document.getElementById('refresh-review-tasks')?.addEventListener(
                            'click',
                            () => this.fetchWorkflowTasks()
                        );
                    }

                    await this.fetchJobs();
                    if (this.page === 'selection-ranks') {
                        this.parseTodayRankHash(false);
                        window.addEventListener('hashchange', () => this.parseTodayRankHash(true));
                        await this.fetchTodayRank();
                    }
                    if (this.page === 'selection-dashboard') {
                        await this.fetchCreativeTopics();
                        await this.fetchTopicLibrary();
                    } else if (this.page === 'selection-hits') {
                        await this.generateCreativeTopics();
                    } else if (this.page === 'selection-topics') {
                        if (typeof this.fetchTopicLibrary !== 'function') {
                            throw new Error('选题模块加载失败，请刷新页面后重试');
                        }
                        await this.fetchTopicLibrary();
                    }
                    if (['selection-hits', 'selection-topics'].includes(this.page)) {
                        this.restoreLatestActiveDramaDownload?.();
                    }
                    if (this.page.startsWith('planning-') || this.page.startsWith('writing-')) {
                        const queryProjectId = new URLSearchParams(window.location.search).get('project_id');
                        const pathProjectId = window.location.pathname.match(/\/projects\/([^/]+)/)?.[1];
                        await this.fetchCreativeProjects(queryProjectId || pathProjectId || null);
                        const topicId = new URLSearchParams(window.location.search).get('topic_id');
                        if (this.page === 'planning-projects' && topicId) {
                            this.creativeWorkspace.create.topic_id = Number(topicId);
                            await this.syncCreativeProjectSource();
                        }
                        const episode = window.location.pathname.match(/\/episodes\/(\d+)\/(?:beats|scripts)/)?.[1];
                        if (episode) this.creativeWorkspace.selectedEpisode = Number(episode);
                        if (
                            this.page === 'planning-brief'
                            && new URLSearchParams(window.location.search).get('task_setup') === '1'
                        ) {
                            await this.openCreativeTaskSetup();
                        }
                    }
                    if (this.page === 'admin-config') {
                        await this.loadConfig();
                        await this.fetchConfigForm();
                        await this.fetchBackups();
                        await this.fetchDramaSettings();
                    }
                    if (this.page === 'admin-data') await this.fetchFiles();
                    if (this.page === 'admin-drama-player') await this.fetchDramaPlayerLibrary();
                    if (this.page === 'admin-analytics') await this.refreshAnalyticsDashboard(false);
                    if (this.page === 'admin-jobs') {
                        await this.loadConfig();
                        await this.fetchQrcodeStatus();
                        const jobId = new URLSearchParams(window.location.search).get('job_id');
                        if (jobId) await this.selectJob(jobId);
                    }
                    this.initTooltips();
                    if (this.page === 'admin-jobs') setInterval(() => this.fetchLogs(), 1000);
                    setInterval(() => this.fetchJobs(), 2000);
                    if (this.tickCreativeGenerationElapsed) setInterval(() => this.tickCreativeGenerationElapsed(), 1000);
                    if (this.tickBreakdownGenerationElapsed) setInterval(() => this.tickBreakdownGenerationElapsed(), 1000);
                },

                initTooltips() {
                    document.querySelectorAll('[data-bs-toggle="tooltip"]').forEach(element => {
                        bootstrap.Tooltip.getOrCreateInstance(element);
                    });
                },

                async fetchWorkflowTasks() {
                    const container = document.getElementById('review-task-list');
                    if (!container) return;
                    container.className = 'alert alert-light border';
                    container.textContent = '正在加载任务...';
                    this.workflowTaskError = '';
                    try {
                        const response = await aiworkerApi.request('/api/workflow/tasks');
                        if (!response.ok) throw new Error('任务加载失败');
                        const approvalStages = new Set(['brief', 'characters', 'outline', 'beats', 'delivery']);
                        this.workflowTasks = (await response.json()).filter(task => approvalStages.has(task.task_type));
                        this.renderWorkflowTaskRows(container);
                    } catch (error) {
                        this.workflowTaskError = error.message || '任务加载失败';
                        container.className = 'alert alert-danger';
                        container.textContent = this.workflowTaskError;
                    }
                },

                renderWorkflowTaskRows(container) {
                    container.className = '';
                    container.replaceChildren();
                    const groups = [
                        {title: '待领取', states: ['pool']},
                        {title: '处理中', states: ['assigned', 'in_progress']},
                    ];
                    let visibleRows = 0;
                    groups.forEach(group => {
                        const tasks = this.workflowTasks.filter(task => group.states.includes(task.status));
                        if (!tasks.length) return;
                        visibleRows += tasks.length;
                        const section = document.createElement('section');
                        section.className = 'mb-4';
                        const heading = document.createElement('h2');
                        heading.className = 'h6 mb-2';
                        heading.textContent = `${group.title} (${tasks.length})`;
                        section.appendChild(heading);
                        const tableWrap = document.createElement('div');
                        tableWrap.className = 'table-responsive border';
                        const table = document.createElement('table');
                        table.className = 'table table-sm align-middle mb-0';
                        const body = document.createElement('tbody');
                        tasks.forEach(task => body.appendChild(this.workflowTaskRow(task)));
                        table.appendChild(body);
                        tableWrap.appendChild(table);
                        section.appendChild(tableWrap);
                        container.appendChild(section);
                    });
                    if (!visibleRows) {
                        container.className = 'alert alert-light border';
                        container.textContent = '暂无待处理任务。';
                    }
                },

                workflowTaskRow(task) {
                    const row = document.createElement('tr');
                    row.className = 'workflow-task-row';
                    const identity = document.createElement('td');
                    const title = document.createElement('div');
                    title.className = 'fw-semibold';
                    title.textContent = task.task_type;
                    const context = document.createElement('div');
                    context.className = 'small text-muted';
                    context.textContent = [task.project_id, task.role_code].filter(Boolean).join(' · ');
                    identity.append(title, context);
                    const statusCell = document.createElement('td');
                    statusCell.className = 'text-nowrap';
                    statusCell.textContent = task.status;
                    const actions = document.createElement('td');
                    actions.className = 'text-end text-nowrap';
                    if (task.status === 'pool' && task.can_claim) {
                        actions.appendChild(this.workflowTaskButton('bi-person-check', '领取', () => this.claimWorkflowTask(task)));
                    }
                    if (task.status === 'pool' && task.can_assign) {
                        actions.appendChild(this.workflowTaskButton('bi-person-plus', '分配', () => this.openWorkflowTaskWindow(task)));
                    }
                    if (task.status === 'assigned' && task.can_start) {
                        actions.appendChild(this.workflowTaskButton('bi-play-fill', '开始', () => this.startWorkflowTask(task)));
                    }
                    row.append(identity, statusCell, actions);
                    return row;
                },

                workflowTaskButton(icon, label, action) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'btn btn-sm btn-outline-secondary ms-1';
                    button.title = label;
                    button.setAttribute('aria-label', label);
                    const iconElement = document.createElement('i');
                    iconElement.className = `bi ${icon}`;
                    iconElement.setAttribute('aria-hidden', 'true');
                    button.appendChild(iconElement);
                    button.addEventListener('click', action);
                    return button;
                },

                async claimWorkflowTask(task) {
                    if (this.workflowTaskWindow.task?.id === task.id) {
                        await this.runWorkflowTaskWindowAction(
                            `/api/workflow/tasks/${task.id}/claim`,
                            '任务已领取'
                        );
                        return;
                    }
                    await this.runWorkflowTaskWrite(`/api/workflow/tasks/${task.id}/claim`);
                },

                workflowTaskStatusLabel(status) {
                    return {
                        pool: '待领取', assigned: '已分配', in_progress: '处理中',
                        completed: '已完成', cancelled: '已取消'
                    }[status] || status || '-';
                },

                stageWorkflowAssignmentLabel(task) {
                    return {
                        brief: '创作 Brief',
                        characters: '人物关系',
                        outline: '分集大纲',
                        beats: 'Beats 脚本'
                    }[task?.task_type] || task?.task_type || '工作流';
                },

                workflowTaskRoleLabel(task) {
                    return {
                        creative_planner: '创意策划',
                        screenwriter: '编剧'
                    }[task?.role_code] || task?.role_code || '执行人';
                },

                async openProjectTaskWindow(projectId, taskType = 'brief') {
                    this.workflowTaskWindow = {
                        task: null,
                        candidates: [],
                        selectedAssigneeId: '',
                        loading: true,
                        saving: false,
                        error: ''
                    };
                    this.workflowTaskModal = this.modal('workflowTaskModal');
                    this.workflowTaskModal?.show();
                    try {
                        const response = await aiworkerApi.request('/api/workflow/tasks');
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '读取项目任务失败');
                        const task = (payload || []).find(item =>
                            item.task_type === taskType
                            && String(item.project_id || '') === String(projectId)
                            && !['completed', 'cancelled'].includes(item.status)
                        );
                        if (!task) throw new Error('当前账号没有可操作的 Brief 任务');
                        await this.openWorkflowTaskWindow(task, false);
                    } catch (error) {
                        this.workflowTaskWindow.error = error.message || '读取项目任务失败';
                    } finally {
                        this.workflowTaskWindow.loading = false;
                    }
                },

                async openWorkflowTaskWindow(task, showModal = true) {
                    this.workflowTaskWindow.task = task;
                    this.workflowTaskWindow.candidates = [];
                    this.workflowTaskWindow.selectedAssigneeId = '';
                    this.workflowTaskWindow.error = '';
                    this.workflowTaskWindow.loading = Boolean(task.can_assign);
                    if (showModal) {
                        this.workflowTaskModal = this.modal('workflowTaskModal');
                        this.workflowTaskModal?.show();
                    }
                    if (!task.can_assign) {
                        this.workflowTaskWindow.loading = false;
                        return;
                    }
                    try {
                        const response = await aiworkerApi.request(
                            `/api/workflow/tasks/${task.id}/assignment-candidates`
                        );
                        const payload = await response.json();
                        if (!response.ok) throw new Error(payload.detail || '读取可分配用户失败');
                        this.workflowTaskWindow.candidates = payload || [];
                        this.workflowTaskWindow.selectedAssigneeId = payload[0]?.user_id || '';
                    } catch (error) {
                        this.workflowTaskWindow.error = error.message || '读取可分配用户失败';
                    } finally {
                        this.workflowTaskWindow.loading = false;
                    }
                },

                selectedWorkflowTaskCandidate() {
                    return this.workflowTaskWindow.candidates.find(
                        item => item.user_id === this.workflowTaskWindow.selectedAssigneeId
                    ) || null;
                },

                async assignWorkflowTask() {
                    const task = this.workflowTaskWindow.task;
                    const candidate = this.selectedWorkflowTaskCandidate();
                    if (!task || !candidate || this.workflowTaskWindow.saving) return;
                    this.workflowTaskWindow.saving = true;
                    this.workflowTaskWindow.error = '';
                    try {
                        if (!candidate.is_project_member && task.project_id) {
                            const memberResponse = await aiworkerApi.request(
                                `/api/workflow/projects/${task.project_id}/members`,
                                {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({
                                        user_id: candidate.user_id,
                                        project_role: task.role_code,
                                        status: 'active'
                                    })
                                }
                            );
                            const memberPayload = await memberResponse.json().catch(() => ({}));
                            if (!memberResponse.ok) throw new Error(memberPayload.detail || '添加项目成员失败');
                        }
                        await this.runWorkflowTaskWindowAction(
                            `/api/workflow/tasks/${task.id}/assign`,
                            '任务已分配',
                            {
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({assignee_user_id: candidate.user_id})
                            }
                        );
                    } catch (error) {
                        this.workflowTaskWindow.error = error.message || '分配任务失败';
                    } finally {
                        this.workflowTaskWindow.saving = false;
                    }
                },

                async startWorkflowTaskFromWindow() {
                    const task = this.workflowTaskWindow.task;
                    if (!task) return;
                    await this.runWorkflowTaskWindowAction(
                        `/api/workflow/tasks/${task.id}/start`,
                        '任务已开始'
                    );
                },

                async runWorkflowTaskWindowAction(url, message, options = {}) {
                    this.workflowTaskWindow.saving = true;
                    this.workflowTaskWindow.error = '';
                    try {
                        const response = await aiworkerApi.request(url, {method: 'POST', ...options});
                        const payload = await response.json().catch(() => ({}));
                        if (!response.ok) throw new Error(payload.detail || '任务操作失败');
                        this.workflowTaskWindow.task = payload;
                        this.workflowTaskWindow.candidates = [];
                        if (this.fetchWorkflowTasks && ['tasks', 'review-tasks'].includes(this.page)) {
                            await this.fetchWorkflowTasks();
                        }
                        this.showCreativeNotice?.(message, 'success');
                    } catch (error) {
                        this.workflowTaskWindow.error = error.message || '任务操作失败';
                    } finally {
                        this.workflowTaskWindow.saving = false;
                    }
                },

                async startWorkflowTask(task) {
                    await this.runWorkflowTaskWrite(`/api/workflow/tasks/${task.id}/start`);
                },


                async runWorkflowTaskWrite(url, options = {}) {
                    const response = await aiworkerApi.request(url, {method: 'POST', ...options});
                    if (!response.ok) {
                        const payload = await response.json().catch(() => ({}));
                        throw new Error(payload.detail || '任务操作失败');
                    }
                    await this.fetchWorkflowTasks();
                },

                async fetchServerIp() {
                    try {
                        const res = await aiworkerApi.request('/api/server/ip');
                        const data = await res.json();
                        if (data.ip) this.serverIp = `${data.ip}:${data.port}`;
                    } catch (e) {}
                },

                async loadConfig() {
                    try {
                        const res = await aiworkerApi.request('/api/config');
                        const data = await res.json();
                        const source = Array.isArray(data) ? (data[0] || {}) : (data || {});
                        this.projectItems = source.items || [];
                        this.config.projects = {};
                        this.projectItems.forEach(item => {
                            if (this.config.projects[item.project] === undefined) {
                                this.config.projects[item.project] = true;
                            }
                        });
                        if (source.autoRun !== undefined) this.config.autoRun = source.autoRun;
                        this.jobs = data.jobs || [];
                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
                        if (!this.selectedJobId && this.jobs.length > 0) this.selectedJobId = this.jobs[0].job_id;
                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
                            if (!this.selectedJobId) this.logs = [];
                        }
                    } catch (e) {}
                },

                async fetchJobs() {
                    try {
                        const res = await aiworkerApi.request('/api/jobs');
                        const data = await res.json();
                        this.jobs = data.jobs || [];
                        this.isRunning = this.jobs.some(job => this.isJobRunning(job));
                        if (!this.selectedJobId && this.jobs.length > 0) {
                            this.selectedJobId = this.jobs[0].job_id;
                        }
                        if (this.selectedJobId && !this.jobs.some(job => job.job_id === this.selectedJobId)) {
                            this.selectedJobId = this.jobs.length > 0 ? this.jobs[0].job_id : '';
                            if (!this.selectedJobId) this.logs = [];
                        }
                        this.notifyDramaJobCompletion?.();
                        await this.syncBackgroundDramaDownloadJobs?.();
                        this.syncDramaDownloadProgress?.();
                        this.syncDramaDownloadLogs?.();
                        if (this.syncCreativeJobProgress) await this.syncCreativeJobProgress();
                        this.syncBreakdownGenerationProgress?.();
                    } catch (e) {
                        this.jobs = [];
                        this.isRunning = false;
                    }
                },

                syncDramaDownloadLogs() {
                    const jobId = this.dramaDownload?.jobId;
                    if (!jobId) return;
                    const job = this.jobs.find(j => j.job_id === jobId);
                    if (job) this.dramaDownload.logCount = job.log_count || 0;
                    aiworkerApi.request(`/api/jobs/${jobId}/logs`).then(res => res.json()).then(data => {
                        this.dramaDownload.logs = data.logs || [];
                    }).catch(() => {});
                },

                async fetchLogs() {
                    try {
                        if (this.selectedJobId) {
                            const res = await aiworkerApi.request(`/api/jobs/${this.selectedJobId}/logs`);
                            if (res.status === 404) {
                                this.logs = [];
                                this.selectedJobId = '';
                                await this.fetchJobs();
                                if (!this.selectedJobId) {
                                    const latest = await aiworkerApi.request('/api/logs');
                                    const data = await latest.json();
                                    this.logs = data.logs || [];
                                    if (data.job_id) this.selectedJobId = data.job_id;
                                    this.batchProgress = data.batch_progress || '';
                                    this.isRunning = data.is_running || false;
                                }
                                return;
                            }
                            const data = await res.json();
                            this.logs = data.logs || [];
                            if (data.job_id && data.job_id !== this.selectedJobId) this.selectedJobId = data.job_id;
                        } else {
                            const res = await aiworkerApi.request('/api/logs');
                            const data = await res.json();
                            this.logs = data.logs || [];
                            if (data.job_id) this.selectedJobId = data.job_id;
                            this.batchProgress = data.batch_progress || '';
                            this.isRunning = data.is_running || false;
                        }
                        this.$nextTick(() => {
                            const el = document.getElementById('logConsole');
                            if (el) el.scrollTop = el.scrollHeight;
                        });
                    } catch (e) {}
                },

                async selectJob(jobId) {
                    this.selectedJobId = jobId;
                    await this.fetchLogs();
                },

                isJobRunning(job) {
                    return ['pending', 'running', 'stopping'].includes(job.status);
                },

                jobStatusClass(status) {
                    if (status === 'succeeded') return 'bg-success';
                    if (status === 'failed') return 'bg-danger';
                    if (status === 'cancelled' || status === 'stopping') return 'bg-warning';
                    if (status === 'running') return 'bg-primary';
                    return 'bg-secondary';
                },

                logClass(log) {
                    if (String(log).includes('[ERROR]')) return 'text-danger';
                    if (String(log).includes('[WARNING]')) return 'text-warning';
                    if (String(log).includes('[SUCCESS]')) return 'text-success';
                    return 'text-light';
                },

                logsText() {
                    return (this.logs || []).join('\n');
                },

                async copyLogs() {
                    const text = this.logsText();
                    if (!text) return;
                    try {
                        await navigator.clipboard.writeText(text);
                        alert(`日志已复制，共 ${this.logs.length} 行`);
                    } catch (e) {
                        try {
                            const textarea = document.createElement('textarea');
                            textarea.value = text;
                            textarea.style.position = 'fixed';
                            textarea.style.opacity = '0';
                            document.body.appendChild(textarea);
                            textarea.select();
                            const copied = document.execCommand('copy');
                            document.body.removeChild(textarea);
                            if (!copied) throw new Error('copy command failed');
                            alert(`日志已复制，共 ${this.logs.length} 行`);
                        } catch (fallbackError) {
                            alert('复制失败，请手动选择日志内容复制');
                        }
                    }
                },

                downloadLogs() {
                    const text = this.logsText();
                    if (!text) return;
                    const blob = new Blob([text + '\n'], {type: 'text/plain;charset=utf-8'});
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    const jobId = this.selectedJobId || 'latest';
                    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
                    link.href = url;
                    link.download = `${jobId}-${stamp}.log`;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                    alert('日志下载已开始');
                },

                selectedProjectCount() {
                    return Object.values(this.config.projects || {}).filter(Boolean).length;
                },

                async runCrawler() {
                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
                        alert('榜单爬取仅支持抖音和红果');
                        return;
                    }
                    if (this.config.crawler_type === 'search') {
                        const keywords = this.projectItems
                            .filter(item => this.config.projects[item.project])
                            .map(item => item['搜索关键词'] || item.keywords || '')
                            .join(',');
                        this.config.keywords = keywords;
                    }
                    try {
                        const res = await aiworkerApi.request('/api/run', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(this.config)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') {
                            alert(data.message);
                            return;
                        }
                        if (data.job && data.job.job_id) this.selectedJobId = data.job.job_id;
                        this.logs = ['[SYSTEM] 正在启动...'];
                    } catch (e) {
                        alert('启动失败');
                        return;
                    }
                    await this.fetchJobs();
                    await this.fetchLogs();
                },

                async runRankCrawler(platform) {
                    const previousPlatform = this.config.platform;
                    const previousType = this.config.crawler_type;
                    this.config.platform = platform;
                    this.config.crawler_type = 'rank';
                    await this.runCrawler();
                    this.config.platform = previousPlatform;
                    this.config.crawler_type = previousType;
                },

                async stopJob(jobId) {
                    await aiworkerApi.request(`/api/jobs/${jobId}/stop`, {method: 'POST'});
                    await this.fetchJobs();
                    await this.fetchLogs();
                },

                async stopCrawler() {
                    if (this.selectedJobId) {
                        await this.stopJob(this.selectedJobId);
                        return;
                    }
                    await aiworkerApi.request('/api/stop', {method: 'POST'});
                    await this.fetchJobs();
                    await this.fetchLogs();
                },

                async deleteJob(jobId) {
                    const job = this.jobs.find(item => item.job_id === jobId);
                    if (job && this.isJobRunning(job)) {
                        alert('Job is still running. Stop it first.');
                        return;
                    }
                    if (!confirm(`Delete job ${jobId}? Logs will be removed from the task list.`)) return;
                    const res = await aiworkerApi.request(`/api/jobs/${jobId}/delete`, {method: 'POST'});
                    const data = await res.json().catch(() => ({}));
                    if (!res.ok || data.status === 'error') {
                        alert(data.message || 'Delete failed');
                        return;
                    }
                    if (this.selectedJobId === jobId) {
                        this.selectedJobId = '';
                        this.logs = [];
                    }
                    this.jobs = this.jobs.filter(item => item.job_id !== jobId);
                    await this.fetchJobs();
                    await this.fetchLogs();
                },

                formatSize(bytes) {
                    if (!bytes) return '0 B';
                    const units = ['B', 'KB', 'MB', 'GB'];
                    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
                    return `${(bytes / Math.pow(1024, index)).toFixed(index ? 1 : 0)} ${units[index]}`;
                },

                formatDate(timestamp) {
                    if (!timestamp) return '';
                    return new Date(timestamp * 1000).toLocaleString();
                },

                downloadFile(path) {
                    window.open(`/api/file/download?path=${encodeURIComponent(path)}`, '_blank');
                },

            };
            const modules = window.aiworkerModules || [];
            const moduleCapabilities = Object.assign({}, ...modules.map((factory) => factory()));
            Object.assign(workspace, moduleCapabilities);
            if (
                ['selection-dashboard', 'selection-hits', 'selection-topics'].includes(workspace.page)
                && typeof workspace.fetchTopicLibrary !== 'function'
            ) {
                throw new Error('选题模块加载失败，请刷新页面后重试');
            }
            return workspace;
        }
