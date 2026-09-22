window.aiworkerModules = window.aiworkerModules || [];
window.aiworkerModules.push(() => ({
                promptStageLabel(stage) {
                    return {
                        brief: 'Brief', characters: '人物关系', outline: '全剧大纲',
                        beats: 'Beats', script: '场次剧本', review: '剧本审核',
                        optimization: '剧本优化'
                    }[stage] || stage || '';
                },

                promptWorkspaceUrl(kind) {
                    const projectId = this.creativeWorkspace.project?.id || '';
                    const state = this.creativeWorkspace.promptLibrary;
                    const path = 'prompt-templates';
                    const params = new URLSearchParams({stage: state.stage || 'brief'});
                    if (['beats', 'script'].includes(state.stage)) {
                        params.set('episode', String(Number(this.creativeWorkspace.selectedEpisode || 1)));
                    }
                    return `/projects/${projectId}/${path}?${params.toString()}`;
                },

                async initializePromptWorkspacePage() {
                    const params = new URLSearchParams(window.location.search);
                    const allowedStages = ['brief', 'characters', 'outline', 'beats', 'script'];
                    const requestedStage = String(params.get('stage') || 'brief');
                    const stage = allowedStages.includes(requestedStage) ? requestedStage : 'brief';
                    this.creativeWorkspace.promptLibrary.stage = stage;
                    if (['beats', 'script'].includes(stage)) {
                        const requestedEpisode = Number(params.get('episode') || 1);
                        const episodeCount = Number(this.creativeWorkspace.project?.target_episode_count || 1);
                        this.creativeWorkspace.selectedEpisode = Math.min(
                            Math.max(1, Number.isFinite(requestedEpisode) ? requestedEpisode : 1),
                            episodeCount
                        );
                    } else {
                        this.creativeWorkspace.selectedEpisode = 0;
                    }
                    await this.loadProjectPromptModule(stage);
                },

                updatePromptWorkspaceLocation() {
                    if (!this.page.startsWith('prompt-template-')) return;
                    const state = this.creativeWorkspace.promptLibrary;
                    const params = new URLSearchParams(window.location.search);
                    params.set('stage', state.stage || 'brief');
                    if (['beats', 'script'].includes(state.stage)) {
                        params.set('episode', String(Number(this.creativeWorkspace.selectedEpisode || 1)));
                    } else {
                        params.delete('episode');
                    }
                    window.history.replaceState(
                        window.history.state,
                        '',
                        `${window.location.pathname}?${params.toString()}${window.location.hash}`
                    );
                },

                async changePromptWorkspaceStage(stage) {
                    const allowedStages = ['brief', 'characters', 'outline', 'beats', 'script'];
                    const normalized = allowedStages.includes(stage) ? stage : 'brief';
                    const state = this.creativeWorkspace.promptLibrary;
                    state.stage = normalized;
                    if (['beats', 'script'].includes(normalized)) {
                        this.creativeWorkspace.selectedEpisode = Math.max(
                            1,
                            Number(this.creativeWorkspace.selectedEpisode || 1)
                        );
                    } else {
                        this.creativeWorkspace.selectedEpisode = 0;
                    }
                    this.updatePromptWorkspaceLocation();
                    await this.loadProjectPromptModule(normalized);
                },

                async changePromptWorkspaceEpisode(value) {
                    const episodeCount = Number(this.creativeWorkspace.project?.target_episode_count || 1);
                    this.creativeWorkspace.selectedEpisode = Math.min(
                        Math.max(1, Number(value || 1)),
                        episodeCount
                    );
                    this.updatePromptWorkspaceLocation();
                    await this.loadProjectPromptModule(this.creativeWorkspace.promptLibrary.stage);
                },

                resetPromptWorkspacePanels() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.personalOpen = false;
                    state.importOpen = false;
                    state.governanceOpen = false;
                    state.observabilityOpen = false;
                    state.marketplaceOpen = false;
                    state.editing = false;
                    state.deleteTemplate = null;
                    state.deleteImpact = null;
                    state.deleteReplacementTemplateId = '';
                    state.error = '';
                },

                async openPromptWorkspacePanel(panel) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const stage = state.stage || 'brief';
                    this.resetPromptWorkspacePanels();
                    this.promptLibraryModal = this.modal('promptLibraryModal');
                    this.promptLibraryModal?.show();
                    await this.loadProjectPromptModule(stage);
                    if (panel === 'personal') {
                        state.personalOpen = true;
                        return;
                    }
                    if (panel === 'project') return this.openPromptGovernance();
                    if (panel === 'marketplace') return this.openPromptMarketplace();
                    if (panel === 'observability') return this.openPromptObservability();
                },

                promptTemplatesByOwner(ownerScope) {
                    return this.orderedPromptTemplates().filter(
                        template => template.owner_scope === ownerScope
                    );
                },

                promptTemplateOwnerLabel(ownerScope) {
                    return {system: '系统模板', user: '我的模板', project: '项目模板'}[ownerScope]
                        || ownerScope || '-';
                },

                promptModeLabel(mode, stage = '') {
                    if (mode === 'single') return ['brief', 'characters', 'outline'].includes(stage) ? '单次生成' : '单集生成';
                    if (mode === 'series') return stage === 'brief' ? '多候选生成' : (stage === 'characters' ? '整批生成' : '批量生成');
                    return {rewrite: '局部重写', repair: '结构修复'}[mode] || mode;
                },

                promptStageForGenerationAction(action) {
                    return {
                        blueprints: 'brief', blueprint_variant: 'brief',
                        characters: 'characters',
                        outlines: 'outline', outline_overview: 'outline',
                        beats: 'beats', script: 'script'
                    }[action] || '';
                },

                promptTemplateDimensionValues(template) {
                    const dimensions = template?.content?.dimensions || {};
                    return [...new Set(Object.values(dimensions).map(value => String(value || '').trim()).filter(Boolean))];
                },

                promptRecommendation(template) {
                    const key = String(template?.template_key || '');
                    return (this.creativeWorkspace.promptLibrary.recommendations || [])
                        .find(item => item.template_key === key) || null;
                },

                orderedPromptTemplates() {
                    const templates = [...(this.creativeWorkspace.promptLibrary.templates || [])];
                    return templates.sort((left, right) => {
                        const leftScore = Number(this.promptRecommendation(left)?.score || 0);
                        const rightScore = Number(this.promptRecommendation(right)?.score || 0);
                        if (leftScore !== rightScore) return rightScore - leftScore;
                        return String(left.title || '').localeCompare(String(right.title || ''), 'zh-CN');
                    });
                },

                promptRecommendationLabel(template) {
                    const recommendation = this.promptRecommendation(template);
                    if (!recommendation) return '';
                    const score = Number(recommendation.score || 0);
                    if (score >= 60) return `高度匹配 ${score}`;
                    if (score >= 35) return `可能匹配 ${score}`;
                    return `信号较少 ${score}`;
                },

                selectedPromptTemplate() {
                    const state = this.creativeWorkspace.promptLibrary;
                    return (state.templates || []).find(item => item.template_key === state.selectedTemplateKey) || null;
                },

                promptPreviewStages(template = null) {
                    const stageRules = (template || this.selectedPromptTemplate())?.content?.stage_rules || {};
                    const stages = Object.keys(stageRules).filter(stage => {
                        const rules = stageRules[stage];
                        return rules && typeof rules === 'object' && Object.values(rules).some(Boolean);
                    });
                    const order = ['brief', 'characters', 'outline', 'beats', 'script', 'review', 'optimization'];
                    return stages.sort((left, right) => {
                        const leftIndex = order.indexOf(left);
                        const rightIndex = order.indexOf(right);
                        if (leftIndex === -1 && rightIndex === -1) return left.localeCompare(right, 'zh-CN');
                        if (leftIndex === -1) return 1;
                        if (rightIndex === -1) return -1;
                        return leftIndex - rightIndex;
                    });
                },

                resetPromptPreviewStage(template = null) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const stages = this.promptPreviewStages(template);
                    state.previewStage = stages.includes(state.stage) ? state.stage : (stages[0] || '');
                },

                selectPromptPreviewStage(stage) {
                    if (!this.promptPreviewStages().includes(stage)) return;
                    this.creativeWorkspace.promptLibrary.previewStage = stage;
                },

                selectPromptTemplate(template) {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.selectedTemplateKey = String(template?.template_key || '');
                    state.selectedTemplateRevision = Number(template?.revision || 1);
                    this.resetPromptPreviewStage(template);
                    state.editing = false;
                    state.editContent = {};
                },

                promptDimensionLabel(key) {
                    return {
                        format: '载体', genre: '题材', audience: '受众',
                        story_engine: '故事引擎', emotion: '情绪体验',
                        pacing: '节奏', expression: '表达风格'
                    }[key] || key;
                },

                promptParameterEntries(template = null) {
                    const parameters = (template || this.selectedPromptTemplate())?.content?.parameters;
                    if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') return [];
                    const metadata = {
                        max_beats: {
                            label: '节拍数量上限',
                            description: '单集最多生成多少个剧情节拍',
                            order: 10,
                        },
                        strong_ending_hook: {
                            label: '强化结尾钩子',
                            description: '让每集结尾保留明确的追看点',
                            order: 20,
                        },
                    };
                    const displayValue = value => {
                        if (typeof value === 'boolean') return value ? '开启' : '关闭';
                        if (value === null || value === undefined || value === '') return '未设置';
                        if (Array.isArray(value)) return value.length
                            ? value.map(item => String(item)).join('、') : '无';
                        if (typeof value === 'object') {
                            const count = Object.keys(value).length;
                            return count ? `已配置 ${count} 项` : '无';
                        }
                        return String(value);
                    };
                    return Object.entries(parameters).map(([key, value]) => {
                        const item = metadata[key];
                        const readableKey = String(key)
                            .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
                            .replace(/[_-]+/g, ' ')
                            .trim();
                        return {
                            key,
                            label: item?.label || `自定义设置：${readableKey || '未命名'}`,
                            description: item?.description || '模板作者提供的额外生成设置',
                            displayValue: displayValue(value),
                            isBoolean: typeof value === 'boolean',
                            enabled: value === true,
                            order: item?.order ?? 100,
                        };
                    }).sort((left, right) => left.order - right.order
                        || left.label.localeCompare(right.label, 'zh-CN'));
                },

                selectedPromptTemplateEditable() {
                    return ['user', 'project'].includes(this.selectedPromptTemplate()?.owner_scope);
                },

                startPromptTemplateEdit() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const template = this.selectedPromptTemplate();
                    if (!template || !this.selectedPromptTemplateEditable()) return;
                    state.editContent = this.cloneCreativeJson(template.content || {});
                    state.editParametersJson = JSON.stringify(state.editContent.parameters || {}, null, 2);
                    state.editExamplesJson = JSON.stringify(state.editContent.examples || [], null, 2);
                    state.editing = true;
                },

                async loadPromptRecommendations(stage) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !stage) return;
                    state.recommendationLoading = true;
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/recommendations?stage=${encodeURIComponent(stage)}`
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取模板推荐失败');
                        state.recommendations = Array.isArray(data.items) ? data.items : [];
                    } catch (_) {
                        state.recommendations = [];
                    } finally {
                        state.recommendationLoading = false;
                    }
                },

                async loadProjectPromptModule(stage) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !['brief', 'characters', 'outline', 'beats', 'script'].includes(stage)) return;
                    const episode = ['brief', 'characters', 'outline'].includes(stage)
                        ? 0 : Number(this.creativeWorkspace.selectedEpisode || 0);
                    state.loading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-modules/${stage}?episode_number=${Math.max(0, episode)}`
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取提示词模板失败');
                        state.settings = data.settings || {enabled: false};
                        state.templates = (Array.isArray(data.templates) ? data.templates : [])
                            .filter(template => !Array.isArray(template.supported_stages)
                                || template.supported_stages.includes(stage));
                        state.effective = data.effective || null;
                        const selectedKey = data.effective?.template_key || state.templates[0]?.template_key || '';
                        const selected = state.templates.find(item => item.template_key === selectedKey) || state.templates[0];
                        state.selectedTemplateKey = String(selected?.template_key || selectedKey);
                        state.selectedTemplateRevision = Number(selected?.revision || data.effective?.revision || 1);
                        this.resetPromptPreviewStage(selected);
                        const activeBinding = episode > 0 && data.episode_binding
                            ? data.episode_binding : data.stage_binding;
                        state.customRequirements = String(activeBinding?.override?.custom_requirements || '');
                        state.scope = episode > 0 && data.episode_binding ? 'episode' : 'stage';
                        await this.loadPromptRecommendations(stage);
                    } catch (error) {
                        state.error = error.message || '读取提示词模板失败';
                    } finally {
                        state.loading = false;
                    }
                },

                async openPromptLibrary(stage) {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.stage = stage;
                    state.error = '';
                    state.importOpen = false;
                    state.governanceOpen = false;
                    state.observabilityOpen = false;
                    state.marketplaceOpen = false;
                    state.personalOpen = false;
                    this.promptLibraryModal = this.modal('promptLibraryModal');
                    this.promptLibraryModal?.show();
                    await this.loadProjectPromptModule(stage);
                },

                promptGovernanceStatusLabel(status) {
                    return {
                        pending_review: '待审核', approved: '已批准',
                        deprecated: '已弃用', archived: '已归档'
                    }[status] || status || '-';
                },

                promptGovernanceActions(item) {
                    return {
                        pending_review: [
                            {key: 'approve', label: '批准当前版本', style: 'btn-success'},
                            {key: 'archive', label: '驳回并归档', style: 'btn-outline-danger'},
                        ],
                        approved: [
                            {key: 'request_review', label: '重新送审', style: 'btn-outline-primary'},
                            {key: 'deprecate', label: '标记弃用', style: 'btn-outline-warning'},
                        ],
                        deprecated: [
                            {key: 'restore', label: '恢复使用', style: 'btn-outline-success'},
                            {key: 'archive', label: '归档', style: 'btn-outline-danger'},
                        ],
                        archived: [
                            {key: 'restore', label: '恢复并送审', style: 'btn-outline-primary'},
                        ],
                    }[item?.governance_status] || [];
                },

                async loadPromptGovernance() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || state.governanceLoading) return;
                    state.governanceLoading = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/governance`
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取模板治理状态失败');
                        state.governanceItems = Array.isArray(data.items) ? data.items : [];
                        state.governancePolicy = Array.isArray(data.policy) ? data.policy : [];
                        state.governanceCanManage = Boolean(data.can_manage);
                    } catch (error) {
                        state.error = error.message || '读取模板治理状态失败';
                    } finally {
                        state.governanceLoading = false;
                    }
                },

                async openPromptGovernance() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.personalOpen = false;
                    state.governanceOpen = true;
                    state.importOpen = false;
                    state.observabilityOpen = false;
                    state.marketplaceOpen = false;
                    state.editing = false;
                    await this.loadPromptGovernance();
                },

                closePromptGovernance() {
                    this.creativeWorkspace.promptLibrary.governanceOpen = false;
                },

                promptMetricPercent(value) {
                    if (value === null || value === undefined) return '不可用';
                    return `${(Number(value) * 100).toFixed(1)}%`;
                },

                promptMetricNumber(value, digits = 0) {
                    if (value === null || value === undefined) return '不可用';
                    return Number(value).toLocaleString('zh-CN', {
                        minimumFractionDigits: digits,
                        maximumFractionDigits: digits,
                    });
                },

                async loadPromptObservability() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || state.observabilityLoading) return;
                    state.observabilityLoading = true;
                    state.error = '';
                    try {
                        const params = new URLSearchParams({limit: '500'});
                        if (state.observabilityStage) params.set('stage', state.observabilityStage);
                        if (state.observabilityTemplateKey) params.set('template_key', state.observabilityTemplateKey);
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/observability?${params.toString()}`
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '读取模板可观测性失败');
                        state.observabilityReport = data;
                    } catch (error) {
                        state.observabilityReport = null;
                        state.error = error.message || '读取模板可观测性失败';
                    } finally {
                        state.observabilityLoading = false;
                    }
                },

                async openPromptObservability() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.personalOpen = false;
                    state.observabilityOpen = true;
                    state.importOpen = false;
                    state.governanceOpen = false;
                    state.marketplaceOpen = false;
                    state.editing = false;
                    state.observabilityStage = state.stage || '';
                    state.observabilityTemplateKey = '';
                    await this.loadPromptObservability();
                },

                closePromptObservability() {
                    this.creativeWorkspace.promptLibrary.observabilityOpen = false;
                },

                promptMarketplaceStatusLabel(status) {
                    return {
                        pending: '待平台审核', approved: '已上架', rejected: '已驳回',
                        withdrawn: '作者已撤回', removed: '平台已下架'
                    }[status] || status || '-';
                },

                promptMarketplaceError(data, fallback) {
                    const detail = data?.detail;
                    if (typeof detail === 'string') return detail;
                    return detail?.message || fallback;
                },

                promptMarketplaceDefaultPreviewStage(item) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const stages = this.promptPreviewStages(item);
                    return [state.marketplaceStage, state.stage]
                        .find(stage => stages.includes(stage)) || stages[0] || '';
                },

                async loadPromptMarketplace() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || state.marketplaceLoading) return;
                    state.marketplaceLoading = true;
                    state.error = '';
                    try {
                        const params = new URLSearchParams({limit: '200'});
                        if (String(state.marketplaceQuery || '').trim()) {
                            params.set('q', String(state.marketplaceQuery).trim());
                        }
                        if (state.marketplaceStage) params.set('stage', state.marketplaceStage);
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-marketplace?${params.toString()}`
                        );
                        const data = await response.json();
                        if (!response.ok) {
                            throw new Error(this.promptMarketplaceError(data, '读取模板市场失败'));
                        }
                        state.marketplaceOfficialItems = Array.isArray(data.official_items)
                            ? data.official_items : [];
                        state.marketplaceItems = Array.isArray(data.items) ? data.items : [];
                        state.marketplaceOwnSubmissions = Array.isArray(data.own_submissions)
                            ? data.own_submissions : [];
                        state.marketplaceModerationQueue = Array.isArray(data.moderation_queue)
                            ? data.moderation_queue : [];
                        state.marketplaceSourceTypes = Array.isArray(data.source_types)
                            ? data.source_types : [];
                        state.marketplacePolicy = Array.isArray(data.policy) ? data.policy : [];
                        state.marketplaceCanReview = Boolean(data.can_review);
                        state.marketplaceCanInstall = Boolean(data.can_install);
                    } catch (error) {
                        state.error = error.message || '读取模板市场失败';
                    } finally {
                        state.marketplaceLoading = false;
                    }
                },

                async openPromptMarketplace() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.personalOpen = false;
                    const personalTemplates = this.promptTemplatesByOwner('user');
                    if (this.selectedPromptTemplate()?.owner_scope !== 'user' && personalTemplates.length) {
                        this.selectPromptTemplate(personalTemplates[0]);
                    }
                    state.marketplaceOpen = true;
                    state.importOpen = false;
                    state.governanceOpen = false;
                    state.observabilityOpen = false;
                    state.editing = false;
                    state.marketplaceStage = state.stage || '';
                    await this.loadPromptMarketplace();
                },

                closePromptMarketplace() {
                    this.creativeWorkspace.promptLibrary.marketplaceOpen = false;
                },

                promptMarketplaceOfficialTemplate(item) {
                    const key = String(item?.template_key || '');
                    return (this.creativeWorkspace.promptLibrary.templates || []).find(
                        template => template.owner_scope === 'system'
                            && String(template.template_key || '') === key
                    ) || null;
                },

                useOfficialPromptMarketplaceTemplate(item) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const template = this.promptMarketplaceOfficialTemplate(item);
                    if (!template || !state.stage) {
                        state.error = '当前创作步骤无法使用这个官方模板';
                        return;
                    }
                    this.selectPromptTemplate(template);
                    this.storePromptRunSelection(state.stage, template, '');
                    state.marketplaceOpen = false;
                    this.promptLibraryModal?.hide();
                    this.showCreativeNotice(
                        `${template.title}将在下次${this.promptStageLabel(state.stage)}生成中使用，不会修改默认设置`
                    );
                },

                clearPromptMarketplaceFieldError(field) {
                    const state = this.creativeWorkspace.promptLibrary;
                    if (!state.marketplaceFieldErrors?.[field]) return;
                    const errors = {...state.marketplaceFieldErrors};
                    delete errors[field];
                    state.marketplaceFieldErrors = errors;
                },

                async submitSelectedPromptToMarketplace() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const template = this.selectedPromptTemplate();
                    if (state.marketplaceSaving) return;
                    if (!projectId) {
                        state.error = '项目未加载，请刷新页面后重试';
                        return;
                    }
                    state.error = '';
                    const errors = {};
                    const fields = {
                        template: 'promptMarketplacePersonalTemplate',
                        attribution: 'promptMarketplaceAttribution',
                        tags: 'promptMarketplaceTags',
                        sourceType: 'promptMarketplaceSourceType',
                        sourceUrl: 'promptMarketplaceSourceUrl',
                        rights: 'promptMarketplaceRights',
                        publication: 'promptMarketplacePublicConfirm',
                        rightsConfirmed: 'promptMarketplaceRightsConfirm',
                    };
                    if (!template || template.owner_scope !== 'user') {
                        errors.template = '请先把模板“另存到我的模板”，再选择个人模板提交';
                    } else if (!String(template.title || '').trim()) {
                        errors.template = '个人模板名称未填写，请到“我的模板”补充名称';
                    }
                    const attribution = String(state.marketplaceAttributionName || '').trim();
                    if (!attribution) errors.attribution = '模板署名未填写';
                    const tags = String(state.marketplaceTags || '').split(/[,，\n]/)
                        .map(item => item.trim()).filter(Boolean);
                    if (tags.length > 10) errors.tags = '标签最多填写 10 个，请删减';
                    if (!(state.marketplaceSourceTypes || []).some(
                        item => item.code === state.marketplaceSourceType
                    )) errors.sourceType = '来源类型未选择，请选择模板来源';
                    const sourceUrl = state.marketplaceSourceType === 'original'
                        ? '' : String(state.marketplaceSourceUrl || '').trim();
                    if (state.marketplaceSourceType !== 'original' && !sourceUrl) {
                        errors.sourceUrl = '非原创模板的可核验来源链接未填写';
                    } else if (sourceUrl) {
                        try {
                            const parsed = new URL(sourceUrl);
                            if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.host) {
                                errors.sourceUrl = '来源链接需填写有效的 HTTP 或 HTTPS 地址';
                            }
                        } catch (_) {
                            errors.sourceUrl = '来源链接需填写有效的 HTTP 或 HTTPS 地址';
                        }
                    }
                    const rights = String(state.marketplaceRightsStatement || '').trim();
                    if (!rights) errors.rights = '来源与权利说明未填写';
                    else if (rights.length < 10) {
                        errors.rights = `来源与权利说明至少填写 10 字（当前 ${rights.length} 字）`;
                    }
                    if (!state.marketplacePublicationConfirmed) {
                        errors.publication = '请确认模板会在公司内部展示并可被安装';
                    }
                    if (!state.marketplaceRightsConfirmed) {
                        errors.rightsConfirmed = '请确认拥有发布和内部使用所需权利';
                    }
                    state.marketplaceFieldErrors = errors;
                    if (Object.keys(errors).length) {
                        const first = Object.keys(errors)[0];
                        setTimeout(() => {
                            const target = document.getElementById(fields[first]);
                            (target?.offsetParent ? target : document.getElementById('promptMarketplaceSubmit'))?.focus();
                        }, 0);
                        return;
                    }
                    state.marketplaceSaving = true;
                    state.error = '';
                    try {
                        const operationId = globalThis.crypto?.randomUUID?.()
                            || `prompt-market-submit-${Date.now()}`;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}/marketplace-submissions`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    operation_id: operationId,
                                    expected_template_revision: Number(template.revision || 1),
                                    title: String(template.title || '').trim(),
                                    description: String(template.description || '').trim(),
                                    tags,
                                    attribution_name: attribution,
                                    source_type: state.marketplaceSourceType,
                                    source_url: sourceUrl,
                                    rights_statement: rights,
                                    publication_confirmed: true,
                                    rights_confirmed: true,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) {
                            throw new Error(this.promptMarketplaceError(data, '提交市场审核失败'));
                        }
                        state.marketplacePublicationConfirmed = false;
                        state.marketplaceRightsConfirmed = false;
                        await this.loadPromptMarketplace();
                        this.showCreativeNotice('模板冻结版本已提交审核；通过前不会在模板中心展示');
                    } catch (error) {
                        state.error = error.message || '提交市场审核失败';
                    } finally {
                        state.marketplaceSaving = false;
                    }
                },

                async reviewPromptMarketplaceSubmission(item, action) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !item?.id || state.marketplaceSaving) return;
                    const label = {
                        approve: '批准上架', reject: '驳回', remove: '下架', withdraw: '撤回'
                    }[action] || action;
                    const note = window.prompt(`请填写“${label}”原因`);
                    if (!String(note || '').trim()) return;
                    if (['remove', 'withdraw'].includes(action)
                        && !window.confirm(`${label}只影响后续安装，现有项目副本将保留。确认继续？`)) return;
                    state.marketplaceSaving = true;
                    state.error = '';
                    try {
                        const operationId = globalThis.crypto?.randomUUID?.()
                            || `prompt-market-review-${Date.now()}`;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-marketplace/submissions/${item.id}/review`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    operation_id: operationId,
                                    action,
                                    expected_review_revision: Number(item.review_revision || 0),
                                    note: String(note).trim(),
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) {
                            throw new Error(this.promptMarketplaceError(data, '市场状态更新失败'));
                        }
                        await this.loadPromptMarketplace();
                        this.showCreativeNotice(`${item.title}：${label}已记录`);
                    } catch (error) {
                        state.error = error.message || '市场状态更新失败';
                    } finally {
                        state.marketplaceSaving = false;
                    }
                },

                async installPromptMarketplaceListing(item) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !item?.id || state.marketplaceSaving || item.installation) return;
                    if (!state.marketplaceCanInstall) {
                        state.error = '只有项目负责人可以安装市场模板';
                        return;
                    }
                    if (!window.confirm(
                        `确认将“${item.title}”安装为当前项目的独立副本？`
                    )) return;
                    state.marketplaceSaving = true;
                    state.error = '';
                    try {
                        const operationId = globalThis.crypto?.randomUUID?.()
                            || `prompt-market-install-${Date.now()}`;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-marketplace/listings/${item.id}/install`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    operation_id: operationId,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) {
                            throw new Error(this.promptMarketplaceError(data, '安装市场模板失败'));
                        }
                        await Promise.all([
                            this.loadPromptMarketplace(),
                            this.loadProjectPromptModule(state.stage),
                        ]);
                        this.showCreativeNotice('模板已作为项目独立副本安装，尚未自动绑定');
                    } catch (error) {
                        state.error = error.message || '安装市场模板失败';
                    } finally {
                        state.marketplaceSaving = false;
                    }
                },

                async ratePromptMarketplaceListing(item) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !item?.id || !item.installation || item.is_owner
                        || state.marketplaceSaving) return;
                    const existingScore = Number(item.my_rating?.score || 5);
                    const rawScore = window.prompt('请评分（1–5 分）', String(existingScore));
                    if (rawScore === null) return;
                    const score = Number(rawScore);
                    if (!Number.isInteger(score) || score < 1 || score > 5) {
                        state.error = '评分必须是 1 到 5 的整数';
                        return;
                    }
                    const review = window.prompt(
                        '可选：写下实际使用依据', String(item.my_rating?.review_text || '')
                    );
                    if (review === null) return;
                    state.marketplaceSaving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-marketplace/listings/${item.id}/rating`,
                            {
                                method: 'PUT',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    score,
                                    review_text: String(review || '').trim(),
                                    expected_revision: Number(item.my_rating?.revision || 0),
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) {
                            throw new Error(this.promptMarketplaceError(data, '保存市场评分失败'));
                        }
                        await this.loadPromptMarketplace();
                        this.showCreativeNotice('已保存一人一票的安装用户评分');
                    } catch (error) {
                        state.error = error.message || '保存市场评分失败';
                    } finally {
                        state.marketplaceSaving = false;
                    }
                },

                async governPromptTemplate(item, action) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !item?.id || state.governanceSaving) return;
                    if (action === 'archive' && !item.impact?.can_archive) {
                        state.error = item.impact?.archive_blockers?.[0] || '模板仍被使用，不能归档';
                        return;
                    }
                    const actionLabel = this.promptGovernanceActions(item)
                        .find(value => value.key === action)?.label || action;
                    const note = window.prompt(`请填写“${actionLabel}”的原因`);
                    if (!String(note || '').trim()) return;
                    if (['deprecate', 'archive'].includes(action)
                        && !window.confirm(`${actionLabel}不会自动修改现有绑定，确认继续？`)) return;
                    state.governanceSaving = true;
                    state.error = '';
                    try {
                        const operationId = globalThis.crypto?.randomUUID?.()
                            || `prompt-governance-${Date.now()}`;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${item.id}/governance`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    operation_id: operationId,
                                    action,
                                    expected_governance_revision: Number(item.governance_revision || 0),
                                    note: String(note).trim(),
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '模板治理操作失败');
                        await Promise.all([
                            this.loadPromptGovernance(),
                            this.loadProjectPromptModule(state.stage),
                        ]);
                        this.showCreativeNotice(`${item.title}：${actionLabel}已记录`);
                    } catch (error) {
                        state.error = error.message || '模板治理操作失败';
                    } finally {
                        state.governanceSaving = false;
                    }
                },

                promptRunSelectionStorageKey(stage) {
                    const projectId = this.creativeWorkspace.project?.id || '';
                    return `aiworker:prompt-next:${projectId}:${stage}`;
                },

                storePromptRunSelection(stage, template, customRequirements = '') {
                    const selection = {
                        templateKey: String(template?.template_key || ''),
                        revision: Number(template?.revision || 1),
                        customRequirements: String(customRequirements || '').trim(),
                    };
                    this.creativeWorkspace.promptLibrary.runSelections[stage] = selection;
                    try {
                        window.sessionStorage?.setItem(
                            this.promptRunSelectionStorageKey(stage), JSON.stringify(selection)
                        );
                    } catch (_) {}
                    return selection;
                },

                clearPromptRunSelection(stage) {
                    delete this.creativeWorkspace.promptLibrary.runSelections[stage];
                    try {
                        window.sessionStorage?.removeItem(this.promptRunSelectionStorageKey(stage));
                    } catch (_) {}
                },

                promptRunSelection(stage) {
                    const state = this.creativeWorkspace.promptLibrary;
                    if (state?.runSelections?.[stage]) return state.runSelections[stage];
                    try {
                        const stored = JSON.parse(
                            window.sessionStorage?.getItem(this.promptRunSelectionStorageKey(stage)) || 'null'
                        );
                        if (stored?.templateKey && Number(stored?.revision || 0) > 0) {
                            state.runSelections[stage] = stored;
                            return stored;
                        }
                    } catch (_) {}
                    return null;
                },

                promptGenerationPayload(stage, body = {}) {
                    const selection = this.promptRunSelection(stage);
                    if (!selection) return body;
                    return {
                        ...body,
                        prompt_template_key: selection.templateKey,
                        prompt_template_revision: selection.revision,
                        prompt_override: {custom_requirements: selection.customRequirements || ''},
                    };
                },

                usePromptForNextGeneration() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const template = this.selectedPromptTemplate();
                    if (!template || !state.stage) return;
                    this.storePromptRunSelection(
                        state.stage, template, state.customRequirements
                    );
                    this.promptLibraryModal?.hide();
                    this.showCreativeNotice(`${template.title}将在下次${this.promptStageLabel(state.stage)}生成中使用，已有内容不变`);
                },

                async savePromptBinding() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const template = this.selectedPromptTemplate();
                    if (!projectId || !template || state.saving) return;
                    const episodeNumber = ['beats', 'script'].includes(state.stage) && state.scope === 'episode'
                        ? Number(this.creativeWorkspace.selectedEpisode || 0) : 0;
                    const defaultScope = episodeNumber > 0
                        ? `第 ${episodeNumber} 集${this.promptStageLabel(state.stage)}默认模板`
                        : `${this.promptStageLabel(state.stage)}阶段默认模板`;
                    if (!state.settings?.enabled && !window.confirm(
                        `保存后将启用模板设置，并把“${template.title}”设为${defaultScope}。只影响之后新生成的内容，不会修改已有内容。是否继续？`
                    )) return;
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-modules/${state.stage}`,
                            {
                                method: 'PUT',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    template_key: template.template_key,
                                    template_revision: Number(template.revision || 1),
                                    episode_number: episodeNumber,
                                    override: {custom_requirements: String(state.customRequirements || '').trim()},
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '保存提示词模板失败');
                        state.settings = data.settings || state.settings;
                        state.effective = data.effective || state.effective;
                        this.promptLibraryModal?.hide();
                        this.showCreativeNotice('提示词模板默认设置已保存，只影响之后新生成的内容');
                    } catch (error) {
                        state.error = error.message || '保存提示词模板失败';
                    } finally {
                        state.saving = false;
                    }
                },

                async copySelectedPromptTemplate(ownerScope = 'user') {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const template = this.selectedPromptTemplate();
                    if (!projectId || !template || state.saving) return;
                    const suggested = `${template.title}·自定义`;
                    const title = window.prompt('请输入新模板名称', suggested);
                    if (!String(title || '').trim()) return;
                    const content = this.cloneCreativeJson(template.content || {});
                    const custom = String(state.customRequirements || '').trim();
                    if (custom) content.shared_dna = `${String(content.shared_dna || '').trim()}\n${custom}`.trim();
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    title: String(title).trim(),
                                    description: `基于“${template.title}”创建`,
                                    owner_scope: ownerScope,
                                    content,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '创建模板失败');
                        await this.loadProjectPromptModule(state.stage);
                        state.selectedTemplateKey = data.template_key;
                        state.selectedTemplateRevision = Number(data.revision || 1);
                        if (ownerScope === 'project' && state.governanceOpen) {
                            await this.loadPromptGovernance();
                        }
                        this.showCreativeNotice(ownerScope === 'project' ? '项目模板已创建' : '个人模板已创建');
                    } catch (error) {
                        state.error = error.message || '创建模板失败';
                    } finally {
                        state.saving = false;
                    }
                },

                async deletePersonalPromptTemplate(template) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !template?.id || state.deletingTemplateId) return;
                    state.deletingTemplateId = String(template.id);
                    state.deleteTemplate = null;
                    state.deleteImpact = null;
                    state.deleteReplacementTemplateId = '';
                    state.error = '';
                    try {
                        const impactResponse = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}/deletion-impact`
                        );
                        const impact = await impactResponse.json();
                        if (!impactResponse.ok) {
                            throw new Error(impact.detail || '读取模板使用情况失败');
                        }
                        if (!impact.can_delete) {
                            state.deleteTemplate = this.cloneCreativeJson(template);
                            state.deleteImpact = impact;
                            state.deleteReplacementTemplateId = String(
                                impact.replacement_candidates?.[0]?.id || ''
                            );
                            setTimeout(() => {
                                document.getElementById('prompt-delete-rebind-panel')
                                    ?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
                                document.getElementById('prompt-delete-replacement')?.focus();
                            }, 0);
                            return;
                        }
                        const marketNote = Number(impact.market_submission_count || 0) > 0
                            ? `\n已有 ${impact.market_submission_count} 条市场申请或上架快照会继续保留。`
                            : '';
                        if (!window.confirm(
                            `确定删除“${template.title}”吗？\n删除后会从“我的模板”和选择列表中隐藏，历史生成记录仍会保留。${marketNote}\n此操作不能在界面中恢复。`
                        )) return;
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}`,
                            {
                                method: 'DELETE',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    expected_revision: Number(template.revision || 1),
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '删除模板失败');
                        await this.loadProjectPromptModule(state.stage);
                        this.clearPersonalPromptDeletePlan();
                        this.showCreativeNotice('个人模板已删除，历史记录仍保留');
                    } catch (error) {
                        state.error = error.message || '删除模板失败';
                    } finally {
                        state.deletingTemplateId = '';
                    }
                },

                clearPersonalPromptDeletePlan() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.deleteTemplate = null;
                    state.deleteImpact = null;
                    state.deleteReplacementTemplateId = '';
                    state.error = '';
                },

                selectedPromptDeleteReplacement() {
                    const state = this.creativeWorkspace.promptLibrary;
                    return (state.deleteImpact?.replacement_candidates || []).find(
                        item => String(item.id) === String(state.deleteReplacementTemplateId)
                    ) || null;
                },

                promptDeleteBindingLabel(binding) {
                    const stage = this.promptStageLabel(binding?.stage);
                    const episode = Number(binding?.episode_number || 0);
                    return episode > 0 ? `${stage} · 第 ${episode} 集` : `${stage}阶段默认`;
                },

                promptDeleteProjectUrl(project, stages = []) {
                    const stage = ['brief', 'characters', 'outline', 'beats', 'script']
                        .find(item => (stages || []).includes(item)) || 'brief';
                    return `/projects/${Number(project?.project_id || 0)}/prompt-templates?stage=${encodeURIComponent(stage)}`;
                },

                async rebindAndDeletePersonalPromptTemplate() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const template = state.deleteTemplate;
                    const replacement = this.selectedPromptDeleteReplacement();
                    const impact = state.deleteImpact;
                    if (!projectId || !template?.id || !replacement || state.deletingTemplateId) return;
                    const referenceCount = Number(impact?.current_project_binding_count || 0);
                    if (!referenceCount) {
                        state.error = '当前项目没有可改绑的引用';
                        return;
                    }
                    const willDelete = !(impact?.other_project_references || []).length;
                    const resultLabel = willDelete
                        ? '删除这个个人模板'
                        : '仅完成当前项目改绑';
                    if (!window.confirm(
                        `确认将当前项目的 ${referenceCount} 处引用改绑到“${replacement.title}”，并${resultLabel}吗？\n只影响之后的生成，已有内容与历史记录不变。`
                    )) return;
                    state.deletingTemplateId = String(template.id);
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}/rebind-and-delete`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    expected_revision: Number(template.revision || 1),
                                    replacement_template_id: replacement.id,
                                    replacement_template_revision: Number(replacement.revision || 1),
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '改绑失败');
                        await this.loadProjectPromptModule(state.stage);
                        if (data.deleted) {
                            this.clearPersonalPromptDeletePlan();
                            this.showCreativeNotice('已改绑当前项目并删除个人模板，历史记录仍保留');
                            return;
                        }
                        const impactResponse = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}/deletion-impact`
                        );
                        const refreshedImpact = await impactResponse.json();
                        if (!impactResponse.ok) {
                            throw new Error(refreshedImpact.detail || '读取剩余引用失败');
                        }
                        state.deleteImpact = refreshedImpact;
                        state.deleteReplacementTemplateId = String(
                            refreshedImpact.replacement_candidates?.[0]?.id || ''
                        );
                        this.showCreativeNotice(
                            `当前项目已改绑；仍有 ${Number(data.remaining_binding_count || 0)} 处其他项目引用待处理`
                        );
                    } catch (error) {
                        state.error = error.message || '改绑并删除模板失败';
                    } finally {
                        state.deletingTemplateId = '';
                    }
                },

                openPromptImport() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.personalOpen = false;
                    state.importOpen = true;
                    state.governanceOpen = false;
                    state.observabilityOpen = false;
                    state.marketplaceOpen = false;
                    state.importDraft = null;
                    state.importWarnings = [];
                    state.importSourceSummary = {};
                    state.error = '';
                },

                closePromptImport() {
                    const state = this.creativeWorkspace.promptLibrary;
                    state.importOpen = false;
                    if (this.page === 'prompt-template-center') state.personalOpen = true;
                    state.importDraft = null;
                    state.importWarnings = [];
                    state.importSourceSummary = {};
                },

                async loadPromptImportFile(event) {
                    const state = this.creativeWorkspace.promptLibrary;
                    const file = event?.target?.files?.[0];
                    if (!file) return;
                    const name = String(file.name || '').toLowerCase();
                    if (!name.endsWith('.md') && !name.endsWith('.txt')) {
                        state.error = '只支持 .md 或 .txt 文件';
                        event.target.value = '';
                        return;
                    }
                    try {
                        const text = await file.text();
                        if (text.length > 50000) throw new Error('外部提示词不能超过 50000 字符');
                        state.importSourceText = text;
                        state.error = '';
                    } catch (error) {
                        state.error = error.message || '读取提示词文件失败';
                    } finally {
                        event.target.value = '';
                    }
                },

                async previewPromptImport() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const sourceText = String(state.importSourceText || '').trim();
                    if (!projectId || state.importLoading) return;
                    if (!sourceText) {
                        state.error = '请粘贴外部提示词或选择文件';
                        return;
                    }
                    state.importLoading = true;
                    state.error = '';
                    state.importDraft = null;
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/import-preview`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    source_text: sourceText,
                                    target_stage: state.stage || null,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '整理外部提示词失败');
                        state.importDraft = this.cloneCreativeJson(data.draft || null, null);
                        state.importWarnings = Array.isArray(data.warnings) ? data.warnings : [];
                        state.importSourceSummary = data.source_summary || {};
                        state.importTitle = String(data.draft?.title || '');
                        state.importDescription = String(data.draft?.description || '');
                        state.importParametersJson = JSON.stringify(data.draft?.content?.parameters || {}, null, 2);
                        state.importExamplesJson = JSON.stringify(data.draft?.content?.examples || [], null, 2);
                    } catch (error) {
                        state.error = error.message || '整理外部提示词失败';
                    } finally {
                        state.importLoading = false;
                    }
                },

                async saveImportedPromptTemplate() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    if (!projectId || !state.importDraft || state.saving) return;
                    const title = String(state.importTitle || '').trim();
                    if (!title) {
                        state.error = '模板名称不能为空';
                        return;
                    }
                    let content;
                    try {
                        const parameters = JSON.parse(String(state.importParametersJson || '{}'));
                        const examples = JSON.parse(String(state.importExamplesJson || '[]'));
                        if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') {
                            throw new Error('模板参数必须是 JSON 对象');
                        }
                        if (!Array.isArray(examples)) throw new Error('模板示例必须是 JSON 数组');
                        content = this.cloneCreativeJson(state.importDraft.content || {});
                        content.parameters = parameters;
                        content.examples = examples.map(example => ({
                            ...example,
                            include_in_context: Boolean(example?.include_in_context),
                        }));
                    } catch (error) {
                        state.error = error.message || '导入草稿 JSON 格式不正确';
                        return;
                    }
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    title,
                                    description: String(state.importDescription || '').trim(),
                                    owner_scope: state.importOwnerScope,
                                    content,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '保存导入模板失败');
                        this.closePromptImport();
                        await this.loadProjectPromptModule(state.stage);
                        state.selectedTemplateKey = data.template_key;
                        state.selectedTemplateRevision = Number(data.revision || 1);
                        this.showCreativeNotice('导入草稿已保存为模板；尚未绑定或启用');
                    } catch (error) {
                        state.error = error.message || '保存导入模板失败';
                    } finally {
                        state.saving = false;
                    }
                },

                async savePromptTemplateRevision() {
                    const state = this.creativeWorkspace.promptLibrary;
                    const projectId = this.creativeWorkspace.project?.id;
                    const template = this.selectedPromptTemplate();
                    if (!projectId || !template || !state.editing || state.saving) return;
                    let content;
                    try {
                        const parameters = JSON.parse(String(state.editParametersJson || '{}'));
                        const examples = JSON.parse(String(state.editExamplesJson || '[]'));
                        if (!parameters || Array.isArray(parameters) || typeof parameters !== 'object') {
                            throw new Error('模板参数必须是 JSON 对象');
                        }
                        if (!Array.isArray(examples)) throw new Error('模板示例必须是 JSON 数组');
                        content = this.cloneCreativeJson(state.editContent || {});
                        content.parameters = parameters;
                        content.examples = examples;
                    } catch (error) {
                        state.error = error.message || '高级模板 JSON 格式不正确';
                        return;
                    }
                    state.saving = true;
                    state.error = '';
                    try {
                        const response = await aiworkerApi.request(
                            `/api/creative/projects/${projectId}/prompt-library/templates/${template.id}/revisions`,
                            {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json'},
                                body: JSON.stringify({
                                    expected_revision: Number(template.revision || 1),
                                    content,
                                }),
                            }
                        );
                        const data = await response.json();
                        if (!response.ok) throw new Error(data.detail || '保存模板版本失败');
                        const selectedKey = template.template_key;
                        await this.loadProjectPromptModule(state.stage);
                        const updated = state.templates.find(item => item.template_key === selectedKey);
                        if (updated) this.selectPromptTemplate(updated);
                        if (template.owner_scope === 'project') {
                            state.editing = false;
                            state.governanceOpen = true;
                            await this.loadPromptGovernance();
                            this.showCreativeNotice(`项目模板 v${data.revision} 已保存并进入待审核`);
                        } else {
                            this.showCreativeNotice(`模板新版本 v${data.revision} 已保存`);
                        }
                    } catch (error) {
                        state.error = error.message || '保存模板版本失败';
                    } finally {
                        state.saving = false;
                    }
                },
}));
