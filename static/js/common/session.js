(function () {
    'use strict';

    const permissionAlert = document.getElementById('permission-alert');
    const permissionMessage = document.getElementById('permission-alert-message');

    window.addEventListener('aiworker:permission-denied', (event) => {
        if (!permissionAlert) return;
        permissionMessage.textContent = event.detail?.message || '无权执行此操作';
        permissionAlert.classList.remove('d-none');
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
})();
