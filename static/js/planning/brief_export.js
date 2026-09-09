(function () {
    'use strict';

    const button = document.getElementById('brief-production-export');
    if (!button || !window.aiworkerProductionExport) return;
    button.addEventListener('click', () => {
        window.aiworkerProductionExport.open(button.dataset.projectId, {trigger: button});
    });
})();
