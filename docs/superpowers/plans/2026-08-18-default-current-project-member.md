# 创建项目时默认选择当前用户实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 创建项目弹窗按用户 ID 默认选择当前登录用户，不改变用户角色。

**架构：** 创意候选接口负责标记当前用户，选题库前端负责将该用户 ID 写入一个现有成员选择数组。数据库和项目成员写入协议保持不变。

**技术栈：** FastAPI、Alpine.js 模块、pytest、Playwright。

---

### 任务 1：当前用户候选标记与弹窗默认值

**文件：**
- 修改：`creative/api.py`
- 修改：`static/js/selection/topics.js`
- 修改：`tests/test_planning_assignment_api.py`
- 创建：`tests/test_selection_project_creation_browser.py`

- [x] **步骤 1：编写失败的测试**

API 测试断言请求账户候选的 `is_current_user` 为真且其他候选为假；浏览器测试用包含该标记的候选响应打开弹窗，并断言仅当前用户 ID 出现在默认选择数组中。

- [x] **步骤 2：运行测试验证失败**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_selection_project_creation_browser.py tests/test_planning_assignment_api.py -q --basetemp .pytest_tmp_default_member_red`

预期：浏览器测试因成员数组仍为空而失败；API 测试因响应缺少 `is_current_user` 而失败。

- [x] **步骤 3：编写最少实现代码**

接口返回前复制候选记录并增加：

```python
candidate["is_current_user"] = candidate["user_id"] == principal.user_id
```

前端加载完成后从两个候选数组中查找第一条 `is_current_user` 记录，并只将其 `user_id` 写入对应数组。

- [x] **步骤 4：运行测试验证通过**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_selection_project_creation_browser.py tests/test_planning_assignment_api.py -q --basetemp .pytest_tmp_default_member_green`

预期：全部通过。

- [x] **步骤 5：运行相关回归和静态检查**

运行：`.\.venv\Scripts\python.exe -m pytest tests/test_webgui_ui_changes.py tests/test_role_page_content.py -q --basetemp .pytest_tmp_default_member_regression`

运行：`node --check static/js/selection/topics.js`

运行：`.\.venv\Scripts\python.exe tools/file_header_manager.py --check creative/api.py tests/test_planning_assignment_api.py tests/test_selection_project_creation_browser.py`

预期：所有命令退出码为 0。
