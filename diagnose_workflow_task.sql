-- 诊断工作流任务权限问题
-- 请将 {project_id} 替换为"傻女重生记"项目的实际 ID
-- 请将 {user_id} 替换为 bianju 的用户 ID

-- 1. 查看项目的基本信息
SELECT id, name, status, created_by, workflow_task_id
FROM creative_projects
WHERE name LIKE '%傻女重生记%' OR name LIKE '%睁眼后盖房发家%';

-- 2. 查看与该项目关联的所有工作流任务
SELECT wt.id, wt.task_type, wt.status, wt.assignee_id,
       wt.project_id, wt.topic_id,
       u.username as assignee_username, u.display_name as assignee_name
FROM workflow_tasks wt
LEFT JOIN users u ON wt.assignee_id = u.id
WHERE wt.project_id = '{project_id}';

-- 3. 查看 brief 类型的工作流任务
SELECT wt.id, wt.task_type, wt.status, wt.assignee_id,
       wt.project_id, wt.topic_id,
       u.username as assignee_username, u.display_name as assignee_name
FROM workflow_tasks wt
LEFT JOIN users u ON wt.assignee_id = u.id
WHERE wt.project_id = '{project_id}' AND wt.task_type = 'brief';

-- 4. 检查 bianju 是否是项目成员
SELECT pm.*, u.username, u.display_name
FROM project_members pm
JOIN users u ON pm.user_id = u.id
WHERE pm.project_id = '{project_id}'
  AND pm.user_id = '{user_id}';

-- 5. 检查项目是否已经有锁定的 brief
SELECT id, project_id, version, status, risk_level, created_at
FROM creative_blueprints
WHERE project_id = '{project_id}'
ORDER BY version DESC;
