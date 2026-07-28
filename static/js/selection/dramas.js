window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                dramaReviewStatusLabel() {
                    return {queued: '等待采集', running: '采集中', completed: '已完成', failed: '失败'}[this.dramaReviews.status] || '未开始';
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

                downloadDramaReviews() {
                    if (!this.dramaReviews.jobId || this.dramaReviews.status !== 'completed') return;
                    const state = this.dramaReviews;
                    const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                    window.open(`${root}/${encodeURIComponent(state.jobId)}/download?format=${encodeURIComponent(state.format || 'csv')}`, '_blank', 'noopener');
                },

                async searchDrama() {
                    const title = this.dramaAnalysis.title.trim();
                    if (!title) return;
                    clearTimeout(this.dramaAnalysis.suggestionTimer);
                    this.dramaAnalysis.suggestionRequestId += 1;
                    this.dramaAnalysis.suggestions = [];
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.candidates = [];
                    this.dramaAnalysis.selected = null;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    this.dramaAnalysis.searchSource = '';
                    this.dramaAnalysis.fallbackReason = '';
                    this.dramaReviews.title = title;
                    this.dramaReviews.platform = this.dramaAnalysis.platform;
                    try {
                        const endpoint = this.dramaAnalysis.platform === 'dy' ? '/api/douyin/drama/search' : '/api/hongguo/drama/search';
                        const res = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({title})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '搜索失败');
                        this.dramaAnalysis.candidates = data.candidates || [];
                        this.dramaAnalysis.searchSource = data.source || 'hongguo';
                        this.dramaAnalysis.fallbackReason = data.fallback_reason || '';
                        if (!this.dramaAnalysis.candidates.length) this.dramaAnalysis.error = '未找到匹配剧目';
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '搜索剧目失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                isDouyinDramaSearch() {
                    return ['douyin', 'douyin_fallback'].includes(this.dramaAnalysis.searchSource);
                },

                changeDramaSearchPlatform() {
                    this.dramaAnalysis.suggestions = [];
                    this.dramaAnalysis.candidates = [];
                    this.dramaAnalysis.selected = null;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    this.dramaAnalysis.searchSource = '';
                    this.dramaAnalysis.fallbackReason = '';
                    this.dramaAnalysis.error = '';
                    this.dramaReviews.platform = this.dramaAnalysis.platform;
                },

                onDramaTitleInput() {
                    clearTimeout(this.dramaAnalysis.suggestionTimer);
                    const requestId = ++this.dramaAnalysis.suggestionRequestId;
                    this.dramaAnalysis.suggestions = [];
                    const title = this.dramaAnalysis.title.trim();
                    if (this.dramaAnalysis.platform === 'dy') return;
                    if (title.length < 2) return;
                    this.dramaAnalysis.suggestionTimer = setTimeout(async () => {
                        this.dramaAnalysis.suggestionLoading = true;
                        try {
                            const res = await aiworkerApi.request('/api/hongguo/drama/suggest', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({title})
                            });
                            const data = await res.json();
                            if (requestId !== this.dramaAnalysis.suggestionRequestId) return;
                            this.dramaAnalysis.suggestions = res.ok && data.status !== 'error' ? (data.candidates || []) : [];
                        } catch (e) {
                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestions = [];
                        } finally {
                            if (requestId === this.dramaAnalysis.suggestionRequestId) this.dramaAnalysis.suggestionLoading = false;
                        }
                    }, 350);
                },

                chooseDramaSuggestion(suggestion) {
                    this.dramaAnalysis.title = suggestion.title || this.dramaAnalysis.title;
                    this.dramaAnalysis.suggestions = [];
                    this.searchDrama();
                },

                async selectDramaCandidate(candidate) {
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.selected = candidate;
                    this.dramaAnalysis.episodes = [];
                    this.dramaAnalysis.episode = 0;
                    if (this.isDouyinDramaSearch()) return;
                    this.dramaAnalysis.loading = true;
                    try {
                        const res = await aiworkerApi.request('/api/hongguo/drama/episodes', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({book_id: candidate.book_id, source_platform: candidate.source_platform || 'hg'})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '读取剧集失败');
                        this.dramaAnalysis.episodes = data.episodes || [];
                        this.dramaAnalysis.episode = this.dramaAnalysis.episodes[0]?.episode || 0;
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '读取剧集失败';
                        this.dramaAnalysis.selected = null;
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                async startDramaAnalysis() {
                    const selected = this.dramaAnalysis.selected;
                    const isDouyinSearch = this.isDouyinDramaSearch();
                    if (!selected || (isDouyinSearch ? !selected.aweme_id : !this.dramaAnalysis.episode)) return;
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    try {
                        const task = await this.resolveSourcePackageWorkflowTask(
                            this.dramaAnalysis.title || selected.title
                        );
                        const endpoint = isDouyinSearch
                            ? '/api/hongguo/drama/douyin-fallback/jobs'
                            : '/api/hongguo/drama/jobs';
                        const jobPayload = isDouyinSearch
                            ? {
                                title: this.dramaAnalysis.title || selected.title,
                                aweme_id: selected.aweme_id,
                                fallback_reason: this.dramaAnalysis.fallbackReason,
                                topic_id: task.topic_id,
                                workflow_task_id: task.id
                            }
                            : {
                                title: selected.title || this.dramaAnalysis.title,
                                book_id: selected.book_id,
                                episode: this.dramaAnalysis.episode,
                                source_platform: selected.source_platform || 'hg',
                                topic_id: task.topic_id,
                                workflow_task_id: task.id
                            };
                        const res = await aiworkerApi.request(endpoint, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(jobPayload)
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') throw new Error(data.message || '创建下载任务失败');
                        this.dramaAnalysis.jobId = data.job.job_id;
                        this.dramaAnalysis.notifiedStatus = '';
                        this.selectedJobId = data.job.job_id;
                        await this.fetchJobs();
                        await this.fetchLogs();
                        alert(isDouyinSearch
                            ? '抖音下载任务已创建，正在下载已确认的视频并生成转写。完成后会弹窗提醒。'
                            : '剧集下载任务已创建，正在下载并生成转写。完成后会弹窗提醒。');
                    } catch (e) {
                        this.dramaAnalysis.error = e.message || '创建下载任务失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                dramaJobArtifacts() {
                    return this.jobs.find(job => job.job_id === this.dramaAnalysis.jobId)?.artifacts || [];
                },

                formatDramaDuration(value) {
                    const seconds = Math.max(0, Math.round(Number(value) / 1000));
                    if (!seconds) return '未知';
                    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
                },

                notifyDramaJobCompletion() {
                    const job = this.jobs.find(item => item.job_id === this.dramaAnalysis.jobId);
                    if (!job || !['succeeded', 'failed', 'cancelled'].includes(job.status)) return;
                    const marker = `${job.job_id}:${job.status}`;
                    if (this.dramaAnalysis.notifiedStatus === marker) return;
                    this.dramaAnalysis.notifiedStatus = marker;
                    if (job.status === 'succeeded') {
                        const artifactCount = (job.artifacts || []).length;
                        const source = job.job?.crawler_type === 'douyin_fallback_analysis' ? '抖音降级视频' : '剧目';
                        alert(`${source}下载与解析完成，已生成 ${artifactCount} 个本地文件。`);
                        return;
                    }
                    alert(`剧目下载未完成：${job.message || job.status}`);
                },

}));

document.querySelector('[data-workspace-page="selection-dramas"]')?.setAttribute('data-ready', 'true');
