window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                dramaReviewStatusLabel() {
                    return {queued: '等待采集', running: '采集中', completed: '已完成', failed: '失败'}[this.dramaReviews.status] || '未开始';
                },

                dramaReviewPlatformForTopic(topic) {
                    const platforms = topic?.source_platforms || [topic?.platform];
                    return platforms.includes('hg') ? 'hg' : 'dy';
                },

                isDouyinDramaReview(state) {
                    return state?.platform === 'dy';
                },

                dramaReviewTopicKey(topic) {
                    return String(topic?.id || topic?.title || '');
                },

                dramaReviewJobForTopic(topic) {
                    return this.dramaReviewJobs[this.dramaReviewTopicKey(topic)] || null;
                },

                dramaReviewRunningForTopic(topic) {
                    return ['queued', 'running'].includes(this.dramaReviewJobForTopic(topic)?.status);
                },

                dramaReviewActionTitle(topic) {
                    const job = this.dramaReviewJobForTopic(topic);
                    if (['queued', 'running'].includes(job?.status)) return '查看剧评下载进度';
                    if (job?.status === 'completed') return '下载剧评（已完成）';
                    return '下载剧评';
                },

                persistDramaReviewJob(state) {
                    const key = this.dramaReviewTopicKey(state?.topic);
                    if (!key || !state?.jobId) return;
                    this.dramaReviewJobs[key] = state;
                },

                syncDramaReviewModal(state) {
                    if (this.dramaReviewTopicKey(this.dramaReviews.topic) === this.dramaReviewTopicKey(state?.topic)) {
                        Object.assign(this.dramaReviews, state);
                    }
                },

                finishDisplayedDramaReviewJob() {
                    const state = this.dramaReviews;
                    if (!state?.jobId || !['completed', 'failed'].includes(state.status)) return;
                    state.polling = false;
                    this.persistDramaReviewJob(state);
                },

                async parseDramaReviewResponse(response) {
                    const body = await response.text();
                    try {
                        return body ? JSON.parse(body) : {};
                    } catch (_) {
                        return {detail: body.trim() || `服务器返回错误（${response.status}）`};
                    }
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

                applyDramaReviewCapture(data, state = this.dramaReviews) {
                    const job = data?.job || {};
                    state.jobId = job.job_id || state.jobId;
                    state.status = job.status || state.status;
                    state.reviewCount = Number(job.review_count || 0);
                    state.sourceTotal = Number(job.source_total || 0);
                    state.items = data?.reviews || [];
                    state.error = job.status === 'failed' ? String(job.error || '剧评采集失败') : '';
                    this.persistDramaReviewJob(state);
                    this.syncDramaReviewModal(state);
                },

                async pollDramaReviewCapture(state = this.dramaReviews) {
                    const jobId = state.jobId;
                    if (!jobId || state.polling) return;
                    state.polling = true;
                    try {
                        const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                        const response = await aiworkerApi.request(`${root}/${encodeURIComponent(jobId)}`);
                        const data = await this.parseDramaReviewResponse(response);
                        if (!response.ok) throw new Error(data.detail || data.message || '读取剧评检索进度失败');
                        if (state.jobId !== jobId) return;
                        this.applyDramaReviewCapture(data, state);
                        if (['queued', 'running'].includes(state.status)) {
                            window.setTimeout(() => this.pollDramaReviewCapture(state), 2000);
                        }
                    } catch (error) {
                        if (state.jobId === jobId) {
                            state.error = error.message || '读取剧评检索进度失败';
                            this.persistDramaReviewJob(state);
                        }
                    } finally {
                        state.polling = false;
                        this.persistDramaReviewJob(state);
                        this.syncDramaReviewModal(state);
                    }
                },

                downloadDramaReviews() {
                    if (!this.dramaReviews.jobId || this.dramaReviews.status !== 'completed') return;
                    const state = this.dramaReviews;
                    const root = this.isDouyinDramaReview(state) ? '/api/douyin/reviews/jobs' : '/api/hongguo/reviews/jobs';
                    window.open(`${root}/${encodeURIComponent(state.jobId)}/download?format=${encodeURIComponent(state.format || 'csv')}`, '_blank', 'noopener');
                },

                async openTopicReviewCapture(topic) {
                    const state = this.dramaReviews;
                    const existing = this.dramaReviewJobForTopic(topic);
                    Object.assign(state, {
                        topic,
                        title: String(topic?.title || '').trim(),
                        platform: this.dramaReviewPlatformForTopic(topic),
                        limit: 100,
                        allReviews: false,
                        format: 'csv',
                        jobId: '',
                        status: '',
                        reviewCount: 0,
                        sourceTotal: 0,
                        items: [],
                        loading: false,
                        polling: false,
                        error: ''
                    }, existing || {});
                    state.topic = topic;
                    state.title = String(topic?.title || '').trim();
                    state.platform = existing?.platform || this.dramaReviewPlatformForTopic(topic);
                    state.loading = false;
                    state.polling = false;
                    this.dramaReviewCaptureModal?.show();
                    if (['queued', 'running'].includes(existing?.status)) this.pollDramaReviewCapture(existing);
                },
}));
