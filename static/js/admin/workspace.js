const RECOMMENDATION_SERIES_COLORS = ['#22c55e', '#3b95f6', '#f59e0b', '#f97316', '#e879f9'];

window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                async fetchFiles({reset = false, cursor = null} = {}) {
                    const page = this.filePage;
                    const requestedQuery = this.fileSearch.trim();
                    const requestSequence = ++page.requestSequence;
                    if (reset) {
                        page.cursorHistory = [];
                        page.currentCursor = null;
                        this.clearFileSelection();
                    }
                    page.error = '';
                    page.loading = true;
                    try {
                        const params = new URLSearchParams({
                            filter: this.fileFilter,
                            query: requestedQuery,
                            limit: '100',
                        });
                        if (cursor) params.set('cursor', cursor);
                        const res = await aiworkerApi.request(`/api/files/page?${params}`);
                        if (!res.ok) throw new Error('文件加载失败');
                        const data = await res.json();
                        if (requestSequence !== page.requestSequence) return;
                        this.files = data.items || [];
                        this.filteredFiles = this.files;
                        page.total = Number(data.total || 0);
                        page.currentCursor = cursor;
                        page.nextCursor = data.next_cursor || null;
                        page.appliedQuery = requestedQuery;
                    } catch (e) {
                        if (requestSequence !== page.requestSequence) return;
                        this.files = [];
                        this.filteredFiles = [];
                        page.total = 0;
                        page.currentCursor = null;
                        page.nextCursor = null;
                        page.error = '文件加载失败，请重试';
                    } finally {
                        if (requestSequence === page.requestSequence) page.loading = false;
                    }
                },

                scrollToCrawlerSettings() {
                    document.getElementById('crawlerSettings')?.scrollIntoView({behavior: 'smooth', block: 'start'});
                },

                async refreshAnalyticsDashboard({notify = false} = {}) {
                    this.analyticsError = '';
                    const refreshed = this.analyticsReportMode === 'monthly'
                        ? await this.fetchMonthlyReports({notify})
                        : await this.fetchWeeklyReports({notify});
                    if (!refreshed) return;
                    this.$nextTick(() => {
                        this.renderAnalyticsCharts();
                        setTimeout(() => this.resizeAnalyticsCharts(), 80);
                    });
                },

                async setAnalyticsReportMode(mode) {
                    const normalized = mode === 'monthly' ? 'monthly' : 'weekly';
                    if (this.analyticsReportMode === normalized) return;
                    this.analyticsReportMode = normalized;
                    try {
                        window.sessionStorage.setItem('aiworker-analytics-report-mode', normalized);
                    } catch (_) {}
                    await this.refreshAnalyticsDashboard();
                },

                activeAnalyticsReport() {
                    return this.analyticsReportMode === 'monthly' ? this.monthlyReport : this.weeklyReport;
                },

                activeAnalyticsReports() {
                    return this.analyticsReportMode === 'monthly' ? this.monthlyReports : this.weeklyReports;
                },

                analyticsReportLoading() {
                    return this.analyticsReportMode === 'monthly' ? this.monthlyReportLoading : this.weeklyReportLoading;
                },

                analyticsReportError() {
                    return this.analyticsReportMode === 'monthly' ? this.monthlyReportError : this.weeklyReportError;
                },

                async fetchWeeklyReports({notify = false} = {}) {
                    this.weeklyReportLoading = true;
                    this.weeklyReportError = '';
                    this.analyticsError = '';
                    try {
                        const params = new URLSearchParams();
                        if (this.analyticsPlatform) params.set('platform', this.analyticsPlatform);
                        const res = await aiworkerApi.request(`/api/analytics/weekly-reports?${params.toString()}`);
                        let data = {};
                        try {
                            data = await res.json();
                        } catch (_) {}
                        if (!res.ok) throw new Error(data.detail || data.message || `加载周度报告失败（HTTP ${res.status || '-'}）`);
                        this.weeklyReports = data.items || [];
                        const selected = this.weeklyReport?.report_id;
                        const report = this.weeklyReports.find(item => item.report_id === selected) || this.weeklyReports[0] || null;
                        this.weeklyReport = report;
                        if (notify) {
                            const generatedCount = this.weeklyReports.filter(item => item.cache_status === 'generated').length;
                            const period = report?.period?.start && report?.period?.end
                                ? `${report.period.start} 至 ${report.period.end}`
                                : '';
                            const message = generatedCount
                                ? `已生成 ${generatedCount} 份周报${period ? `，最新周期：${period}` : ''}`
                                : this.weeklyReports.length
                                    ? `当前周报已是最新${period ? `，最新周期：${period}` : ''}`
                                    : '暂无完整周度数据，请确认上周工作日快照是否齐全。';
                            this.showWeeklyReportRefreshToast('success', '周报已更新', message);
                        }
                        return true;
                    } catch (e) {
                        this.weeklyReports = [];
                        this.weeklyReport = null;
                        const message = e.message || '加载周度报告失败';
                        this.weeklyReportError = message;
                        this.analyticsError = message;
                        if (notify) this.showWeeklyReportRefreshToast('danger', '周报更新失败', message);
                        return false;
                    } finally {
                        this.weeklyReportLoading = false;
                    }
                },

                async fetchMonthlyReports({notify = false} = {}) {
                    this.monthlyReportLoading = true;
                    this.monthlyReportError = '';
                    this.analyticsError = '';
                    try {
                        const params = new URLSearchParams();
                        if (this.analyticsPlatform) params.set('platform', this.analyticsPlatform);
                        const res = await aiworkerApi.request(`/api/analytics/monthly-reports?${params.toString()}`);
                        let data = {};
                        try {
                            data = await res.json();
                        } catch (_) {}
                        if (!res.ok) throw new Error(data.detail || data.message || `加载月度报告失败（HTTP ${res.status || '-'}）`);
                        this.monthlyReports = data.items || [];
                        const selected = this.monthlyReport?.report_id;
                        const report = this.monthlyReports.find(item => item.report_id === selected) || this.monthlyReports[0] || null;
                        this.monthlyReport = report;
                        if (notify) {
                            const generatedCount = this.monthlyReports.filter(item => item.cache_status === 'generated').length;
                            const period = report?.period?.start && report?.period?.end
                                ? `${report.period.start} 至 ${report.period.end}`
                                : '';
                            const message = generatedCount
                                ? `已生成 ${generatedCount} 份月报${period ? `，最新周期：${period}` : ''}`
                                : this.monthlyReports.length
                                    ? `当前月报已是最新${period ? `，最新周期：${period}` : ''}`
                                    : '暂无月度数据，请确认本月工作日榜单快照是否存在。';
                            this.showWeeklyReportRefreshToast('success', '月报已更新', message);
                        }
                        return true;
                    } catch (e) {
                        this.monthlyReports = [];
                        this.monthlyReport = null;
                        const message = e.message || '加载月度报告失败';
                        this.monthlyReportError = message;
                        this.analyticsError = message;
                        if (notify) this.showWeeklyReportRefreshToast('danger', '月报更新失败', message);
                        return false;
                    } finally {
                        this.monthlyReportLoading = false;
                    }
                },

                showWeeklyReportRefreshToast(kind, title, message) {
                    if (window.aiworkerUi?.showToast) {
                        window.aiworkerUi.showToast({kind, title, message});
                        return;
                    }
                    window.alert(`${title}：${message}`);
                },

                async selectWeeklyReport(reportId) {
                    if (!reportId) return;
                    try {
                        const res = await aiworkerApi.request(`/api/analytics/weekly-reports/${encodeURIComponent(reportId)}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '加载周报详情失败');
                        this.weeklyReport = data;
                        this.$nextTick(() => this.renderAnalyticsCharts());
                    } catch (e) {
                        this.weeklyReportError = e.message || '加载周报详情失败';
                    }
                },

                async selectMonthlyReport(reportId) {
                    if (!reportId) return;
                    try {
                        const res = await aiworkerApi.request(`/api/analytics/monthly-reports/${encodeURIComponent(reportId)}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.detail || '加载月报详情失败');
                        this.monthlyReport = data;
                        this.$nextTick(() => this.renderAnalyticsCharts());
                    } catch (e) {
                        this.monthlyReportError = e.message || '加载月报详情失败';
                    }
                },

                selectAnalyticsReport(reportId) {
                    return this.analyticsReportMode === 'monthly'
                        ? this.selectMonthlyReport(reportId)
                        : this.selectWeeklyReport(reportId);
                },

                weeklyReportExport(format) {
                    const reportId = this.weeklyReport?.report_id;
                    if (!reportId) return;
                    window.open(`/api/analytics/weekly-reports/${encodeURIComponent(reportId)}/export?format=${encodeURIComponent(format)}`, '_blank', 'noopener');
                },

                monthlyReportExport(format) {
                    const reportId = this.monthlyReport?.report_id;
                    if (!reportId) return;
                    window.open(`/api/analytics/monthly-reports/${encodeURIComponent(reportId)}/export?format=${encodeURIComponent(format)}`, '_blank', 'noopener');
                },

                analyticsReportExport(format) {
                    return this.analyticsReportMode === 'monthly'
                        ? this.monthlyReportExport(format)
                        : this.weeklyReportExport(format);
                },

                renderAnalyticsCharts() {
                    if (!window.echarts) return;
                    this.renderPieChart('categoryChart', this.analyticsCategoryRows(), '题材标签');
                    this.renderTrendChart();
                },

                resizeAnalyticsCharts() {
                    if (!window.echarts) return;
                    ['categoryChart', 'trendChart'].forEach(id => {
                        const el = document.getElementById(id);
                        if (!el || !echarts.getInstanceByDom(el)) return;
                        echarts.getInstanceByDom(el).resize();
                    });
                },

                renderPieChart(elementId, rows, name) {
                    const el = document.getElementById(elementId);
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'item'},
                        series: [{
                            name,
                            type: 'pie',
                            radius: ['40%', '70%'],
                            data: rows.slice(0, 12).map(item => ({name: item.tag || '-', value: item.count || 0})),
                            label: {color: '#f1f4f8'}
                        }]
                    });
                },

                renderBarChart(elementId, rows, name) {
                    const el = document.getElementById(elementId);
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    const data = rows.slice(0, 10).reverse();
                    chart.setOption({
                        backgroundColor: 'transparent',
                        tooltip: {trigger: 'axis'},
                        grid: {left: 80, right: 20, top: 20, bottom: 30},
                        xAxis: {type: 'value', axisLabel: {color: '#9aa4b2'}},
                        yAxis: {type: 'category', data: data.map(item => this.shortLabel(item.tag || '-')), axisLabel: {color: '#f1f4f8', width: 72, overflow: 'truncate'}},
                        series: [{name, type: 'bar', data: data.map(item => item.count || 0), itemStyle: {color: '#3b95f6'}}]
                    });
                },

                analyticsCategoryRows() {
                    if (this.analyticsReportMode === 'monthly') return this.monthlyReport?.tags?.theme_tags || [];
                    return this.weeklyReport?.tags?.theme_tags || [];
                },

                analyticsTrendData() {
                    if (this.analyticsReportMode === 'monthly') {
                        return {
                            points: this.monthlyReport?.recommendation_metrics?.points || [],
                            series: this.monthlyReport?.recommendation_metrics?.series || [],
                        };
                    }
                    return {
                        points: this.weeklyReport?.recommendation_metrics?.points || [],
                        series: this.weeklyReport?.recommendation_metrics?.series || [],
                    };
                },

                formatAnalyticsMetric(value) {
                    const amount = Number(value || 0);
                    if (Math.abs(amount) >= 100000000) return `${(amount / 100000000).toFixed(1)}亿`;
                    if (Math.abs(amount) >= 10000) return `${(amount / 10000).toFixed(1)}万`;
                    return String(amount);
                },

                renderTrendChart() {
                    const el = document.getElementById('trendChart');
                    if (!el) return;
                    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
                    chart.clear();
                    const trend = this.analyticsTrendData();
                    const points = trend.points;
                    chart.setOption({
                        backgroundColor: 'transparent',
                        color: RECOMMENDATION_SERIES_COLORS,
                        tooltip: {
                            trigger: 'axis',
                            formatter: params => {
                                const date = params[0]?.axisValue || '';
                                const rows = params.map(point => {
                                    const item = trend.series[point.seriesIndex];
                                    if (!item) return '';
                                    const raw = item.values[point.dataIndex];
                                    const average = item.period_average ?? item.weekly_average;
                                    const averageLabel = this.analyticsReportMode === 'monthly' ? '月均' : '周均';
                                    return `${point.marker}${item.title} · ${item.metric_label}：${raw == null ? '-' : this.formatAnalyticsMetric(raw)}（${averageLabel} ${this.formatAnalyticsMetric(average)}，指数 ${point.value ?? '-'}）`;
                                }).filter(Boolean);
                                return [date, ...rows].join('<br/>');
                            },
                        },
                        legend: {top: 0, type: 'scroll', textStyle: {color: '#dbe4ee'}},
                        grid: {left: 16, right: 24, top: 50, bottom: 48, containLabel: true},
                        xAxis: {type: 'category', boundaryGap: false, data: points, axisLabel: {color: '#9aa4b2', hideOverlap: true}},
                        yAxis: {type: 'value', name: this.analyticsReportMode === 'monthly' ? '月均=100' : '周均=100', nameTextStyle: {color: '#9aa4b2'}, axisLabel: {color: '#9aa4b2'}},
                        series: trend.series.map((item, index) => ({
                            name: `${item.title} · ${item.metric_label}`,
                            type: 'line',
                            smooth: true,
                            data: item.index_values,
                            showSymbol: true,
                            itemStyle: {color: RECOMMENDATION_SERIES_COLORS[index % RECOMMENDATION_SERIES_COLORS.length]},
                            lineStyle: {width: 2},
                        })),
                    });
                },

                applyFileSearch() {
                    clearTimeout(this.filePage.searchTimer);
                    this.filePage.requestSequence += 1;
                    this.filePage.error = '';
                    this.filePage.loading = true;
                    this.filePage.searchTimer = setTimeout(() => this.fetchFiles({reset: true}), 250);
                },

                fileSearchStatus() {
                    const page = this.filePage;
                    const pendingQuery = this.fileSearch.trim();
                    if (page.loading) {
                        return pendingQuery ? `正在检索“${pendingQuery}”…` : '正在加载文件…';
                    }
                    if (page.error) return page.error;
                    if (page.appliedQuery) {
                        return `已按文件名或路径检索“${page.appliedQuery}”，找到 ${page.total} 个文件`;
                    }
                    return `共 ${page.total} 个文件`;
                },

                clearFileSelection() {
                    this.selectedFilePaths = [];
                    this.filePage.selectionMode = 'explicit';
                    this.filePage.excludedPaths = [];
                    this.filePage.selectionFilter = this.fileFilter;
                    this.filePage.selectionQuery = this.fileSearch.trim();
                },

                nextFilePage() {
                    if (!this.filePage.nextCursor || this.filePage.loading) return;
                    this.filePage.cursorHistory.push(this.filePage.currentCursor);
                    this.fetchFiles({cursor: this.filePage.nextCursor});
                },

                previousFilePage() {
                    if (!this.filePage.cursorHistory.length || this.filePage.loading) return;
                    this.filePage.cursorHistory.pop();
                    const cursor = this.filePage.cursorHistory.at(-1) || null;
                    this.fetchFiles({cursor});
                },

                groupedFiles() {
                    const groupOrder = ['视频', '音频', '剧本与文稿', '转写数据', '元数据', '其他文件'];
                    const dramas = new Map();
                    for (const file of this.filteredFiles) {
                        const dramaName = file.drama_name || '其他数据';
                        if (!dramas.has(dramaName)) dramas.set(dramaName, {drama_name: dramaName, count: 0, groups: new Map()});
                        const drama = dramas.get(dramaName);
                        const typeName = file.file_group || '其他文件';
                        if (!drama.groups.has(typeName)) drama.groups.set(typeName, []);
                        drama.groups.get(typeName).push(file);
                        drama.count += 1;
                    }
                    return [...dramas.values()]
                        .sort((left, right) => left.drama_name === '其他数据' ? 1 : (right.drama_name === '其他数据' ? -1 : 0))
                        .map(group => ({
                            drama_name: group.drama_name,
                            count: group.count,
                            file_groups: [...group.groups.entries()]
                                .sort((left, right) => groupOrder.indexOf(left[0]) - groupOrder.indexOf(right[0]))
                                .map(([name, files]) => ({name, files})),
                        }));
                },

                isFileSelected(path) {
                    if (this.filePage.selectionMode === 'all_matching') {
                        return !this.filePage.excludedPaths.includes(path);
                    }
                    return this.selectedFilePaths.includes(path);
                },

                allVisibleFilesSelected() {
                    return this.filteredFiles.length > 0 && this.filteredFiles.every(file => this.isFileSelected(file.path));
                },

                selectedFileCount() {
                    return this.filePage.selectionMode === 'all_matching'
                        ? Math.max(0, this.filePage.total - this.filePage.excludedPaths.length)
                        : this.selectedFilePaths.length;
                },

                startBatchDelete() {
                    this.clearFileSelection();
                    this.batchDeleteMode = true;
                },

                cancelBatchDelete() {
                    this.clearFileSelection();
                    this.batchDeleteMode = false;
                },

                startBatchDownload() {
                    this.clearFileSelection();
                    this.filePage.operation = {operation: 'archive', status: 'preparing', progress: {completed: 0, total: 0, succeeded: 0, failed: 0}};
                    this.batchDownloadMode = true;
                },

                cancelBatchDownload() {
                    this.clearFileSelection();
                    this.filePage.operation = null;
                    this.batchDownloadMode = false;
                },

                toggleFileSelection(path, selected) {
                    if (this.filePage.selectionMode === 'all_matching') {
                        if (selected) this.filePage.excludedPaths = this.filePage.excludedPaths.filter(item => item !== path);
                        else if (!this.filePage.excludedPaths.includes(path)) this.filePage.excludedPaths.push(path);
                        return;
                    }
                    if (selected && !this.isFileSelected(path)) {
                        this.selectedFilePaths.push(path);
                    }
                    if (!selected) {
                        this.selectedFilePaths = this.selectedFilePaths.filter(item => item !== path);
                    }
                },

                toggleAllVisibleFiles(selected) {
                    this.toggleAllMatchingFiles(selected);
                },

                toggleAllMatchingFiles(selected) {
                    if (selected) {
                        this.selectedFilePaths = [];
                        this.filePage.excludedPaths = [];
                        this.filePage.selectionMode = 'all_matching';
                        this.filePage.selectionFilter = this.fileFilter;
                        this.filePage.selectionQuery = this.fileSearch.trim();
                    } else {
                        this.clearFileSelection();
                    }
                },

                async downloadSelectedFiles() {
                    await this.startFileOperation('archive');
                },

                fileOperationSelection() {
                    return this.filePage.selectionMode === 'all_matching'
                        ? {mode: 'all_matching', filter: this.filePage.selectionFilter, query: this.filePage.selectionQuery, excluded_paths: this.filePage.excludedPaths}
                        : {mode: 'explicit', paths: [...new Set(this.selectedFilePaths)]};
                },

                async startFileOperation(operation) {
                    if (!this.selectedFileCount()) return;
                    try {
                        const response = await aiworkerApi.request('/api/files/operations', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({operation, selection: this.fileOperationSelection()}),
                        });
                        if (!response.ok) {
                            const data = await response.json().catch(() => ({}));
                            throw new Error(data.detail || '批量任务创建失败');
                        }
                        const data = await response.json();
                        this.filePage.operation = {jobId: data.job_id, operation, status: 'running', progress: {completed: 0, total: this.selectedFileCount()}};
                        this.pollFileOperation();
                    } catch (error) {
                        alert(error.message || '批量任务创建失败，请检查服务状态后重试');
                    }
                },

                async pollFileOperation() {
                    const operation = this.filePage.operation;
                    if (!operation?.jobId) return;
                    try {
                        const response = await aiworkerApi.request(`/api/jobs/${operation.jobId}`);
                        if (!response.ok) throw new Error('读取批量任务失败');
                        const job = await response.json();
                        operation.status = job.status;
                        operation.progress = job.progress || operation.progress;
                        if (['pending', 'running', 'stopping'].includes(job.status)) {
                            setTimeout(() => this.pollFileOperation(), 1000);
                        } else if (job.status === 'succeeded' && operation.operation === 'delete') {
                            this.clearFileSelection();
                            await this.fetchFiles({reset: true});
                        } else if (job.status === 'succeeded' && operation.operation === 'archive') {
                            const progress = operation.progress || {};
                            progress.succeeded = progress.succeeded ?? progress.completed ?? 0;
                            progress.failed = progress.failed ?? 0;
                            operation.progress = progress;
                        } else if (['failed', 'cancelled'].includes(job.status) && operation.operation === 'archive') {
                            this.cancelBatchDownload();
                        }
                    } catch (error) {
                        operation.status = 'failed';
                    }
                },

                downloadFileOperation() {
                    const jobId = this.filePage.operation?.jobId;
                    if (jobId) window.open(`/api/files/operations/${encodeURIComponent(jobId)}/download`, '_blank', 'noopener');
                },

                async deleteSelectedFiles() {
                    const count = this.selectedFileCount();
                    if (!count || !confirm(`确认删除已选的 ${count} 个文件吗？本地文件将永久移除。`)) return;
                    await this.startFileOperation('delete');
                },

                async deleteFiles(paths) {
                    const selectedPaths = [...new Set(paths || [])];
                    if (!selectedPaths.length) return;
                    const label = selectedPaths.length === 1 ? '该文件' : `已选的 ${selectedPaths.length} 个文件`;
                    if (!confirm(`确认删除${label}吗？本地文件将永久移除。`)) return;
                    try {
                        const res = await aiworkerApi.request('/api/files', {
                            method: 'DELETE',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({paths: selectedPaths})
                        });
                        const data = await res.json().catch(() => ({}));
                        if (!res.ok || data.status === 'error') {
                            alert(data.message || '删除失败，请刷新后重试');
                            return;
                        }
                        const deletedPaths = data.deleted_paths || [];
                        this.selectedFilePaths = this.selectedFilePaths.filter(path => !deletedPaths.includes(path));
                        await this.fetchFiles({reset: true});
                        if (data.status === 'partial_success') {
                            alert(`已删除 ${deletedPaths.length} 个文件，${(data.failed || []).length} 个文件删除失败`);
                        } else {
                            alert(`已删除 ${deletedPaths.length} 个文件`);
                        }
                    } catch (e) {
                        alert('删除失败，请检查服务状态后重试');
                    }
                },

                async openFilePreview(file) {
                    const kind = file.preview_kind || 'none';
                    this.previewFile = {status: 'loading', file, kind, url: '', content: '', message: ''};
                    this.filePreviewModal.show();
                    if (['video', 'audio'].includes(kind)) {
                        this.previewFile.url = `/api/file/preview?path=${encodeURIComponent(file.path)}`;
                        this.previewFile.status = 'success';
                        return;
                    }
                    if (!['text', 'json'].includes(kind)) {
                        this.previewFile.status = 'error';
                        this.previewFile.message = '该文件格式暂不支持在线预览';
                        return;
                    }
                    try {
                        const res = await aiworkerApi.request(`/api/file/content?path=${encodeURIComponent(file.path)}`);
                        const data = await res.json();
                        if (!res.ok) throw new Error(data.message || '读取文件失败');
                        this.previewFile.content = data.content || '';
                        this.previewFile.status = 'success';
                    } catch (e) {
                        this.previewFile.status = 'error';
                        this.previewFile.message = e.message || '读取文件失败';
                    }
                },

                closeFilePreview() {
                    this.previewFile = {status: 'idle', file: null, kind: '', url: '', content: '', message: ''};
                },

                async analyzeAudio(file) {
                    this.audioAnalysis = {status: 'loading', file_name: file.name};
                    this.audioModal.show();
                    try {
                        const res = await aiworkerApi.request('/api/audio/analyze', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({file_path: file.path})
                        });
                        const data = await res.json();
                        if (!res.ok || data.status === 'error') {
                            this.audioAnalysis = {status: 'error', message: data.message || '分析失败'};
                            return;
                        }
                        this.audioAnalysis = data;
                    } catch (e) {
                        this.audioAnalysis = {status: 'error', message: '分析失败'};
                    }
                },

                downloadAudioAnalysis() {
                    if (this.audioAnalysis.status !== 'success') return;
                    const lines = [
                        `文件：${this.audioAnalysis.file_name}`,
                        `时间：${this.audioAnalysis.analysis_time}`,
                        '',
                        '## 摘要',
                        this.audioAnalysis.summary || '',
                        '',
                        '## 关键点',
                        ...(this.audioAnalysis.key_points || []).map(item => `- ${item}`),
                        '',
                        '## 转写文本',
                        this.audioAnalysis.transcript || ''
                    ];
                    const blob = new Blob([lines.join('\n')], {type: 'text/markdown;charset=utf-8'});
                    const url = URL.createObjectURL(blob);
                    const link = document.createElement('a');
                    const stem = String(this.audioAnalysis.file_name || 'audio').replace(/\.[^.]+$/, '');
                    link.href = url;
                    link.download = `${stem}_analysis.md`;
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    URL.revokeObjectURL(url);
                },

                async fetchConfigForm() {
                    try {
                        const res = await aiworkerApi.request('/api/config/form');
                        const data = await res.json();
                        if (data.status === 'error') {
                            alert(data.message || '加载配置失败');
                            return;
                        }
                        this.configForm = {
                            root: data.root || {},
                            items: (data.items || []).map(item => this.normalizeConfigItem(item))
                        };
                    } catch (e) {
                        alert('加载配置失败');
                    }
                },

                normalizeConfigItem(item = {}) {
                    return {
                        project: item.project || '',
                        '评论不少于': Number(item['评论不少于'] || 0),
                        '搜索关键词': item['搜索关键词'] || '',
                        '屏蔽关键词': item['屏蔽关键词'] || '',
                        '有关视频判定': item['有关视频判定'] || '',
                        '黑名单视频判定': item['黑名单视频判定'] || '',
                        '作品天数': Number(item['作品天数'] || 0),
                        '白名单地区': item['白名单地区'] || '',
                        '黑名单地区': item['黑名单地区'] || ''
                    };
                },

                addConfigItem() {
                    this.configForm.items.push(this.normalizeConfigItem({
                        '评论不少于': 3,
                        '作品天数': 90
                    }));
                },

                removeConfigItem(index) {
                    if (!confirm('删除该项目配置？保存后会写入 data.json。')) return;
                    this.configForm.items.splice(index, 1);
                },

                async saveConfigForm() {
                    const crawlerType = String(this.configForm.root['默认爬取类型'] || '').toLowerCase();
                    const isRank = crawlerType.includes('rank') || crawlerType.includes('榜单');
                    if (!isRank) {
                        if (this.configForm.items.length === 0) {
                            alert('搜索类型至少需要一个项目配置');
                            return;
                        }
                        const missing = this.configForm.items.findIndex(item => !String(item['搜索关键词'] || '').trim());
                        if (missing >= 0) {
                            alert(`第 ${missing + 1} 个项目缺少搜索关键词`);
                            return;
                        }
                    }
                    const res = await aiworkerApi.request('/api/config/form', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(this.configForm)
                    });
                    const data = await res.json();
                    if (!res.ok || data.status !== 'success') {
                        alert(data.message || '保存失败');
                        return;
                    }
                    alert('保存成功');
                    await this.loadConfig();
                    await this.fetchConfigForm();
                    await this.fetchBackups();
                },

                async fetchBackups() {
                    try {
                        const res = await aiworkerApi.request('/api/config/backups');
                        this.backups = await res.json();
                    } catch (e) {
                        this.backups = [];
                    }
                },

                async createBackup() {
                    const res = await aiworkerApi.request('/api/config/backup/create', {method: 'POST'});
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '创建失败');
                    await this.fetchBackups();
                },

                async restoreBackup(filename) {
                    if (!confirm(`恢复备份 ${filename}？当前配置会被覆盖。`)) return;
                    const res = await aiworkerApi.request('/api/config/backup/restore', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename})
                    });
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '恢复失败');
                    await this.fetchConfigForm();
                    await this.loadConfig();
                },

                async deleteBackup(filename) {
                    if (!confirm(`删除备份 ${filename}？`)) return;
                    const res = await aiworkerApi.request('/api/config/backup/delete', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({filename})
                    });
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '删除失败');
                    await this.fetchBackups();
                },

                async openProjectModal(filePath, mode) {
                    this.modalMode = mode;
                    this.modalFile = filePath;
                    this.selectedModalProjects = [];
                    this.modalProjects = [];
                    try {
                        const res = await aiworkerApi.request('/api/file/analyze', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({file_path: filePath})
                        });
                        const data = await res.json();
                        this.modalProjects = Object.entries(data)
                            .map(([name, count]) => ({name, count}))
                            .sort((a, b) => a.name.localeCompare(b.name));
                        this.selectedModalProjects = this.modalProjects.map(item => item.name);
                        this.projectModal.show();
                    } catch (e) {
                        alert('分析文件失败');
                    }
                },

                moveProjectUp(index) {
                    if (index <= 0) return;
                    const item = this.modalProjects[index];
                    this.modalProjects[index] = this.modalProjects[index - 1];
                    this.modalProjects[index - 1] = item;
                },

                moveProjectDown(index) {
                    if (index >= this.modalProjects.length - 1) return;
                    const item = this.modalProjects[index];
                    this.modalProjects[index] = this.modalProjects[index + 1];
                    this.modalProjects[index + 1] = item;
                },

                async confirmBatchAction() {
                    if (this.selectedModalProjects.length === 0) {
                        alert('请至少选择一个项目');
                        return;
                    }
                    const projects = this.modalProjects
                        .filter(item => this.selectedModalProjects.includes(item.name))
                        .map(item => item.name);
                    const endpoint = this.modalMode === 'crawl' ? '/api/batch/run' : '/api/export/excel';
                    const res = await aiworkerApi.request(endpoint, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({
                            file_path: this.modalFile,
                            projects,
                            auto_upload: this.autoUpload
                        })
                    });
                    const data = await res.json();
                    alert(data.message || '已提交');
                    this.projectModal.hide();
                },

                async fetchQrcodeStatus() {
                    try {
                        const res = await aiworkerApi.request('/api/qrcode/status');
                        this.qrcodeStatus = await res.json();
                    } catch (e) {}
                },

                async onLoginTypeChange() {
                    if (this.config.login_type === 'qrcode') await this.fetchQrcodeStatus();
                },

                onPlatformChange() {
                    if (this.config.crawler_type === 'rank' && !['dy', 'hg'].includes(this.config.platform)) {
                        this.config.crawler_type = 'search';
                        alert('榜单爬取仅支持抖音和红果，已切换为搜索类型');
                    }
                },

                async startQrcodeLogin() {
                    if (this.isRunning) {
                        alert('爬虫正在运行，请先停止');
                        return;
                    }
                    this.qrcodeLoggingIn = true;
                    try {
                        const res = await aiworkerApi.request('/api/qrcode/login', {method: 'POST'});
                        const data = await res.json();
                        if (data.status === 'success') {
                            this.pollQrcodeStatus();
                        } else {
                            alert(data.message || '启动扫码失败');
                            this.qrcodeLoggingIn = false;
                        }
                    } catch (e) {
                        alert('启动扫码失败');
                        this.qrcodeLoggingIn = false;
                    }
                },

                pollQrcodeStatus() {
                    const timer = setInterval(async () => {
                        await this.fetchQrcodeStatus();
                        if (this.qrcodeStatus.has_state) {
                            clearInterval(timer);
                            this.qrcodeLoggingIn = false;
                            alert('扫码登录成功');
                        }
                    }, 3000);
                    setTimeout(() => {
                        clearInterval(timer);
                        this.qrcodeLoggingIn = false;
                    }, 300000);
                },

                async clearQrcodeLogin() {
                    if (!confirm('清除保存的登录状态？')) return;
                    const res = await aiworkerApi.request('/api/qrcode/clear', {method: 'POST'});
                    const data = await res.json();
                    if (data.status !== 'success') alert(data.message || '清除失败');
                    await this.fetchQrcodeStatus();
                },

                llmResponseMessage(data, fallback) {
                    return data?.detail || data?.message || fallback;
                },

                llmSecretSafeMessage(error, fallback, secret) {
                    const message = String(error?.message || fallback);
                    return secret && message.includes(secret) ? fallback : message;
                },

                selectedLLMProvider() {
                    return this.llmSettings.providers.find(
                        provider => provider.id === this.llmSettings.selectedProviderId
                    ) || null;
                },

                defaultLLMModel() {
                    for (const provider of this.llmSettings.providers) {
                        const model = (provider.models || []).find(
                            item => item.id === this.llmSettings.defaultModelId
                        );
                        if (model) return {provider, model};
                    }
                    return null;
                },

                llmEndpointWarning(baseUrl) {
                    try {
                        const endpoint = new URL(String(baseUrl || '').trim());
                        const localHosts = ['localhost', '127.0.0.1', '::1'];
                        if (endpoint.protocol === 'http:' && !localHosts.includes(endpoint.hostname.toLowerCase())) {
                            return '该端点使用公网或局域网 HTTP，API Key 和请求内容可能被明文传输。';
                        }
                    } catch (_) {}
                    return '';
                },

                async fetchLLMSettings({selectedProviderId = ''} = {}) {
                    const state = this.llmSettings;
                    state.loading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request('/api/llm/settings');
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '读取 LLM 设置失败'));
                        state.presets = data.presets || {};
                        state.providers = Array.isArray(data.providers) ? data.providers : [];
                        state.defaultModelId = data.default_model_id || '';
                        const preferredId = selectedProviderId || state.selectedProviderId;
                        const provider = state.providers.find(item => item.id === preferredId)
                            || state.providers[0]
                            || null;
                        if (provider) this.selectLLMProvider(provider);
                        else this.startNewLLMProvider(Object.keys(state.presets)[0] || 'custom');
                    } catch (error) {
                        state.error = error.message || '读取 LLM 设置失败，请重试';
                    } finally {
                        state.loading = false;
                    }
                },

                selectLLMProvider(provider) {
                    if (!provider) return;
                    const state = this.llmSettings;
                    state.selectedProviderId = provider.id;
                    state.editor = {
                        id: provider.id,
                        name: provider.name || '',
                        preset: provider.preset || 'custom',
                        base_url: provider.base_url || '',
                        wire_api: provider.wire_api || 'chat_completions',
                        api_key_env_var: provider.api_key_env_var || '',
                        api_key: '',
                        has_api_key: Boolean(provider.has_api_key),
                        credential_status: provider.credential_status
                            || (provider.has_api_key ? 'configured' : 'missing'),
                        credential_source: provider.credential_source || 'missing',
                        is_enabled: provider.is_enabled !== false
                    };
                    state.modelDraft = {model_id: '', display_name: ''};
                    state.modelEditor = null;
                    state.test = {
                        model_id: (provider.models || []).find(model => model.is_default)?.model_id
                            || (provider.models || []).find(model => model.is_enabled)?.model_id
                            || '',
                        result: null
                    };
                    state.error = '';
                },

                startNewLLMProvider(presetName = 'openai') {
                    const state = this.llmSettings;
                    const preset = state.presets[presetName]
                        || state.presets.custom
                        || {label: '', base_url: '', wire_api: 'chat_completions'};
                    state.selectedProviderId = '';
                    state.editor = {
                        id: '',
                        name: preset.label || '',
                        preset: state.presets[presetName] ? presetName : 'custom',
                        base_url: preset.base_url || '',
                        wire_api: preset.wire_api || 'chat_completions',
                        api_key_env_var: preset.env_key || '',
                        api_key: '',
                        has_api_key: false,
                        credential_status: 'missing',
                        credential_source: 'missing',
                        is_enabled: true
                    };
                    state.modelDraft = {model_id: '', display_name: ''};
                    state.modelEditor = null;
                    state.test = {model_id: '', result: null};
                    state.error = '';
                    state.notice = '';
                    this.$nextTick?.(() => document.getElementById('llm-provider-name')?.focus());
                },

                applyLLMProviderPreset() {
                    const editor = this.llmSettings.editor;
                    const preset = this.llmSettings.presets[editor.preset];
                    if (!preset) return;
                    if (!editor.id) editor.name = preset.label || editor.name;
                    editor.base_url = preset.base_url || '';
                    editor.wire_api = preset.wire_api || 'chat_completions';
                    editor.api_key_env_var = preset.env_key || '';
                    editor.credential_status = 'missing';
                    this.llmSettings.test.result = null;
                },

                async saveLLMProvider() {
                    const state = this.llmSettings;
                    const editor = state.editor;
                    if (!String(editor.name || '').trim() || !String(editor.base_url || '').trim()
                        || !String(editor.api_key_env_var || '').trim()) {
                        state.error = '请填写提供商名称、Base URL 和 API Key 环境变量';
                        return;
                    }
                    state.saving = true;
                    state.error = '';
                    state.notice = '';
                    const apiKey = String(editor.api_key || '').trim();
                    const payload = {
                        name: String(editor.name).trim(),
                        preset: editor.preset,
                        base_url: String(editor.base_url).trim(),
                        wire_api: editor.wire_api,
                        api_key_env_var: String(editor.api_key_env_var).trim(),
                        is_enabled: Boolean(editor.is_enabled)
                    };
                    try {
                        const response = await aiworkerApi.request(
                            editor.id ? `/api/llm/providers/${editor.id}` : '/api/llm/providers',
                            {
                                method: editor.id ? 'PUT' : 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify(payload)
                            }
                        );
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '保存提供商失败'));
                        const providerId = data.id || editor.id;
                        if (apiKey) {
                            if (!providerId) throw new Error('保存提供商失败');
                            const credentialResponse = await aiworkerApi.request(
                                `/api/llm/providers/${providerId}/credential`,
                                {
                                    method: 'PUT',
                                    headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({api_key: apiKey})
                                }
                            );
                            let credentialData = {};
                            try { credentialData = await credentialResponse.json(); } catch (_) {}
                            if (!credentialResponse.ok) {
                                throw new Error(this.llmResponseMessage(credentialData, '保存页面密钥失败'));
                            }
                            state.notice = '新密钥已生效';
                        } else {
                            state.notice = editor.id ? '提供商设置已更新' : '提供商已添加';
                        }
                        await this.fetchLLMSettings({selectedProviderId: providerId});
                    } catch (error) {
                        state.error = this.llmSecretSafeMessage(error, '保存提供商失败', apiKey);
                    } finally {
                        editor.api_key = '';
                        state.saving = false;
                    }
                },

                async clearLLMProviderCredential() {
                    const state = this.llmSettings;
                    const providerId = state.editor.id;
                    if (!providerId || state.editor.credential_source !== 'credential_store' || state.saving) return;
                    state.saving = true;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/llm/providers/${providerId}/credential`,
                            {method: 'DELETE'}
                        );
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '清除页面密钥失败'));
                        state.notice = '页面密钥已清除';
                        await this.fetchLLMSettings({selectedProviderId: providerId});
                    } catch (error) {
                        state.error = this.llmSecretSafeMessage(error, '清除页面密钥失败', String(state.editor.api_key || '').trim());
                    } finally {
                        state.editor.api_key = '';
                        state.saving = false;
                    }
                },

                async deleteLLMProvider() {
                    const state = this.llmSettings;
                    const provider = this.selectedLLMProvider();
                    if (!provider?.id || provider.is_default || state.providerDeletingId) return;
                    const label = provider.name || provider.id;
                    const modelCount = (provider.models || []).length;
                    if (!window.confirm(
                        `确认删除提供商「${label}」？这会同时删除 ${modelCount} 个模型和环境变量名称配置，但不会删除环境变量中的凭据，且无法撤销。`
                    )) return;
                    state.providerDeletingId = provider.id;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/llm/providers/${provider.id}`,
                            {method: 'DELETE'}
                        );
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '删除提供商失败'));
                        const nextProvider = state.providers.find(item => item.id !== provider.id);
                        await this.fetchLLMSettings({selectedProviderId: nextProvider?.id || ''});
                        state.notice = `提供商 ${label} 已删除`;
                    } catch (error) {
                        state.error = error.message || '删除提供商失败';
                    } finally {
                        state.providerDeletingId = '';
                    }
                },

                async syncLLMModels() {
                    const state = this.llmSettings;
                    const providerId = state.editor.id;
                    if (!providerId || state.syncing) return;
                    state.syncing = true;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/llm/providers/${providerId}/models/sync`,
                            {method: 'POST'}
                        );
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '模型同步失败'));
                        state.notice = `模型同步完成：新增 ${Number(data.added || 0)} 个，更新 ${Number(data.updated || 0)} 个`;
                        await this.fetchLLMSettings({selectedProviderId: providerId});
                    } catch (error) {
                        const message = error.message || '模型同步失败';
                        state.error = message.includes('手工新增')
                            ? message
                            : `${message}；可继续使用下方表单手工新增模型。`;
                    } finally {
                        state.syncing = false;
                    }
                },

                async addLLMModel() {
                    const state = this.llmSettings;
                    const providerId = state.editor.id;
                    const modelId = String(state.modelDraft.model_id || '').trim();
                    if (!providerId || !modelId) {
                        state.error = '请先保存提供商并填写模型 ID';
                        return;
                    }
                    state.modelSaving = true;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/llm/providers/${providerId}/models`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    model_id: modelId,
                                    display_name: String(state.modelDraft.display_name || '').trim(),
                                    is_enabled: true
                                })
                            }
                        );
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '添加模型失败'));
                        state.modelDraft = {model_id: '', display_name: ''};
                        state.notice = '模型已添加';
                        await this.fetchLLMSettings({selectedProviderId: providerId});
                    } catch (error) {
                        state.error = error.message || '添加模型失败';
                    } finally {
                        state.modelSaving = false;
                    }
                },

                startEditLLMModel(model) {
                    this.llmSettings.modelEditor = {
                        id: model.id,
                        model_id: model.model_id || '',
                        display_name: model.display_name || '',
                        is_enabled: model.is_enabled !== false,
                        is_default: Boolean(model.is_default)
                    };
                    this.llmSettings.error = '';
                },

                async saveLLMModel() {
                    const state = this.llmSettings;
                    const editor = state.modelEditor;
                    if (!editor || !String(editor.model_id || '').trim()) {
                        state.error = '模型 ID 不能为空';
                        return;
                    }
                    state.modelSaving = true;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(`/api/llm/models/${editor.id}`, {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({
                                model_id: String(editor.model_id).trim(),
                                display_name: String(editor.display_name || '').trim(),
                                is_enabled: Boolean(editor.is_enabled)
                            })
                        });
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '更新模型失败'));
                        state.modelEditor = null;
                        state.notice = '模型设置已更新';
                        await this.fetchLLMSettings({selectedProviderId: state.editor.id});
                    } catch (error) {
                        state.error = error.message || '更新模型失败';
                    } finally {
                        state.modelSaving = false;
                    }
                },

                async deleteLLMModel(model) {
                    const state = this.llmSettings;
                    if (!model?.id || model.is_default || state.modelDeletingId) return;
                    const label = model.display_name || model.model_id;
                    if (!window.confirm(`确认删除模型「${label}」？删除后可重新同步或手工添加。`)) return;
                    state.modelDeletingId = model.id;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request(`/api/llm/models/${model.id}`, {
                            method: 'DELETE'
                        });
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '删除模型失败'));
                        if (state.modelEditor?.id === model.id) state.modelEditor = null;
                        state.notice = `模型 ${label} 已删除`;
                        await this.fetchLLMSettings({selectedProviderId: state.editor.id});
                    } catch (error) {
                        state.error = error.message || '删除模型失败';
                    } finally {
                        state.modelDeletingId = '';
                    }
                },

                async setDefaultLLMModel(model) {
                    const state = this.llmSettings;
                    if (!model?.id || state.defaultSavingId) return;
                    state.defaultSavingId = model.id;
                    state.error = '';
                    state.notice = '';
                    try {
                        const response = await aiworkerApi.request('/api/llm/default-model', {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify({model_id: model.id})
                        });
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '切换默认模型失败'));
                        state.notice = `默认模型已切换为 ${model.display_name || model.model_id}`;
                        await this.fetchLLMSettings({selectedProviderId: state.editor.id});
                    } catch (error) {
                        state.error = error.message || '切换默认模型失败';
                    } finally {
                        state.defaultSavingId = '';
                    }
                },

                async testLLMConnection() {
                    const state = this.llmSettings;
                    const editor = state.editor;
                    const modelId = String(state.test.model_id || '').trim();
                    if (!String(editor.base_url || '').trim() || !modelId) {
                        state.error = '请填写 Base URL 和测试模型 ID';
                        return;
                    }
                    if (state.testing) return;
                    state.testing = true;
                    state.error = '';
                    state.test.result = null;
                    const apiKey = String(editor.api_key || '').trim();
                    const payload = {
                        ...(editor.id ? {provider_id: editor.id} : {}),
                        base_url: String(editor.base_url).trim(),
                        api_key_env_var: String(editor.api_key_env_var || '').trim(),
                        ...(apiKey ? {api_key: apiKey} : {}),
                        model_id: modelId,
                        wire_api: editor.wire_api
                    };
                    try {
                        const response = await aiworkerApi.request('/api/llm/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload)
                        });
                        let data = {};
                        try { data = await response.json(); } catch (_) {}
                        if (!response.ok) throw new Error(this.llmResponseMessage(data, '模型连接测试失败'));
                        state.test.result = data;
                    } catch (error) {
                        const message = this.llmSecretSafeMessage(error, '模型连接测试失败', apiKey);
                        state.test.result = {status: 'error', message};
                        state.error = message;
                    } finally {
                        editor.api_key = '';
                        state.testing = false;
                    }
                },

                async fetchDramaSettings() {
                    try {
                        const response = await aiworkerApi.request('/api/hongguo/drama/settings');
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.message || '读取设置失败');
                        this.dramaAnalysis.settings = {...data, activation_code: ''};
                    } catch (error) {
                        this.dramaAnalysis.error = error.message || '读取红果抓包设置失败';
                    }
                },

                async saveDramaSettings() {
                    this.dramaAnalysis.loading = true;
                    this.dramaAnalysis.error = '';
                    try {
                        const activationCode = String(this.dramaAnalysis.settings.activation_code || '').trim();
                        const payload = {
                            config_url: this.dramaAnalysis.settings.config_url,
                            capture_root: this.dramaAnalysis.settings.capture_root,
                            capture_service_url: this.dramaAnalysis.settings.capture_service_url,
                            wait_timeout_seconds: Number(this.dramaAnalysis.settings.wait_timeout_seconds),
                            ...(activationCode ? {activation_code: activationCode} : {})
                        };
                        const response = await aiworkerApi.request('/api/hongguo/drama/settings', {
                            method: 'PUT',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload),
                        });
                        const data = await response.json();
                        if (!response.ok || data.status === 'error') throw new Error(data.message || '保存设置失败');
                        this.dramaAnalysis.settings = {...data, activation_code: ''};
                        alert('设置已保存');
                    } catch (error) {
                        this.dramaAnalysis.error = error.message || '保存设置失败';
                    } finally {
                        this.dramaAnalysis.loading = false;
                    }
                },

                async testDramaInterface() {
                    if (this.dramaAnalysis.interfaceTesting) return;
                    this.dramaAnalysis.interfaceTesting = true;
                    this.dramaAnalysis.error = '';
                    this.dramaAnalysis.interfaceTest = null;
                    try {
                        const activationCode = String(this.dramaAnalysis.settings.activation_code || '').trim();
                        const payload = {
                            config_url: this.dramaAnalysis.settings.config_url,
                            capture_root: this.dramaAnalysis.settings.capture_root,
                            capture_service_url: this.dramaAnalysis.settings.capture_service_url,
                            wait_timeout_seconds: Number(this.dramaAnalysis.settings.wait_timeout_seconds),
                            ...(activationCode ? {activation_code: activationCode} : {})
                        };
                        const response = await aiworkerApi.request('/api/hongguo/drama/test', {
                            method: 'POST',
                            headers: {'Content-Type': 'application/json'},
                            body: JSON.stringify(payload),
                        });
                        const data = await response.json();
                        this.dramaAnalysis.interfaceTest = {
                            status: data.status || (response.ok ? 'success' : 'error'),
                            message: data.message || (response.ok ? '来源与服务检查完成' : '来源或服务检查失败'),
                            checks: Array.isArray(data.checks) ? data.checks : [],
                        };
                    } catch (error) {
                        this.dramaAnalysis.interfaceTest = {
                            status: 'error',
                            message: error.message || '检查来源与服务失败',
                            checks: [],
                        };
                    } finally {
                        this.dramaAnalysis.interfaceTesting = false;
                    }
                },

}));
