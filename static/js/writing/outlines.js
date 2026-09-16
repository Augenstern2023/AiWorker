window.aiworkerModules = window.aiworkerModules || [];

const outlineSeriesOverviewFields = [
    ['core_event', '核心事件'],
    ['character_progress', '人物推进'],
    ['conflict', '主要冲突'],
    ['reversal', '关键反转'],
    ['highlight', '剧情亮点'],
    ['cliffhanger', '结尾悬念'],
];

function copyOutlineSeriesOverview(value) {
    const source = value && typeof value === 'object' ? value : {};
    return Object.fromEntries(outlineSeriesOverviewFields.map(([key]) => [key, String(source[key] || '')]));
}

window.aiworkerModules.push(() => ({
    filteredProjectOutlines() {
        const rawSelected = this.creativeWorkspace.selectedEpisode;
        const selected = rawSelected === undefined || rawSelected === null || rawSelected === ''
            ? 0
            : Number(rawSelected);
        const items = this.currentProjectOutlines().filter(
            item => selected === 0 || Number(item.episode_number) === selected
        );
        const query = String(this.creativeWorkspace.outlineQuery || '').trim().toLocaleLowerCase();
        if (!query) return items;
        return items.filter((item) => {
            const episode = String(Number(item.episode_number || 0));
            const event = String(item.content?.core_event || '').toLocaleLowerCase();
            return episode.includes(query) || event.includes(query);
        });
    },

    outlineEpisodeExpanded(episodeNumber) {
        const expanded = this.creativeWorkspace.expandedOutlineEpisodes || {};
        return Boolean(expanded[String(Number(episodeNumber))]);
    },

    toggleOutlineEpisode(episodeNumber) {
        const key = String(Number(episodeNumber));
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.creativeWorkspace.expandedOutlineEpisodes[key] = !this.outlineEpisodeExpanded(episodeNumber);
    },

    expandAllOutlineEpisodes() {
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.filteredProjectOutlines().forEach((item) => {
            this.creativeWorkspace.expandedOutlineEpisodes[String(Number(item.episode_number))] = true;
        });
    },

    collapseAllOutlineEpisodes() {
        if (!this.creativeWorkspace.expandedOutlineEpisodes) {
            this.creativeWorkspace.expandedOutlineEpisodes = {};
        }
        this.filteredProjectOutlines().forEach((item) => {
            this.creativeWorkspace.expandedOutlineEpisodes[String(Number(item.episode_number))] = false;
        });
    },

    currentOutlineSeriesOverview() {
        const value = this.currentProjectOutlines()[0]?.content?.series_overview;
        if (!value || typeof value !== 'object') return null;
        const valid = outlineSeriesOverviewFields.every(
            ([key]) => typeof value[key] === 'string' && value[key].trim()
        );
        return valid ? value : null;
    },

    outlineSeriesOverviewExists() {
        return Boolean(this.currentOutlineSeriesOverview());
    },

    outlineOverviewEditable() {
        if (this.isFreeCreativeProject?.()) return false;
        const first = this.currentProjectOutlines()[0];
        return Boolean(first && this.creativeResourceEditable(first));
    },

    outlineOverviewGenerationReady() {
        const required = Number(this.creativeWorkspace.project?.target_episode_count || 0);
        return required > 0 && this.currentProjectOutlines().length === required;
    },

    outlineOverviewGenerationAllowed() {
        if (this.isFreeCreativeProject?.()) return false;
        const first = this.currentProjectOutlines()[0];
        if (!first) return false;
        if (first.status === 'draft') return true;
        if (!['locked', 'approved'].includes(first.status)) return false;
        return String(this.latestOutlineBatchId() || '') === String(
            this.creativeWorkspace.project?.outline_batch_id || ''
        );
    },

    resetOutlineSeriesOverview() {
        this.creativeWorkspace.outlineOverviewExpanded = true;
        this.creativeWorkspace.outlineOverviewDraft = copyOutlineSeriesOverview(
            this.currentOutlineSeriesOverview()
        );
        this.creativeWorkspace.outlineOverviewErrors = {};
        this.creativeWorkspace.outlineOverviewSaving = false;
    },

    toggleOutlineSeriesOverview() {
        this.creativeWorkspace.outlineOverviewExpanded = !this.creativeWorkspace.outlineOverviewExpanded;
    },

    outlineSeriesOverviewDirty() {
        if (!this.outlineSeriesOverviewExists()) return false;
        const current = copyOutlineSeriesOverview(this.currentOutlineSeriesOverview());
        const draft = copyOutlineSeriesOverview(this.creativeWorkspace.outlineOverviewDraft);
        return outlineSeriesOverviewFields.some(([key]) => draft[key] !== current[key]);
    },

    clearOutlineOverviewError(key) {
        if (this.creativeWorkspace.outlineOverviewErrors?.[key]) {
            delete this.creativeWorkspace.outlineOverviewErrors[key];
        }
    },

    outlineOverviewErrorSummary() {
        return Object.values(this.creativeWorkspace.outlineOverviewErrors || {})
            .filter(Boolean)
            .join('；');
    },

    validateOutlineSeriesOverview() {
        const draft = copyOutlineSeriesOverview(this.creativeWorkspace.outlineOverviewDraft);
        const errors = {};
        outlineSeriesOverviewFields.forEach(([key, label]) => {
            draft[key] = draft[key].trim();
            if (!draft[key]) errors[key] = `请填写${label}`;
        });
        this.creativeWorkspace.outlineOverviewDraft = draft;
        this.creativeWorkspace.outlineOverviewErrors = errors;
        return Object.keys(errors).length ? null : draft;
    },

    async saveOutlineSeriesOverview() {
        if (!this.outlineOverviewEditable()) return null;
        const overview = this.validateOutlineSeriesOverview();
        if (!overview) return null;
        const projectId = this.creativeWorkspace.project?.id;
        const batchId = this.latestOutlineBatchId();
        const saveToken = Number(this.creativeWorkspace.outlineOverviewSaveSequence || 0) + 1;
        this.creativeWorkspace.outlineOverviewSaveSequence = saveToken;
        const requestIsCurrent = () => (
            Number(this.creativeWorkspace.outlineOverviewSaveSequence || 0) === saveToken
            && Number(this.creativeWorkspace.project?.id || 0) === Number(projectId)
            && String(this.latestOutlineBatchId() || '') === String(batchId || '')
        );
        this.creativeWorkspace.outlineOverviewSaving = true;
        try {
            const res = await aiworkerApi.request(
                `/api/creative/projects/${projectId}/outlines/${batchId}/overview`,
                {
                    method: 'PATCH',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({series_overview: overview}),
                }
            );
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || '保存全集剧情概览失败');
            const saved = copyOutlineSeriesOverview(data.series_overview || overview);
            if (!requestIsCurrent()) {
                this.showCreativeNotice('全集剧情概览已保存到原大纲版本，当前页面已切换');
                return data;
            }
            this.currentProjectOutlines().forEach((item) => {
                item.content = item.content || {};
                item.content.series_overview = {...saved};
            });
            this.creativeWorkspace.outlineOverviewDraft = {...saved};
            this.creativeWorkspace.outlineOverviewErrors = {};
            this.showCreativeNotice('全集剧情概览已保存');
            return data;
        } catch (error) {
            if (requestIsCurrent()) {
                this.creativeWorkspace.error = error.message || '保存全集剧情概览失败';
                this.showCreativeNotice(this.creativeWorkspace.error, 'error');
            }
            return null;
        } finally {
            if (
                Number(this.creativeWorkspace.outlineOverviewSaveSequence || 0)
                === saveToken
            ) {
                this.creativeWorkspace.outlineOverviewSaving = false;
            }
        }
    },

    async generateOutlineSeriesOverview() {
        const projectId = this.creativeWorkspace.project?.id;
        const batchId = this.latestOutlineBatchId();
        if (
            !projectId || !batchId || !this.outlineOverviewGenerationAllowed()
            || !this.outlineOverviewGenerationReady() || this.outlineSeriesOverviewExists()
        ) return null;
        try {
            const body = {};
            if (this.currentOutlineBatchStatus() === 'draft') {
                const task = await this.resolveCreativeWorkflowTask('outline', projectId);
                body.workflow_task_id = task.id;
            }
            return this.startCreativeGeneration(
                `/api/creative/projects/${projectId}/outlines/${batchId}/overview/generate`,
                this.promptGenerationPayload('outline', body),
                'outline_overview',
                {batchId}
            );
        } catch (error) {
            this.showCreativeNotice(error.message || '生成全集剧情概览失败', 'error');
            return null;
        }
    },

    outlineFieldRewriteAllowed(item) {
        if (!item || item.stage !== 'outline') return false;
        if (this.creativeResourceEditable(item)) return true;
        if (!['locked', 'approved'].includes(String(item.status || ''))) return false;
        const project = this.creativeWorkspace.project;
        if (this.isFreeCreativeProject?.()) {
            return Boolean(
                project
                && String(item.batch_id || '') === String(this.latestOutlineBatchId() || '')
                && String(item.batch_id || '') === String(this.creativeHead?.('outline')?.resource_ref || '')
            );
        }
        return Boolean(
            project
            && String(item.batch_id || '') === String(project.outline_batch_id || '')
            && String(item.batch_id || '') === String(this.latestOutlineBatchId() || '')
        );
    },

    openOutlineFieldRewrite(item, fieldKey, fieldLabel) {
        if (!this.outlineFieldRewriteAllowed(item)) return;
        const modalElement = document.getElementById('outlineFieldRewriteModal');
        if (modalElement && !modalElement.dataset.outlineFieldRewriteResetBound) {
            modalElement.dataset.outlineFieldRewriteResetBound = 'true';
            modalElement.addEventListener('hidden.bs.modal', () => this.resetOutlineFieldRewrite());
        }
        const previousSequence = Number(
            this.creativeWorkspace.outlineFieldRewrite?.requestSequence || 0
        );
        this.creativeWorkspace.outlineFieldRewrite = {
            projectId: Number(this.creativeWorkspace.project?.id || item.project_id || 0),
            batchId: String(item.batch_id || this.latestOutlineBatchId() || ''),
            episodeId: Number(item.id || 0),
            episodeNumber: Number(item.episode_number || 0),
            fieldKey: String(fieldKey || ''),
            fieldLabel: String(fieldLabel || ''),
            sourceStatus: String(item.status || ''),
            sourceText: String(item.content?.[fieldKey] || ''),
            sourceFingerprint: '',
            instruction: '',
            candidateText: '',
            loading: false,
            applying: false,
            error: '',
            requestSequence: previousSequence + 1,
        };
        this.modal('outlineFieldRewriteModal')?.show();
    },

    outlineFieldRewriteRequestIsCurrent(snapshot) {
        const state = this.creativeWorkspace.outlineFieldRewrite || {};
        return Boolean(
            Number(state.requestSequence || 0) === Number(snapshot.requestSequence || 0)
            && Number(state.projectId || 0) === Number(snapshot.projectId || 0)
            && String(state.batchId || '') === String(snapshot.batchId || '')
            && Number(state.episodeId || 0) === Number(snapshot.episodeId || 0)
            && String(state.fieldKey || '') === String(snapshot.fieldKey || '')
            && Number(this.creativeWorkspace.project?.id || 0) === Number(snapshot.projectId || 0)
            && String(this.latestOutlineBatchId() || '') === String(snapshot.batchId || '')
        );
    },

    async requestOutlineFieldRewrite() {
        const state = this.creativeWorkspace.outlineFieldRewrite;
        if (!state?.episodeId || !state.fieldKey || state.loading || state.applying) return null;
        const snapshot = {
            projectId: Number(state.projectId || 0),
            batchId: String(state.batchId || ''),
            episodeId: Number(state.episodeId || 0),
            fieldKey: String(state.fieldKey || ''),
            requestSequence: Number(state.requestSequence || 0) + 1,
        };
        state.requestSequence = snapshot.requestSequence;
        state.loading = true;
        state.error = '';
        try {
            const response = await aiworkerApi.request(
                `/api/creative/episodes/${snapshot.episodeId}/fields/${encodeURIComponent(snapshot.fieldKey)}/rewrite`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({instruction: String(state.instruction || '').trim()}),
                }
            );
            const data = await response.json();
            if (!this.outlineFieldRewriteRequestIsCurrent(snapshot)) return null;
            if (!response.ok) throw new Error(data.detail || '生成字段新稿失败');
            if (
                data.episode_id != null && Number(data.episode_id) !== snapshot.episodeId
                || data.project_id != null && Number(data.project_id) !== snapshot.projectId
                || data.batch_id != null && String(data.batch_id) !== snapshot.batchId
                || data.field != null && String(data.field) !== snapshot.fieldKey
            ) {
                throw new Error('字段新稿来源已变化，请重新打开');
            }
            state.sourceText = String(data.original_value ?? state.sourceText ?? '');
            state.sourceStatus = String(data.source_status || state.sourceStatus || '');
            state.sourceFingerprint = String(data.source_fingerprint || '');
            state.candidateText = String(data.candidate_value || '');
            return data;
        } catch (error) {
            if (this.outlineFieldRewriteRequestIsCurrent(snapshot)) {
                state.error = error.message || '生成字段新稿失败';
            }
            return null;
        } finally {
            if (this.outlineFieldRewriteRequestIsCurrent(snapshot)) {
                state.loading = false;
            }
        }
    },

    async applyOutlineFieldRewrite() {
        const state = this.creativeWorkspace.outlineFieldRewrite;
        const candidate = String(state?.candidateText || '').trim();
        if (
            !state?.episodeId || !state.fieldKey || !state.sourceFingerprint
            || !candidate || state.loading || state.applying
        ) return null;
        const snapshot = {
            projectId: Number(state.projectId || 0),
            batchId: String(state.batchId || ''),
            episodeId: Number(state.episodeId || 0),
            fieldKey: String(state.fieldKey || ''),
            requestSequence: Number(state.requestSequence || 0) + 1,
        };
        state.requestSequence = snapshot.requestSequence;
        state.applying = true;
        state.error = '';
        try {
            const operationId = globalThis.crypto?.randomUUID
                ? globalThis.crypto.randomUUID()
                : `outline-field-${Date.now()}-${Math.random().toString(16).slice(2)}`;
            const response = await aiworkerApi.request(
                `/api/creative/episodes/${snapshot.episodeId}/fields/${encodeURIComponent(snapshot.fieldKey)}/apply-rewrite`,
                {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        candidate_value: candidate,
                        source_fingerprint: state.sourceFingerprint,
                        expected_project_revision: Number(this.creativeWorkspace.project?.revision || 0),
                        expected_head_revision: this.isFreeCreativeProject?.()
                            ? Number(this.creativeHeadRevision?.('outline') || 0)
                            : undefined,
                        operation_id: operationId,
                    }),
                }
            );
            const data = await response.json();
            if (!this.outlineFieldRewriteRequestIsCurrent(snapshot)) return null;
            if (!response.ok) throw new Error(data.detail || '采用字段新稿失败');
            if (data.created_new_version) {
                await this.selectCreativeProject(snapshot.projectId);
            } else {
                const item = this.currentProjectOutlines().find(
                    candidateItem => Number(candidateItem.id) === snapshot.episodeId
                );
                if (item) {
                    item.content = item.content || {};
                    item.content[snapshot.fieldKey] = String(
                        data.episode?.content?.[snapshot.fieldKey] ?? candidate
                    );
                }
            }
            this.modal('outlineFieldRewriteModal')?.hide();
            this.resetOutlineFieldRewrite();
            this.showCreativeNotice(
                data.created_new_version ? '已基于锁定版本创建新草稿' : '已采用字段新稿'
            );
            return data;
        } catch (error) {
            if (this.outlineFieldRewriteRequestIsCurrent(snapshot)) {
                state.error = error.message || '采用字段新稿失败';
            }
            return null;
        } finally {
            if (this.outlineFieldRewriteRequestIsCurrent(snapshot)) {
                state.applying = false;
            }
        }
    },

    resetOutlineFieldRewrite() {
        const sequence = Number(
            this.creativeWorkspace.outlineFieldRewrite?.requestSequence || 0
        ) + 1;
        this.creativeWorkspace.outlineFieldRewrite = {
            projectId: 0,
            batchId: '',
            episodeId: 0,
            episodeNumber: 0,
            fieldKey: '',
            fieldLabel: '',
            sourceStatus: '',
            sourceText: '',
            sourceFingerprint: '',
            instruction: '',
            candidateText: '',
            loading: false,
            applying: false,
            error: '',
            requestSequence: sequence,
        };
    },

    resetOutlineEpisodeCards() {
        this.creativeWorkspace.outlineQuery = '';
        this.creativeWorkspace.expandedOutlineEpisodes = {};
        this.resetOutlineFieldRewrite();
        this.resetOutlineSeriesOverview();
    },

    syncOutlineEpisodeCardContext(project) {
        const overview = this.currentOutlineSeriesOverview();
        const outlineBatchId = typeof this.latestOutlineBatchId === 'function'
            ? this.latestOutlineBatchId()
            : project?.outline_batch_id;
        const contextKey = project
            ? JSON.stringify([
                project.id || '',
                project.locked_brief_id || '',
                project.character_batch_id || '',
                outlineBatchId || project.outline_batch_id || '',
                overview || null,
            ])
            : '';
        if (contextKey === String(this.creativeWorkspace.outlineCardContextKey || '')) return;
        this.creativeWorkspace.outlineCardContextKey = contextKey;
        this.resetOutlineEpisodeCards();
    },
}));

document.querySelector('[data-workspace-page="writing-outlines"]')?.setAttribute('data-ready', 'true');
