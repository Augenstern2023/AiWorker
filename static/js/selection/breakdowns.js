window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async updateTopicLibrary(topic, status) {
                    this.topicLibrary.error = '';
                    try {
                        const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
                            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok) {
                            this.topicLibrary.error = data.detail || '更新选题状态失败';
                            return;
                        }
                        Object.assign(topic, data);
                        await this.fetchTopicLibrary();
                    } catch (error) {
                        this.topicLibrary.error = error.message || '更新选题状态失败';
                    }
                },

                createTopicLibraryBreakdown(topic) {
                    return this.startTranscriptBreakdown(
                        topic,
                        'topic-library'
                    );
                },

                async openTopicTranscriptPreview(topic) {
                    this.topicTranscriptPreview = {topic, episodes: [], selectedEpisode: null, loading: true, error: ''};
                    this.topicTranscriptPreviewModal.show();
                    try {
                        const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}/transcripts`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取剧目转写失败');
                        this.topicTranscriptPreview.episodes = data.episodes || [];
                        this.topicTranscriptPreview.selectedEpisode = this.topicTranscriptPreview.episodes[0]?.episode_number || null;
                    } catch (e) {
                        this.topicTranscriptPreview.error = e.message || '读取剧目转写失败';
                    } finally {
                        this.topicTranscriptPreview.loading = false;
                    }
                },

                selectedTopicTranscript() {
                    return this.topicTranscriptPreview.episodes.find(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    ) || null;
                },

                canMoveTopicTranscript(offset) {
                    const episodes = this.topicTranscriptPreview.episodes;
                    const index = episodes.findIndex(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    );
                    const target = index + Number(offset || 0);
                    return index >= 0 && target >= 0 && target < episodes.length;
                },

                moveTopicTranscript(offset) {
                    if (!this.canMoveTopicTranscript(offset)) return;
                    const episodes = this.topicTranscriptPreview.episodes;
                    const index = episodes.findIndex(
                        item => Number(item.episode_number) === Number(this.topicTranscriptPreview.selectedEpisode)
                    );
                    this.topicTranscriptPreview.selectedEpisode = episodes[index + Number(offset)].episode_number;
                },

                transcriptSourceLabel(source) {
                    return {script: '剧本转写', transcript: '语音转写', text: '文本转写'}[source] || '剧目转写';
                },

                async startTranscriptBreakdown(topic, source, forceRegenerate = false) {
                    if (!topic?.id) return;
                    if (source === 'topic-library') this.topicLibrary.error = '';
                    if (source === 'creative') this.creative.error = '';
                    this.breakdownGeneration = {
                        topic,
                        source,
                        jobId: '',
                        status: 'pending',
                        message: '正在检查已下载的剧目脚本',
                        progress: {completed: 0, total: 1},
                        coverage: [],
                        requiresTranscription: false,
                        notifiedStatus: '',
                        error: '',
                        startedAt: Date.now(),
                        elapsedSeconds: 0,
                        logs: [],
                        logCount: 0,
                        showLogs: false
                    };
                    try {
                        const res = await aiworkerApi.request('/api/creative/breakdowns', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({topic_id: topic.id, force_regenerate: forceRegenerate})
                        });
                        const data = await res.json();
                        const detail = data.detail;
                        if (res.status === 409 && detail?.code === 'transcript_required') {
                            this.breakdownGeneration.status = 'blocked';
                            this.breakdownGeneration.message = detail.message;
                            this.transcriptRequiredModal.show();
                            return;
                        }
                        if (!res.ok || data.status === 'error') {
                            throw new Error(typeof detail === 'string' ? detail : detail?.message || data.message || '生成拆解失败');
                        }
                        if (data.status === 'existing' && data.breakdown) {
                            this.breakdownGeneration.status = 'existing';
                            this.breakdownGeneration.message = '已加载最新拆解版本';
                            this.breakdownGeneration.startedAt = 0;
                            if (source === 'topic-library') this.topicLibrary.breakdown = data.breakdown;
                            else this.creative.breakdown = data.breakdown;
                            this.openBreakdownEditor(source, topic.title, data.breakdown);
                            return;
                        }
                        this.breakdownGeneration.jobId = data.job.job_id;
                        this.breakdownGeneration.status = data.job.status || 'pending';
                        this.breakdownGeneration.message = data.requires_transcription
                            ? '已发现下载视频，正在生成剧本转写'
                            : (data.job.message || '已加载剧目脚本，正在生成个性化拆解');
                        this.breakdownGeneration.progress = data.job.progress || {completed: 0, total: 1};
                        this.breakdownGeneration.coverage = data.coverage || [];
                        this.breakdownGeneration.requiresTranscription = Boolean(data.requires_transcription);
                        this.breakdownGeneration.startedAt = Number(data.job.started_at || 0) > 0
                            ? Number(data.job.started_at) * 1000
                            : Date.now();
                        this.breakdownGenerationJobs[data.job.job_id] = {
                            topic,
                            source,
                            notifiedStatus: ''
                        };
                        this.breakdownJobStatuses[data.job.job_id] = this.breakdownGeneration.status;
                        this.breakdownGenerationProgressModal.show();
                        if (source === 'topic-library') await this.fetchTopicLibrary();
                        if (source === 'creative') await this.fetchCreativeTopics();
                        await this.fetchJobs();
                    } catch (e) {
                        const message = e.message || '生成拆解失败';
                        this.breakdownGeneration.status = 'failed';
                        this.breakdownGeneration.message = message;
                        this.breakdownGeneration.error = message;
                        if (source === 'topic-library') this.topicLibrary.error = message;
                        else this.creative.error = message;
                        this.breakdownGenerationProgressModal.show();
                    }
                },

                async syncBreakdownGenerationProgress() {
                    await this.syncBackgroundBreakdownJobs();
                    if (!this.breakdownGeneration.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.breakdownGeneration.jobId);
                    if (!job) return;
                    this.breakdownGeneration.status = job.status;
                    this.breakdownGeneration.message = job.message || this.breakdownGeneration.message;
                    this.breakdownGeneration.progress = job.progress || this.breakdownGeneration.progress || {};
                    this.breakdownGeneration.logCount = Number(job.log_count || 0);
                    if (this.breakdownGeneration.showLogs) await this.syncBreakdownGenerationLogs();
                    if (!this.breakdownGeneration.startedAt && Number(job.started_at || 0) > 0) {
                        this.breakdownGeneration.startedAt = Number(job.started_at) * 1000;
                    }
                    this.breakdownGeneration.elapsedSeconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Number(job.progress?.elapsed_seconds || 0)
                    );
                    if (job.progress?.coverage?.length) this.breakdownGeneration.coverage = job.progress.coverage;
                    if (!['succeeded', 'failed', 'cancelled'].includes(job.status) || this.breakdownGeneration.notifiedStatus === job.status) return;
                    this.breakdownGeneration.notifiedStatus = job.status;
                    if (job.status !== 'succeeded') {
                        this.breakdownGeneration.error = `拆解生成${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || '请查看任务日志'}`;
                        return;
                    }
                    let completedJob = job;
                    if (!(completedJob.events || []).length) {
                        const detailResponse = await aiworkerApi.request(`/api/jobs/${job.job_id}`);
                        if (detailResponse.ok) completedJob = await detailResponse.json();
                    }
                    const finished = [...(completedJob.events || [])].reverse().find(event => event.event === 'job_finished' && event.payload?.breakdown_id);
                    const breakdownId = finished?.payload?.breakdown_id;
                    if (!breakdownId) {
                        this.breakdownGeneration.error = '生成任务已结束，但未返回拆解版本，请查看任务日志。';
                        return;
                    }
                    try {
                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${breakdownId}`);
                        const record = await res.json();
                        if (!res.ok) throw new Error(record.detail || '读取生成结果失败');
                        if (this.breakdownGeneration.source === 'topic-library') {
                            this.topicLibrary.breakdown = record;
                            await this.fetchTopicLibrary();
                        } else {
                            this.creative.breakdown = record;
                            await this.fetchCreativeTopics();
                        }
                        const source = this.breakdownGeneration.source;
                        const title = this.breakdownGeneration.topic?.title || record.content?.title;
                        this.breakdownGenerationProgressModal.hide();
                        setTimeout(() => this.openBreakdownEditor(source, title, record), 200);
                    } catch (e) {
                        this.breakdownGeneration.error = e.message || '读取生成结果失败';
                    }
                },

                breakdownJobTopicId(job) {
                    return Number(job?.job?.extra?.topic_id || job?.extra?.topic_id || 0);
                },

                breakdownJobRunningForTopic(topicId) {
                    return this.jobs.some(job =>
                        (job.crawler_type || job.job?.crawler_type) === 'breakdown_generate'
                        && this.breakdownJobTopicId(job) === Number(topicId)
                        && ['pending', 'running', 'stopping'].includes(job.status)
                    );
                },

                async syncBackgroundBreakdownJobs() {
                    let refreshTopics = false;
                    let refreshCandidates = false;
                    for (const job of this.jobs) {
                        if ((job.crawler_type || job.job?.crawler_type) !== 'breakdown_generate') continue;
                        const previousStatus = this.breakdownJobStatuses[job.job_id];
                        this.breakdownJobStatuses[job.job_id] = job.status;
                        if (
                            job.job_id === this.breakdownGeneration.jobId
                            || !['pending', 'running', 'stopping'].includes(previousStatus)
                            || !['succeeded', 'failed', 'cancelled'].includes(job.status)
                        ) continue;
                        const topicId = this.breakdownJobTopicId(job);
                        const topic = this.topicLibrary.items.find(item => Number(item.id) === topicId)
                            || this.creative.items.find(item => Number(item.id) === topicId);
                        const title = topic?.title || `选题 ${topicId}`;
                        if (job.status === 'succeeded') {
                            this.showCreativeNotice?.(`${title}拆解生成完成`, 'success');
                        } else {
                            this.showCreativeNotice?.(`${title}拆解生成${job.status === 'cancelled' ? '已取消' : '失败'}`, 'danger');
                        }
                        refreshTopics = refreshTopics || ['selection-topics', 'selection-breakdowns'].includes(this.page);
                        refreshCandidates = refreshCandidates || this.page === 'selection-hits';
                    }
                    if (refreshTopics) await this.fetchTopicLibrary();
                    if (refreshCandidates) await this.fetchCreativeTopics();
                },

                breakdownGenerationRunning() {
                    return ['pending', 'running', 'stopping'].includes(this.breakdownGeneration.status);
                },

                breakdownGenerationRetryable() {
                    return ['failed', 'cancelled'].includes(this.breakdownGeneration.status);
                },

                breakdownGenerationIndeterminate() {
                    if (!this.breakdownGenerationRunning()) return false;
                    const progress = this.breakdownGeneration.progress || {};
                    if (progress.indeterminate) return true;
                    return Number(progress.completed || 0) === 0 && Number(progress.total || 0) <= 1;
                },

                breakdownGenerationProgressLabel() {
                    if (!this.breakdownGenerationIndeterminate()) return `${this.breakdownGenerationProgressPercent()}%`;
                    const seconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Number(this.breakdownGeneration.progress?.elapsed_seconds || 0)
                    );
                    return `已等待 ${this.formatBreakdownGenerationElapsed(seconds)}`;
                },

                formatBreakdownGenerationElapsed(seconds) {
                    const safeSeconds = Math.max(0, Math.floor(Number(seconds || 0)));
                    const minutes = Math.floor(safeSeconds / 60);
                    const remainder = safeSeconds % 60;
                    return minutes ? `${minutes}分${remainder}秒` : `${remainder}秒`;
                },

                tickBreakdownGenerationElapsed() {
                    if (!this.breakdownGenerationRunning()) return;
                    if (!this.breakdownGeneration.startedAt) this.breakdownGeneration.startedAt = Date.now();
                    this.breakdownGeneration.elapsedSeconds = Math.max(
                        Number(this.breakdownGeneration.elapsedSeconds || 0),
                        Math.floor((Date.now() - this.breakdownGeneration.startedAt) / 1000)
                    );
                },

                breakdownGenerationProgressPercent() {
                    const progress = this.breakdownGeneration.progress || {};
                    const total = Number(progress.total || 0);
                    if (total) return Math.min(100, Math.round(Number(progress.completed || 0) * 100 / total));
                    return this.breakdownGeneration.status === 'succeeded' ? 100 : 0;
                },

                async openBreakdownTaskLog() {
                    if (!this.breakdownGeneration.jobId) return;
                    this.breakdownGeneration.showLogs = true;
                    await this.syncBreakdownGenerationLogs();
                    this.$nextTick(() => {
                        this.$refs.breakdownGenerationLogPane?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
                    });
                },

                async syncBreakdownGenerationLogs() {
                    const jobId = this.breakdownGeneration.jobId;
                    if (!jobId) return;
                    try {
                        const res = await aiworkerApi.request(`/api/jobs/${jobId}/logs`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '读取任务日志失败');
                        this.breakdownGeneration.logs = data.logs || [];
                    } catch (e) {
                        this.breakdownGeneration.error = e.message || '读取任务日志失败';
                    }
                },

                regenerateCurrentBreakdown() {
                    const record = this.breakdownEditor.record;
                    if (!record?.topic_id || this.breakdownGenerationRunning()) return;
                    if (!confirm('确定根据已下载脚本更新拆解并生成一个新版本吗？')) return;
                    const topic = this.breakdownGeneration.topic?.id === record.topic_id
                        ? this.breakdownGeneration.topic
                        : {id: record.topic_id, title: this.breakdownEditor.title};
                    const source = this.breakdownEditor.source || 'topic-library';
                    this.breakdownEditorModal.hide();
                    setTimeout(() => this.startTranscriptBreakdown(topic, source, true), 350);
                },

                dramaDownloadTitleKey(value) {
                    return String(value || '').normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
                },

                isDramaDownloadJob(job) {
                    return ['drama_download', 'douyin_fallback_analysis'].includes(
                        job?.crawler_type || job?.job?.crawler_type || ''
                    );
                },

                dramaDownloadJobTopicId(job) {
                    return Number(job?.job?.extra?.topic_id || job?.extra?.topic_id || 0);
                },

                dramaDownloadRunningForTopic(topicId) {
                    return this.jobs.some(job =>
                        this.isDramaDownloadJob(job)
                        && this.dramaDownloadJobTopicId(job) === Number(topicId)
                        && ['pending', 'running', 'stopping'].includes(job.status)
                    );
                },

                findDramaDownloadJob(title = '', topicId = null) {
                    const titleKey = this.dramaDownloadTitleKey(title);
                    return this.jobs.find(job => {
                        const jobTitle = job.job?.extra?.title || '';
                        const sameTopic = Number(topicId || 0) > 0
                            && this.dramaDownloadJobTopicId(job) === Number(topicId);
                        return this.isDramaDownloadJob(job)
                            && ['pending', 'running', 'stopping'].includes(job.status)
                            && (sameTopic || (!topicId && (!titleKey || this.dramaDownloadTitleKey(jobTitle) === titleKey)));
                    }) || null;
                },

                activeDramaDownloadJobs() {
                    return this.jobs.filter(job => {
                        if (!this.isDramaDownloadJob(job)) return false;
                        if (!['pending', 'running', 'stopping'].includes(job.status)) return false;
                        if (this.hiddenDramaDownloadJobs[job.job_id]) return false;
                        return job.job_id !== this.dramaDownload.jobId || this.dramaDownload.background;
                    });
                },

                openDramaDownloadJob(job) {
                    this.hiddenDramaDownloadJobs = {...this.hiddenDramaDownloadJobs, [job.job_id]: false};
                    this.restoreDramaDownloadJob(null, job, true);
                },

                hideDramaDownloadJob(job) {
                    this.hiddenDramaDownloadJobs = {...this.hiddenDramaDownloadJobs, [job.job_id]: true};
                },

                restoreDramaDownloadJob(topic, job, showModal = true) {
                    if (!job) return false;
                    const crawlerType = job.crawler_type || job.job?.crawler_type || '';
                    const extra = job.job?.extra || {};
                    const source = crawlerType === 'douyin_fallback_analysis' ? 'douyin_fallback' : 'hongguo';
                    this.dramaDownload = {
                        title: topic?.title || extra.title || '', topic: topic || null, source,
                        fallbackReason: extra.fallback_reason || '', candidates: [], selectedCandidate: null,
                        selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], relatedVideos: [],
                        missingEpisodes: [], seriesRangeLabel: '', seriesWarnings: [], selectedEpisodes: [],
                        allEpisodes: Boolean(extra.all_episodes), searching: false, episodesLoading: false,
                        starting: false, error: '', jobId: job.job_id, jobStatus: job.status || 'running',
                        progress: job.progress || {}, progressMessage: job.message || '下载任务正在运行',
                        paused: Boolean(job.paused), finished: false, background: !showModal, showFiles: false,
                        logs: [], logCount: Number(job.log_count || 0)
                    };
                    if (showModal) this.dramaDownloadModal.show();
                    this.scrollDramaDownloadToTop();
                    return true;
                },

                restoreLatestActiveDramaDownload() {
                    if (this.dramaDownload.jobId) return;
                    const job = this.findDramaDownloadJob();
                    if (job) this.restoreDramaDownloadJob(null, job, false);
                },

                scrollDramaDownloadToTop() {
                    this.$nextTick(() => {
                        const body = document.querySelector('#dramaDownloadModal .modal-body');
                        if (body) body.scrollTop = 0;
                    });
                },

                dramaDownloadStatusLabel() {
                    if (this.dramaDownload.starting) return '正在启动';
                    if (this.dramaDownload.paused) return '已暂停';
                    return {pending: '等待执行', running: '下载中', stopping: '正在停止', succeeded: '已完成', failed: '失败', cancelled: '已取消'}[this.dramaDownload.jobStatus] || '下载中';
                },

                dramaDownloadStatusClass() {
                    if (this.dramaDownload.paused) return 'bg-warning text-dark';
                    return {pending: 'bg-secondary', running: 'bg-primary', stopping: 'bg-warning text-dark', succeeded: 'bg-success', failed: 'bg-danger', cancelled: 'bg-secondary'}[this.dramaDownload.jobStatus] || 'bg-primary';
                },

                dramaDownloadCurrentDescription() {
                    const current = Number(this.dramaDownload.progress?.current || 0);
                    if (current > 0) return this.dramaDownload.source === 'douyin_fallback' ? `当前处理第 ${current} 个视频` : `当前处理第 ${current} 集`;
                    const completed = Number(this.dramaDownload.progress?.completed || 0);
                    const total = Number(this.dramaDownload.progress?.total || 0);
                    return total > 0 ? `已完成 ${completed} / ${total}` : '';
                },

                formatDramaBytes(value) {
                    const bytes = Math.max(0, Number(value || 0));
                    if (bytes < 1024) return `${bytes} B`;
                    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
                    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
                },

                formatDramaDuration(value) {
                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
                    if (!seconds) return '未知';
                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
                },

                async parseDramaDownloadResponse(response) {
                    const body = await response.text();
                    try {
                        return body ? JSON.parse(body) : {};
                    } catch (_) {
                        return {
                            status: 'error',
                            message: body.trim() || `服务器返回错误（${response.status}）`
                        };
                    }
                },

                async openTopicDownload(topic) {
                    await this.fetchJobs();
                    const runningJob = this.findDramaDownloadJob(topic?.title || '', topic?.id);
                    if (runningJob) {
                        this.restoreDramaDownloadJob(topic, runningJob, true);
                        return;
                    }
                    try {
                        const statusResponse = await aiworkerApi.request('/api/hongguo/drama/download/status', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({title: topic?.title || ''})
                        });
                        const downloadStatus = await this.parseDramaDownloadResponse(statusResponse);
                        if (statusResponse.ok && downloadStatus.douyin_full_downloaded) {
                            alert(`抖音全剧已下载（第 ${downloadStatus.episode_start}-${downloadStatus.episode_end} 集），无需重复下载。`);
                            return;
                        }
                    } catch (_) {}
                    this.dramaDownload = {title: topic.title || '', topic, source: 'hongguo', fallbackReason: '', candidates: [], selectedCandidate: null, selectedAwemeIds: [], allDouyinCandidates: false, episodes: [], relatedVideos: [], missingEpisodes: [], seriesRangeLabel: '', seriesWarnings: [], selectedEpisodes: [], allEpisodes: false, searching: true, episodesLoading: false, starting: false, error: '', jobId: '', jobStatus: '', progress: {}, progressMessage: '', paused: false, finished: false, background: false, showFiles: false};
                    this.dramaDownloadModal.show();
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/download/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: this.dramaDownload.title})});
                        const data = await this.parseDramaDownloadResponse(res);
                        this.dramaDownload.source = data.source || 'hongguo';
                        this.dramaDownload.fallbackReason = data.fallback_reason || '';
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '剧目检索失败');
                        this.dramaDownload.candidates = data.candidates || [];
                        if (!this.dramaDownload.candidates.length) this.dramaDownload.error = this.dramaDownload.source === 'douyin_fallback' ? '红果和抖音都没有找到可用视频，请确认剧名后重试' : '没有找到剧名完全匹配的红果剧目，请确认剧名后重试';
                        this.dramaDownload.searching = false;
                        // Hongguo has a book/episode flow, but Douyin fallback always
                        // requires an explicit user choice to avoid downloading an
                        // unrelated promotion or clip that happens to match the title.
                        if (this.dramaDownload.candidates.length === 1 && this.dramaDownload.source !== 'douyin_fallback') await this.selectDramaDownloadCandidate(this.dramaDownload.candidates[0]);
                    } catch (e) {
                        this.dramaDownload.error = e.message || '剧目检索失败';
                    } finally {
                        this.dramaDownload.searching = false;
                    }
                },

                async selectDramaDownloadCandidate(candidate) {
                    this.dramaDownload.selectedCandidate = candidate;
                    this.dramaDownload.episodes = [];
                    this.dramaDownload.relatedVideos = [];
                    this.dramaDownload.missingEpisodes = [];
                    this.dramaDownload.seriesRangeLabel = '';
                    this.dramaDownload.seriesWarnings = [];
                    this.dramaDownload.selectedEpisodes = [];
                    this.dramaDownload.selectedAwemeIds = [];
                    this.dramaDownload.allEpisodes = false;
                    this.dramaDownload.allDouyinCandidates = false;
                    if (this.dramaDownload.source === 'douyin_fallback') {
                        this.dramaDownload.episodesLoading = true;
                        this.dramaDownload.error = '';
                        try {
                            const res = await aiworkerApi.request('/api/hongguo/drama/douyin-fallback/series', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({title: this.dramaDownload.title || candidate.title, aweme_id: candidate.aweme_id})
                            });
                            const data = await this.parseDramaDownloadResponse(res);
                            if (!res.ok || data.status === 'error') throw new Error(data.message || '解析抖音剧集失败');
                            this.dramaDownload.episodes = data.episodes || [];
                            this.dramaDownload.relatedVideos = data.related_videos || [];
                            this.dramaDownload.missingEpisodes = data.missing_episodes || [];
                            this.dramaDownload.seriesRangeLabel = data.range_label || '';
                            this.dramaDownload.seriesWarnings = data.warnings || [];
                            const first = this.douyinDownloadItems().find(item => !item.is_paid);
                            this.dramaDownload.selectedAwemeIds = first ? [first.aweme_id] : [];
                            this.syncDouyinDownloadSelection();
                        } catch (e) {
                            this.dramaDownload.error = e.message || '解析抖音剧集失败';
                        } finally {
                            this.dramaDownload.episodesLoading = false;
                        }
                        return;
                    }
                    this.dramaDownload.episodesLoading = true;
                    this.dramaDownload.error = '';
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({book_id: candidate.book_id, source_platform: candidate.source_platform || 'hg', title: this.dramaDownload.title || candidate.title || ''})});
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
                        this.dramaDownload.episodes = data.episodes || [];
                    } catch (e) {
                        this.dramaDownload.error = e.message || '读取剧集失败';
                    } finally {
                        this.dramaDownload.episodesLoading = false;
                    }
                },

                toggleAllDramaEpisodes() {
                    if (this.dramaDownload.allEpisodes) this.dramaDownload.selectedEpisodes = this.downloadableDramaEpisodes().map(item => item.episode);
                    else this.dramaDownload.selectedEpisodes = [];
                },

                isDramaEpisodeDownloaded(episode) {
                    return episode.download_status?.status === 'downloaded';
                },

                downloadableDramaEpisodes() {
                    return this.dramaDownload.episodes.filter(episode => !this.isDramaEpisodeDownloaded(episode));
                },

                dramaSelectedEpisodesForDownload() {
                    if (this.dramaDownload.allEpisodes) return this.downloadableDramaEpisodes().map(item => item.episode);
                    return this.dramaDownload.selectedEpisodes.filter(episode => !this.dramaDownload.episodes.find(item => Number(item.episode) === Number(episode) && this.isDramaEpisodeDownloaded(item)));
                },

                toggleAllDouyinDownloadCandidates() {
                    const downloadableIds = this.dramaDownload.episodes.filter(item => !item.is_paid).map(item => item.aweme_id);
                    this.dramaDownload.selectedAwemeIds = this.dramaDownload.allDouyinCandidates ? downloadableIds : [];
                },

                syncDouyinDownloadSelection() {
                    const downloadableIds = this.dramaDownload.episodes.filter(item => !item.is_paid).map(item => item.aweme_id);
                    this.dramaDownload.allDouyinCandidates = downloadableIds.length > 0 && downloadableIds.every(id => this.dramaDownload.selectedAwemeIds.includes(id));
                },

                douyinDownloadItems() {
                    return [...(this.dramaDownload.episodes || []), ...(this.dramaDownload.relatedVideos || [])];
                },

                selectedDouyinEpisodeSummary() {
                    const labels = this.douyinDownloadItems()
                        .filter(item => this.dramaDownload.selectedAwemeIds.includes(item.aweme_id) && item.episode_label)
                        .map(item => item.episode_label);
                    const unique = [...new Set(labels)];
                    return unique.length ? `集数：${unique.join('、')}` : '';
                },

                dramaSeriesSourceLabel(source) {
                    return {collection: '合集', author: '作者作品', search: '搜索结果'}[source] || '相关视频';
                },

                async startDramaDownload() {
                    const isDouyinFallback = this.dramaDownload.source === 'douyin_fallback';
                    const episodeNumbers = this.dramaSelectedEpisodesForDownload();
                    if (isDouyinFallback ? !this.dramaDownload.selectedAwemeIds.length : !this.dramaDownload.selectedCandidate || !episodeNumbers.length) return;
                    this.dramaDownload.starting = true;
                    this.dramaDownload.error = '';
                    this.dramaDownload.progressMessage = '正在创建下载任务…';
                    this.dramaDownload.progress = {completed: 0, total: isDouyinFallback ? this.dramaDownload.selectedAwemeIds.length : episodeNumbers.length, percent: 0, downloaded: 0, skipped_paid: 0, failed: 0};
                    try {
                        const endpoint = isDouyinFallback ? '/api/hongguo/drama/douyin-fallback/jobs' : '/api/hongguo/drama/download/jobs';
                        const payload = isDouyinFallback
                            ? {topic_id: this.dramaDownload.topic.id, title: this.dramaDownload.title, aweme_ids: this.dramaDownload.selectedAwemeIds, fallback_reason: this.dramaDownload.fallbackReason || ''}
                            : {topic_id: this.dramaDownload.topic.id, title: this.dramaDownload.title, book_id: this.dramaDownload.selectedCandidate.book_id, source_platform: this.dramaDownload.selectedCandidate.source_platform || 'hg', episodes: episodeNumbers, all_episodes: this.dramaDownload.allEpisodes && episodeNumbers.length === this.dramaDownload.episodes.length};
                        const res = await aiworkerApi.request(endpoint, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
                        this.dramaDownload.jobId = data.job.job_id;
                        this.dramaDownload.jobStatus = data.job.status || 'pending';
                        this.dramaDownloadJobStatuses[data.job.job_id] = this.dramaDownload.jobStatus;
                        this.hiddenDramaDownloadJobs = {...this.hiddenDramaDownloadJobs, [data.job.job_id]: false};
                        this.dramaDownload.progress = {...this.dramaDownload.progress, ...(data.job.progress || {})};
                        this.dramaDownload.progressMessage = isDouyinFallback ? '抖音降级下载任务已开始' : '下载任务已开始';
                        this.scrollDramaDownloadToTop();
                        await this.fetchTopicLibrary();
                        await this.fetchCreativeTopics();
                        await this.fetchJobs();
                    } catch (e) {
                        this.dramaDownload.error = e.message || '创建下载任务失败';
                    } finally {
                        this.dramaDownload.starting = false;
                    }
                },

                syncDramaDownloadProgress() {
                    if (!this.dramaDownload.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
                    if (!job) return;
                    this.dramaDownload.jobStatus = job.status || this.dramaDownload.jobStatus;
                    this.dramaDownload.paused = Boolean(job.paused);
                    this.dramaDownload.progress = {...(this.dramaDownload.progress || {}), ...(job.progress || {})};
                    this.dramaDownload.progressMessage = job.message || this.dramaDownload.progressMessage;
                    if (job.status === 'succeeded') {
                        this.dramaDownload.background = false;
                        if (!this.dramaDownload.finished) {
                            alert(`${job.message || (this.dramaDownload.source === 'douyin_fallback' ? '抖音降级视频解析完成' : '剧集下载完成')}，文件已保存到数据管理。`);
                            this.fetchTopicLibrary();
                            this.fetchCreativeTopics();
                        }
                        this.dramaDownload.finished = true;
                    } else if (['failed', 'cancelled'].includes(job.status)) {
                        if (!this.dramaDownload.finished) alert(`剧集下载${job.status === 'cancelled' ? '已取消' : '失败'}：${job.message || ''}`);
                        this.dramaDownload.finished = true;
                    }
                },

                async syncBackgroundDramaDownloadJobs() {
                    let refreshTopics = false;
                    for (const job of this.jobs) {
                        if (!this.isDramaDownloadJob(job)) continue;
                        const previousStatus = this.dramaDownloadJobStatuses[job.job_id];
                        this.dramaDownloadJobStatuses[job.job_id] = job.status;
                        if (
                            job.job_id === this.dramaDownload.jobId
                            || !['pending', 'running', 'stopping'].includes(previousStatus)
                            || !['succeeded', 'failed', 'cancelled'].includes(job.status)
                        ) continue;
                        const title = job.job?.extra?.title || '剧目';
                        if (job.status === 'succeeded') {
                            this.showCreativeNotice?.(`《${title}》下载与解析完成`, 'success');
                        } else {
                            this.showCreativeNotice?.(
                                `《${title}》下载${job.status === 'cancelled' ? '已取消' : '失败'}`,
                                'danger'
                            );
                        }
                        refreshTopics = true;
                    }
                    if (refreshTopics) {
                        await this.fetchTopicLibrary();
                        await this.fetchCreativeTopics();
                    }
                },

                async pauseDramaDownloadJob(job) {
                    const res = await aiworkerApi.request(`/api/jobs/${job.job_id}/pause`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') {
                        this.showCreativeNotice?.(data.message || '暂停失败', 'danger');
                        return;
                    }
                    await this.fetchJobs();
                },

                async resumeDramaDownloadJob(job) {
                    const res = await aiworkerApi.request(`/api/jobs/${job.job_id}/resume`, {method: 'POST'});
                    const data = await res.json();
                    if (!res.ok || data.status === 'error') {
                        this.showCreativeNotice?.(data.message || '继续失败', 'danger');
                        return;
                    }
                    await this.fetchJobs();
                },

                async cancelDramaDownloadJob(job) {
                    const title = job.job?.extra?.title || '当前剧目';
                    if (!confirm(`确定取消《${title}》的下载任务吗？已完成的集数会保留。`)) return;
                    await aiworkerApi.request(`/api/jobs/${job.job_id}/stop`, {method: 'POST'});
                    await this.fetchJobs();
                },

                async pauseDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
                    if (!job) return;
                    await this.pauseDramaDownloadJob(job);
                    this.syncDramaDownloadProgress();
                },

                async resumeDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
                    if (!job) return;
                    await this.resumeDramaDownloadJob(job);
                    this.syncDramaDownloadProgress();
                },

                async cancelDramaDownload() {
                    if (!this.dramaDownload.jobId) return;
                    const job = this.jobs.find(item => item.job_id === this.dramaDownload.jobId);
                    if (!job) return;
                    await this.cancelDramaDownloadJob(job);
                },

                dramaDownloadArtifacts() {
                    if (!this.dramaDownload.jobId) return [];
                    return this.jobs.find(item => item.job_id === this.dramaDownload.jobId)?.artifacts || [];
                },

                dramaDownloadArtifactName(item) {
                    return String(item?.path || '').replace(/\\/g, '/').split('/').pop() || '未命名文件';
                },

                async openDramaDownloadFiles() {
                    await this.fetchJobs();
                    this.dramaDownload.showFiles = true;
                    this.$nextTick(() => {
                        document.getElementById('dramaDownloadArtifacts')?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
                    });
                },

                openBreakdownEditor(source, title, record) {
                    const fieldGuide = record?.field_guide || [];
                    this.breakdownEditor = {
                        source,
                        title: title || record?.content?.title || '未命名选题',
                        record,
                        content: this.normalizeBreakdownEditorContent(record?.content, fieldGuide),
                        fieldGuide,
                        saving: false,
                        error: ''
                    };
                    this.breakdownEditorModal.show();
                },

                syncBreakdownEditorRecord(record) {
                    const fieldGuide = record?.field_guide || this.breakdownEditor.fieldGuide || [];
                    this.breakdownEditor.record = record;
                    this.breakdownEditor.content = this.normalizeBreakdownEditorContent(record?.content, fieldGuide);
                    this.breakdownEditor.fieldGuide = fieldGuide;
                    if (this.breakdownEditor.source === 'topic-library') {
                        this.topicLibrary.breakdown = record;
                    } else {
                        this.creative.breakdown = record;
                    }
                },

                normalizeBreakdownEditorContent(content, fieldGuide) {
                    const source = content && typeof content === 'object' ? content : {};
                    return (fieldGuide || []).reduce((result, item) => {
                        const value = source[item.key];
                        result[item.key] = item.type === '数组'
                            ? (Array.isArray(value) ? value.map(entry => String(entry ?? '')).filter(Boolean) : [])
                            : String(value ?? '');
                        return result;
                    }, {});
                },

                addBreakdownArrayItem(key) {
                    if (!Array.isArray(this.breakdownEditor.content[key])) this.breakdownEditor.content[key] = [];
                    this.breakdownEditor.content[key].push('');
                },

                removeBreakdownArrayItem(key, index) {
                    this.breakdownEditor.content[key]?.splice(index, 1);
                },

                async saveBreakdownEditor() {
                    const record = this.breakdownEditor.record;
                    if (!record || record.status !== 'draft') return false;
                    const content = this.normalizeBreakdownEditorContent(this.breakdownEditor.content, this.breakdownEditor.fieldGuide);
                    this.breakdownEditor.saving = true;
                    this.breakdownEditor.error = '';
                    try {
                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${record.id}`, {
                            method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '保存拆解失败');
                        this.syncBreakdownEditorRecord(data);
                        return true;
                    } catch (e) {
                        this.breakdownEditor.error = e.message || '保存拆解失败';
                        return false;
                    } finally {
                        this.breakdownEditor.saving = false;
                    }
                },

                breakdownRecordStatusLabel(status) {
                    return {
                        draft: '草稿', superseded: '已替代', locked: '已完成'
                    }[status] || status || '草稿';
                },

                downloadBreakdown() {
                    const breakdownId = this.breakdownEditor.record?.id;
                    if (!breakdownId) return;
                    window.open(
                        `/api/creative/breakdowns/${encodeURIComponent(breakdownId)}/download`,
                        '_blank',
                        'noopener'
                    );
                },

                async submitBreakdownEditor() {
                    const record = this.breakdownEditor.record;
                    if (!record || record.status !== 'draft') return;
                    if (!await this.saveBreakdownEditor()) return;
                    if (!confirm('完成后该拆解版本将锁定且不可修改，确定继续吗？')) return;
                    this.breakdownEditor.saving = true;
                    try {
                        const res = await aiworkerApi.request(`/api/creative/breakdowns/${record.id}/lock`, {method: 'POST'});
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '完成拆解失败');
                        Object.assign(record, data);
                        record.status = data.status || 'locked';
                        if (this.breakdownEditor.source === 'topic-library') await this.fetchTopicLibrary();
                        if (this.breakdownEditor.source === 'creative') await this.fetchCreativeTopics();
                        alert('拆解已完成并锁定，可以创建策划项目。');
                    } catch (e) {
                        this.breakdownEditor.error = e.message || '完成拆解失败';
                    } finally {
                        this.breakdownEditor.saving = false;
                    }
                },

                async saveTopicLibraryBreakdown() {
                    return this.saveBreakdownEditor();
                },

                async lockTopicLibraryBreakdown() {
                    return this.submitBreakdownEditor();
                },

                async generateCreativeTopics() {
                    this.creative.loading = true;
                    this.creative.error = '';
                    try {
                        const res = await aiworkerApi.request('/api/creative/topics/generate', {
                            method: 'POST', headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({platform: this.creative.platform, limit: 10})
                        });
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '生成选题失败');
                        this.creative.items = data.items || [];
                    } catch (e) {
                        this.creative.error = e.message || '生成选题失败';
                    } finally {
                        this.creative.loading = false;
                    }
                },

                async updateCreativeTopic(topic, status) {
                    const res = await aiworkerApi.request(`/api/creative/topics/${topic.id}`, {
                        method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({status})
                    });
                    const data = await res.json();
                    if (!res.ok) { alert(data.detail || '更新失败'); return; }
                    Object.assign(topic, data);
                    await this.fetchCreativeTopics();
                },

                createCreativeBreakdown(topic) {
                    return this.startTranscriptBreakdown(
                        topic,
                        'creative'
                    );
                },

                async submitCreativeBreakdown() {
                    return this.submitBreakdownEditor();
                },

}));

document.querySelector('[data-workspace-page="selection-breakdowns"]')?.setAttribute('data-ready', 'true');
