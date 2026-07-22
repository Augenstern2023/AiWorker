(function () {
    'use strict';

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('logout-button')?.addEventListener('click', async () => {
            try {
                await window.aiworkerApi.request('/api/auth/logout', {method: 'POST'});
            } finally {
                window.location.assign('/login');
            }
        });
    });
})();
