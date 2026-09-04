-- 检查 bianju 是否是该项目的成员
-- 请将 {project_id} 替换为实际项目 ID，{user_id} 替换为 bianju 的用户 ID

-- 1. 查看项目信息
SELECT id, name, created_by, status
FROM creative_projects
WHERE id = {project_id};

-- 2. 查看项目的所有成员
SELECT pm.*, u.username, u.display_name
FROM project_members pm
JOIN users u ON pm.user_id = u.id
WHERE pm.project_id = '{project_id}';

-- 3. 检查 bianju 是否有项目成员记录
SELECT *
FROM project_members
WHERE project_id = '{project_id}' AND user_id = '{user_id}';

-- 4. 查看 bianju 的用户信息和权限
SELECT u.id, u.username, u.display_name, u.status,
       r.code as role_code, r.name as role_name
FROM users u
LEFT JOIN user_roles ur ON u.id = ur.user_id
LEFT JOIN roles r ON ur.role_id = r.id
WHERE u.username = 'bianju' OR u.display_name = 'bianju';
