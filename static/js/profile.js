(function () {
    'use strict';

    const summary = document.getElementById('profile-summary');
    if (!summary) return;

    const form = document.getElementById('profile-form');
    const email = document.getElementById('profile-email');
    const phone = document.getElementById('profile-phone');
    const gender = document.getElementById('profile-gender');
    const submit = document.getElementById('profile-submit');
    const saveIcon = document.getElementById('profile-save-icon');
    const saving = document.getElementById('profile-saving');
    const submitLabel = document.getElementById('profile-submit-label');
    const feedback = document.getElementById('profile-feedback');
    const feedbackIcon = document.getElementById('profile-feedback-icon');
    const feedbackMessage = document.getElementById('profile-feedback-message');
    const feedbackToast = bootstrap.Toast.getOrCreateInstance(feedback, {delay: 4200});

    function value(item) {
        return item === null || item === undefined || item === '' ? '-' : String(item);
    }

    function formatDate(item) {
        return value(item)
            .replace('T', ' ')
            .replace(/\.\d+(?=[+-]\d\d:\d\d$)/, '')
            .replace(/[+-]\d\d:\d\d$/, '');
    }

    function showFeedback(message, type = 'success') {
        const isSuccess = type === 'success';
        feedback.classList.remove('text-bg-success', 'text-bg-danger');
        feedback.classList.add(isSuccess ? 'text-bg-success' : 'text-bg-danger');
        feedbackIcon.className = `bi ${isSuccess ? 'bi-check-circle-fill' : 'bi-exclamation-triangle-fill'}`;
        feedbackMessage.textContent = message;
        feedbackToast.show();
    }

    function setSaving(isSaving) {
        submit.disabled = isSaving;
        saving.classList.toggle('d-none', !isSaving);
        saveIcon.classList.toggle('d-none', isSaving);
        submitLabel.textContent = isSaving ? '正在保存' : '保存修改';
    }

    function render(profile) {
        const entries = [
            ['用户名', profile.username],
            ['显示名称', profile.display_name],
            ['部门归属', (profile.departments || []).map(department => `${department.name}${department.is_primary ? '（主部门）' : ''}`).join('、') || '-'],
            ['角色', (profile.role_codes || []).join('、') || '-'],
            ['创建时间', formatDate(profile.created_at)],
            ['状态', profile.status === 'active' ? '启用' : '停用'],
        ];
        summary.replaceChildren(...entries.flatMap(([label, item]) => {
            const term = document.createElement('dt');
            term.textContent = label;
            const definition = document.createElement('dd');
            definition.textContent = value(item);
            return [term, definition];
        }));
        email.value = profile.email || '';
        phone.value = profile.phone || '';
        gender.value = profile.gender || 'unspecified';
    }

    async function loadProfile({notifyOnFailure = true} = {}) {
        try {
            const response = await window.aiworkerApi.request('/api/profile');
            if (!response.ok) throw new Error('个人信息加载失败');
            render(await response.json());
        } catch (exception) {
            summary.replaceChildren();
            const term = document.createElement('dt');
            term.textContent = '账号信息';
            const definition = document.createElement('dd');
            definition.textContent = '暂时无法加载';
            summary.append(term, definition);
            if (notifyOnFailure) showFeedback(exception.message || '个人信息加载失败', 'danger');
        }
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        setSaving(true);
        try {
            const response = await window.aiworkerApi.request('/api/profile', {
                method: 'PATCH',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    email: email.value.trim() || null,
                    phone: phone.value.trim() || null,
                    gender: gender.value,
                }),
            });
            if (!response.ok) {
                const data = await response.json().catch(() => ({}));
                throw new Error(data.detail || '个人信息保存失败');
            }
            render(await response.json());
            showFeedback('保存成功，个人资料已更新');
        } catch (exception) {
            showFeedback(exception.message || '个人信息保存失败', 'danger');
        } finally {
            setSaving(false);
        }
    });

    document.getElementById('profile-refresh')?.addEventListener('click', () => loadProfile());
    loadProfile({notifyOnFailure: false});
})();
