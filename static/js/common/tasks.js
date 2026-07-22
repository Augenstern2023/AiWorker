function localDateString(date = new Date()) {
            const year = date.getFullYear();
            const month = String(date.getMonth() + 1).padStart(2, '0');
            const day = String(date.getDate()).padStart(2, '0');
            return `${year}-${month}-${day}`;
        }

        function appData() {
            const workspace = {
                page: document.body.dataset.workspacePage || '',
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
                audioAnalysis: {status: 'idle'},
                dramaAnalysis: {
                    title: '',
                    candidates: [],
                    selected: null,
                    episodes: [],
                    episode: 0,
                    suggestions: [],
                    suggestionLoading: false,
                    suggestionTimer: null,
                    suggestionRequestId: 0,
                    searchSource: '',
                    fallbackReason: '',
                    loading: false,
                    error: '',
                    jobId: '',
                    notifiedStatus: '',
                    interfaceTest: null,
                    settings: {config_url: '', has_activation_code: false},
                    activationCode: ''
                },
                creative: {platform: 'dy', items: [], loading: false, error: '', breakdown: null},
                topicLibrary: {platform: '', lifecycleStatus: '', category: '', minScore: '', hasLocked: '', items: [], loading: false, error: '', breakdown: null},
                breakdownEditor: {source: '', title: '', record: null, content: {}, fieldGuide: [], saving: false, error: ''},
                breakdownGeneration: {topic: null, source: '', jobId: '', status: '', message: '', progress: {}, coverage: [], requiresTranscription: false, notifiedStatus: '', error: '', startedAt: 0, elapsedSeconds: 0},
                creativeWorkspace: {
                    projects: [], eligibleTopics: [], lockedBreakdowns: [], project: null, loading: false, error: '', step: 'blueprints',
                    create: {topic_id: '', breakdown_id: '', name: '', target_platform: '', target_episode_count: 20},
                    directionCount: 3, selectedBlueprintId: null, selectedEpisode: 1,
                    jobId: '', jobMessage: '', jobProgress: {}, jobStatus: '', paused: false, notifiedStatus: '',
                    jobStartedAt: 0, jobElapsedSeconds: 0,
                    startingAction: '', activeAction: '',
                    saveState: {}, notice: '', noticeType: 'success', noticeTimer: null
                },
                dramaDownload: {title: '', topic: null, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], selectedEpisodes: [], allEpisodes: false, searching: false, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false},
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
                todayRankSectionKey: '',
                todayRankRankingKey: '',
                todayRankData: null,
                todayRankLoading: false,
                todayRankError: '',
                rankingActiveTables: {},
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

                async init() {
                    this.projectModal = this.modal('projectModal');
                    this.audioModal = this.modal('audioModal');
                    this.filePreviewModal = this.modal('filePreviewModal');
                    document.getElementById('filePreviewModal')?.addEventListener('hidden.bs.modal', () => this.closeFilePreview());
                    this.topicTranscriptPreviewModal = this.modal('topicTranscriptPreviewModal');
                    this.dramaDownloadModal = this.modal('dramaDownloadModal');
                    document.getElementById('dramaDownloadModal')?.addEventListener('hidden.bs.modal', () => {
                        if (this.dramaDownload.jobId) this.dramaDownload.background = true;
                    });
                    this.breakdownEditorModal = this.modal('breakdownEditorModal');
                    this.transcriptRequiredModal = this.modal('transcriptRequiredModal');
                    this.breakdownGenerationProgressModal = this.modal('breakdownGenerationProgressModal');
                    this.creativeGenerationProgressModal = this.modal('creativeGenerationProgressModal');

                    await this.fetchJobs();
                    if (this.page === 'selection-ranks' || this.page === 'selection-dashboard') {
                        this.parseTodayRankHash(false);
                        window.addEventListener('hashchange', () => this.parseTodayRankHash(true));
                        await this.fetchTodayRank();
                    }
                    if (['selection-dashboard', 'selection-topics', 'selection-breakdowns'].includes(this.page)) {
                        await this.fetchCreativeTopics();
                        await this.fetchTopicLibrary();
                        this.restoreLatestActiveDramaDownload?.();
                    }
                    if (this.page === 'selection-dramas') await this.fetchDramaSettings();
                    if (this.page.startsWith('planning-') || this.page.startsWith('writing-') || this.page === 'admin-deliveries') {
                        const queryProjectId = new URLSearchParams(window.location.search).get('project_id');
                        const pathProjectId = window.location.pathname.match(/\/projects\/([^/]+)/)?.[1];
                        await this.fetchCreativeProjects(queryProjectId || pathProjectId || null);
                        const topicId = new URLSearchParams(window.location.search).get('topic_id');
                        if (this.page === 'planning-projects' && topicId) {
                            this.creativeWorkspace.create.topic_id = Number(topicId);
                            await this.syncCreativeProjectSource();
                        }
                        const episode = window.location.pathname.match(/\/episodes\/(\d+)\/beats/)?.[1];
                        if (episode) this.creativeWorkspace.selectedEpisode = Number(episode);
                    }
                    if (this.page === 'admin-config') {
                        await this.loadConfig();
                        await this.fetchConfigForm();
                        await this.fetchBackups();
                    }
                    if (this.page === 'admin-data') await this.fetchFiles();
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
                        this.syncDramaDownloadProgress?.();
                        if (this.syncCreativeJobProgress) await this.syncCreativeJobProgress();
                        this.syncBreakdownGenerationProgress?.();
                    } catch (e) {
                        this.jobs = [];
                        this.isRunning = false;
                    }
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
            return Object.assign(workspace, ...modules.map((factory) => factory()));
        }
