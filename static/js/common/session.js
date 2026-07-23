(function () {
    'use strict';

    const permissionAlert = document.getElementById('permission-alert');
    const permissionMessage = document.getElementById('permission-alert-message');

    function showToast(message, type) {
        if (!permissionAlert) return;
        permissionMessage.textContent = message;
        permissionAlert.className = 'alert position-fixed top-0 end-0 m-3 ' + (type === 'error' ? 'alert-danger' : 'alert-info');
        permissionAlert.classList.remove('d-none');
    }

    window.addEventListener('aiworker:permission-denied', (event) => {
        showToast(event.detail?.message || '无权执行此操作', 'error');
    });
    window.addEventListener('aiworker:toast', (event) => {
        showToast(event.detail?.message || '', event.detail?.type || 'info');
    });
    document.getElementById('permission-alert-dismiss')?.addEventListener('click', () => {
        permissionAlert.classList.add('d-none');
    });
    document.getElementById('logout-button')?.addEventListener('click', async () => {
        try {
            await window.aiworkerApi.request('/api/auth/logout', {method: 'POST'});
        } finally {
            window.location.assign('/login');
        }
    });

    window.AiWorkerToast = { show: showToast };
})();
