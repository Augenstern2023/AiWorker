# Alembic 迁移远端同步保护实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在提交、推送、远端 CI 和生产升级入口四处阻止未提交或未同步至 `manjuxuanpin` 的 Alembic 迁移修改生产数据库。

**架构：** `tools/alembic_guard.py` 提供无 UI 的迁移图、Git 暂存区、推送范围和生产前置条件校验；两个小型 CLI 分别服务 hooks/CI 与生产升级。所有 Git 范围检查都针对提交树执行，常规本地检查不访问生产数据库，远端 Gitea workflow 用隔离 PostgreSQL 验证空库升级。

**技术栈：** Python 3.11、Alembic、SQLAlchemy、Git、pre-commit、pytest、Gitea Actions、uv、PostgreSQL 16

---

## 文件结构

- 创建：`tools/alembic_guard.py` — 校验迁移图、暂存区、推送范围、远端 SHA 与数据库 revision 的可复用领域逻辑。
- 创建：`tools/check_alembic_integrity.py` — 面向开发者、pre-commit 和 CI 的命令行入口。
- 创建：`tools/install_git_hooks.py` — 跨平台安装 `pre-commit` 与 `pre-push` hook。
- 创建：`scripts/upgrade_production_database.py` — 只在当前提交已同步至 `manjuxuanpin/main` 时执行 `alembic upgrade head`。
- 创建：`tests/test_alembic_guard.py` — 临时 Git 仓库中的迁移图、暂存区和推送范围测试。
- 创建：`tests/test_production_migration_guard.py` — 生产升级失败关闭和成功调用测试。
- 创建：`tests/test_alembic_guard_configuration.py` — pre-commit、安装器、README 和 Gitea workflow 契约测试。
- 修改：`.pre-commit-config.yaml` — 注册 repository、staged 和 pre-push 三个本地校验 hook。
- 创建：`.gitea/workflows/alembic-integrity.yml` — `manjuxuanpin` 远端迁移检查和 PostgreSQL 空库升级。
- 修改：`README.md` — 记录 hooks 安装、日常检查和生产迁移受控命令。

### 任务 1：实现可复用的 Alembic 迁移图校验器

**文件：**
- 创建：`tests/test_alembic_guard.py`
- 创建：`tools/alembic_guard.py`
- 创建：`tools/check_alembic_integrity.py`

- [ ] **步骤 1：编写真实仓库与最小迁移目录测试**

在 `tests/test_alembic_guard.py` 添加项目版权头，构造只包含 `alembic.ini`、
`system_database/alembic/versions/` 和最小 revision 文件的临时目录。测试真实仓库
当前 head，并覆盖 revision 数字前缀不一致、重复 revision、缺失父节点和多 head：

```python
def test_real_repository_has_one_resolvable_head() -> None:
    report = validate_migration_tree(PROJECT_ROOT)

    assert report.head
    assert report.head in report.revisions


def test_revision_number_must_match_filename_prefix(tmp_path: Path) -> None:
    root = make_migration_tree(
        tmp_path,
        {"0021_wrong.py": ("0022_actual", "0020_user_presence")},
    )

    with pytest.raises(GuardError, match="文件名前缀 0021.*revision 前缀 0022"):
        validate_migration_tree(root)
```

数字前缀规则只比较文件名和 revision ID 第一个下划线前的编号；仓库中已有的
`0002_audit_actor_display_snapshot.py`、`0010_project_creator_permission_backfill.py`
和 `0018_workflow_member_writer_operation.py` 因编号一致而合法。

- [ ] **步骤 2：运行迁移图测试确认失败**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py -k "migration_tree or real_repository or revision_number" -v
```

预期：测试收集失败，提示 `tools.alembic_guard` 不存在。

- [ ] **步骤 3：实现迁移图数据结构和 Alembic 解析**

在 `tools/alembic_guard.py` 定义稳定接口：

```python
@dataclass(frozen=True)
class MigrationReport:
    head: str
    revisions: tuple[str, ...]


class GuardError(RuntimeError):
    pass


def validate_migration_tree(repo_root: Path) -> MigrationReport:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(repo_root / "system_database" / "alembic"),
    )
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise GuardError(f"Alembic 必须只有一个 head，当前为: {heads}")
    revisions = tuple(revision.revision for revision in scripts.walk_revisions())
    _validate_filename_prefixes(scripts)
    return MigrationReport(head=heads[0], revisions=revisions)
```

捕获 Alembic 的 `CommandError`、重复 revision 警告转异常、脚本导入异常，并统一转成
不包含数据库 URL 的 `GuardError`。`_validate_filename_prefixes()` 从
`Script.revision` 与 `Script.path` 取数字前缀比较。Alembic 本身负责缺失父节点、
环路和重复 revision 的图解析。

- [ ] **步骤 4：实现仓库文件跟踪检查与 repository CLI**

增加：

```python
def untracked_migration_files(repo_root: Path) -> tuple[Path, ...]:
    output = run_git(
        repo_root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "--",
        "system_database/alembic/versions/*.py",
    )
    return tuple(Path(line) for line in output.splitlines() if line)


def validate_repository(repo_root: Path) -> MigrationReport:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        raise GuardError(f"存在未跟踪迁移脚本: {', '.join(map(str, untracked))}")
    return validate_migration_tree(repo_root)
```

`tools/check_alembic_integrity.py` 使用 `argparse` 暴露 `repository` 子命令，成功打印
唯一 head，失败只打印校验错误并返回退出码 1。脚本从自身路径定位仓库根目录，不能
依赖启动命令的当前目录。

- [ ] **步骤 5：运行任务 1 测试并提交**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py -k "migration_tree or real_repository or revision_number" -v
uv run python tools/check_alembic_integrity.py repository
uv run pre-commit run --files tools/alembic_guard.py tools/check_alembic_integrity.py tests/test_alembic_guard.py
```

预期：全部通过，CLI 输出当前唯一 Alembic head；在本计划基线提交上该值为
`0020_user_presence`，新增合法 migration 后不要求修改测试中的固定 revision。

提交：

```powershell
git add tools/alembic_guard.py tools/check_alembic_integrity.py tests/test_alembic_guard.py
git commit -m "feat: validate alembic migration graph"
```

### 任务 2：校验暂存区和发往 manjuxuanpin 的提交范围

**文件：**
- 修改：`tools/alembic_guard.py`
- 修改：`tools/check_alembic_integrity.py`
- 修改：`tests/test_alembic_guard.py`

- [ ] **步骤 1：编写暂存区契约测试**

使用 `git init`、本地测试身份和最小迁移链创建临时仓库。测试
`system_database/schema.py` 已暂存但 migration 未暂存时失败，二者同时新增时通过，
以及任何已存在 migration 的修改、删除或重命名都失败：

```python
def test_staged_schema_change_requires_new_staged_migration(git_repo: Path) -> None:
    append_text(git_repo / "system_database/schema.py", "\nnew_column = True\n")
    git(git_repo, "add", "system_database/schema.py")

    with pytest.raises(GuardError, match="必须同时暂存新增 Alembic migration"):
        validate_staged_changes(git_repo)


@pytest.mark.parametrize("operation", ["modify", "delete", "rename"])
def test_staged_history_migration_is_immutable(
    git_repo: Path, operation: str
) -> None:
    change_historical_migration(git_repo, operation)

    with pytest.raises(GuardError, match="禁止修改、删除或重命名历史 migration"):
        validate_staged_changes(git_repo)
```

- [ ] **步骤 2：运行暂存区测试确认失败**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py -k staged -v
```

预期：FAIL，提示 `validate_staged_changes` 尚未定义。

- [ ] **步骤 3：解析 Git name-status 并实现 staged 规则**

在 `tools/alembic_guard.py` 增加：

```python
@dataclass(frozen=True)
class GitChange:
    status: str
    old_path: str | None
    path: str


def staged_changes(repo_root: Path) -> tuple[GitChange, ...]:
    raw = run_git_bytes(
        repo_root,
        "diff",
        "--cached",
        "--name-status",
        "-z",
        "--find-renames",
    )
    return parse_name_status(raw)


def validate_staged_changes(repo_root: Path) -> None:
    changes = staged_changes(repo_root)
    _reject_historical_migration_changes(changes)
    _require_migration_for_schema_changes(changes)
    if _touches_migrations(changes):
        validate_repository(repo_root)
```

`parse_name_status()` 必须正确消费重命名记录的旧路径和新路径；只有状态 `A` 且路径
位于版本目录的文件算新增 migration。结构声明路径常量初始值严格为
`("system_database/schema.py",)`。

- [ ] **步骤 4：编写 pre-push 提交树测试**

测试普通更新、新分支、删除 ref 和非 `manjuxuanpin` 远端。通过标准 Git
pre-push 输入格式构造更新：

```python
update = PushUpdate(
    local_ref="refs/heads/main",
    local_sha=local_sha,
    remote_ref="refs/heads/main",
    remote_sha=remote_sha,
)

with pytest.raises(GuardError, match="推送范围修改了数据库结构.*没有新增 migration"):
    validate_push_updates(git_repo, "manjuxuanpin", (update,))
```

另加一个测试：提交树包含新增 migration，但当前工作树把该文件删除或修改时，仍以
`local_sha` 的提交树校验成功；这约束实现不能错误地用工作树代替待推送提交。

- [ ] **步骤 5：运行 pre-push 测试确认失败**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py -k push -v
```

预期：FAIL，提示 `PushUpdate` 或 `validate_push_updates` 尚未定义。

- [ ] **步骤 6：实现推送范围与提交树导出**

在 `tools/alembic_guard.py` 定义：

```python
ZERO_SHA = "0" * 40
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass(frozen=True)
class PushUpdate:
    local_ref: str
    local_sha: str
    remote_ref: str
    remote_sha: str


def parse_push_updates(value: str) -> tuple[PushUpdate, ...]:
    updates: list[PushUpdate] = []
    for line_number, line in enumerate(value.splitlines(), start=1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 4:
            raise GuardError(f"pre-push 第 {line_number} 行必须包含 4 个字段")
        updates.append(PushUpdate(*fields))
    return tuple(updates)


def validate_push_updates(
    repo_root: Path,
    remote_name: str,
    updates: tuple[PushUpdate, ...],
) -> None:
    untracked = untracked_migration_files(repo_root)
    if untracked:
        raise GuardError(f"存在未跟踪迁移脚本: {', '.join(map(str, untracked))}")
    for update in updates:
        if update.local_sha == ZERO_SHA:
            continue
        base_sha = push_base(repo_root, remote_name, update)
        changes = committed_changes(repo_root, base_sha, update.local_sha)
        if remote_name == "manjuxuanpin":
            _reject_historical_migration_changes(changes)
            _require_migration_for_schema_changes(changes)
        with exported_migration_tree(repo_root, update.local_sha) as tree_root:
            validate_migration_tree(tree_root)
```

具体规则：

- 删除 ref（`local_sha == ZERO_SHA`）跳过提交树解析。
- `push_base()` 对已有 ref 返回 `remote_sha`；对新分支尝试
  `merge-base(local_sha, refs/remotes/manjuxuanpin/main)`，找不到时返回
  `EMPTY_TREE_SHA`。`committed_changes()` 使用
  `git diff --name-status -z --find-renames <base> <tip>` 并复用
  `parse_name_status()`。
- `remote_name == "manjuxuanpin"` 时执行 schema/migration 配对及历史 migration
  不可变规则；其他远端只验证 local SHA 的迁移图。
- `exported_migration_tree()` 是 context manager，使用
  `git archive --format=tar <local_sha> alembic.ini system_database/alembic` 输出到
  `tempfile.TemporaryDirectory()`，用 `tarfile` 安全解包，拒绝绝对路径和 `..` 路径，
  再调用 `validate_migration_tree()`。
- 每次 pre-push 都额外拒绝当前工作树中的未跟踪 migration 文件。

扩展 CLI：`pre-push --remote-name <name> --remote-url <url>` 从 stdin 读取更新记录。
`remote-url` 只用于错误上下文，不参与“受保护远端”的身份判断，也不得完整回显包含
凭证的 URL。

- [ ] **步骤 7：运行任务 2 测试并提交**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py -v
uv run pre-commit run --files tools/alembic_guard.py tools/check_alembic_integrity.py tests/test_alembic_guard.py
```

预期：全部通过。

提交：

```powershell
git add tools/alembic_guard.py tools/check_alembic_integrity.py tests/test_alembic_guard.py
git commit -m "feat: guard staged and pushed migrations"
```

### 任务 3：接入 Git hooks 并提供跨平台安装入口

**文件：**
- 修改：`.pre-commit-config.yaml`
- 创建：`tools/install_git_hooks.py`
- 创建：`tests/test_alembic_guard_configuration.py`
- 修改：`README.md`

- [ ] **步骤 1：编写配置契约测试**

在 `tests/test_alembic_guard_configuration.py` 读取 YAML 和 README，断言三个 hook 的
入口、stage、`always_run` 与 `pass_filenames`，并测试安装器调用两个 hook type：

```python
def test_pre_commit_registers_all_alembic_guards() -> None:
    config = yaml.safe_load((PROJECT_ROOT / ".pre-commit-config.yaml").read_text("utf-8"))
    hooks = {
        hook["id"]: hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
    }

    assert hooks["check-alembic-repository"]["stages"] == ["pre-commit"]
    assert hooks["check-alembic-staged"]["stages"] == ["pre-commit"]
    assert hooks["check-alembic-pre-push"]["stages"] == ["pre-push"]
    assert all(hooks[name]["always_run"] for name in hooks if name.startswith("check-alembic"))


def test_hook_installer_requests_commit_and_push(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(install_git_hooks.subprocess, "check_call", calls.append)

    install_git_hooks.install()

    assert calls == [[
        sys.executable,
        "-m",
        "pre_commit",
        "install",
        "--hook-type",
        "pre-commit",
        "--hook-type",
        "pre-push",
    ]]
```

- [ ] **步骤 2：运行配置测试确认失败**

运行：

```powershell
uv run pytest tests/test_alembic_guard_configuration.py -v
```

预期：FAIL，因为 hooks 和安装脚本尚不存在。

- [ ] **步骤 3：注册三个本地 hook**

在 `.pre-commit-config.yaml` 的 `repo: local` 下加入：

```yaml
      - id: check-alembic-repository
        name: Check Alembic migration graph
        entry: python tools/check_alembic_integrity.py repository
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]

      - id: check-alembic-staged
        name: Check staged schema migration pairing
        entry: python tools/check_alembic_integrity.py staged
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-commit]

      - id: check-alembic-pre-push
        name: Check migrations pushed to manjuxuanpin
        entry: python tools/check_alembic_integrity.py pre-push
        language: system
        pass_filenames: false
        always_run: true
        stages: [pre-push]
```

pre-commit 会把 pre-push 的远端名称和 URL 追加为位置参数，CLI 同时兼容位置参数和
显式参数，测试两种调用形式。

- [ ] **步骤 4：实现安装器并更新 README**

`tools/install_git_hooks.py` 提供 `install() -> None`，只调用当前解释器中的
`pre_commit` 模块，不复制 `.git/hooks` 文件。README 的“测试与质量检查”增加：

```powershell
# 首次检出后安装提交和推送保护
uv run python tools/install_git_hooks.py

# 手工验证 Alembic 图
uv run python tools/check_alembic_integrity.py repository
```

同时注明保护远端仅为 `manjuxuanpin`，`--no-verify` 不得用于数据库结构提交。

- [ ] **步骤 5：验证真实 hook 并提交**

运行：

```powershell
uv run pytest tests/test_alembic_guard_configuration.py tests/test_alembic_guard.py -v
uv run pre-commit run check-alembic-repository --all-files
uv run pre-commit run check-alembic-staged --all-files
uv run pre-commit run --files .pre-commit-config.yaml tools/install_git_hooks.py README.md tests/test_alembic_guard_configuration.py
```

预期：全部通过；安装器本身只由测试验证，不在隔离 worktree 中改写主工作区 hooks。

提交：

```powershell
git add .pre-commit-config.yaml tools/install_git_hooks.py tests/test_alembic_guard_configuration.py README.md
git commit -m "build: enforce alembic git hooks"
```

### 任务 4：增加生产数据库升级门禁

**文件：**
- 修改：`tools/alembic_guard.py`
- 修改：`tools/check_alembic_integrity.py`
- 创建：`scripts/upgrade_production_database.py`
- 创建：`tests/test_production_migration_guard.py`
- 修改：`README.md`

- [ ] **步骤 1：编写生产前置条件失败关闭测试**

将远端查询与数据库 revision 读取作为可注入函数，逐项测试：非 main 分支、已跟踪
修改、未跟踪 migration、远端不可达、`HEAD != manjuxuanpin/main`、数据库未知
revision。所有失败都必须发生在 upgrade 回调之前：

```python
@pytest.mark.parametrize(
    ("precondition", "message"),
    [
        ("wrong_branch", "必须从 main 分支执行"),
        ("dirty_tree", "工作树不干净"),
        ("remote_unreachable", "无法确认 manjuxuanpin/main"),
        ("head_not_remote", "当前 HEAD 尚未同步"),
        ("unknown_revision", "数据库 revision 不存在于本地迁移图"),
    ],
)
def test_production_guard_fails_before_upgrade(
    production_repo: Path,
    precondition: str,
    message: str,
) -> None:
    upgrade_calls: list[str] = []
    arrange_precondition(production_repo, precondition)

    with pytest.raises(GuardError, match=message):
        guarded_upgrade(
            production_repo,
            read_remote_sha=fake_remote_sha,
            read_database_revision=fake_database_revision,
            upgrade=lambda target: upgrade_calls.append(target),
        )

    assert upgrade_calls == []
```

- [ ] **步骤 2：运行生产门禁测试确认失败**

运行：

```powershell
uv run pytest tests/test_production_migration_guard.py -v
```

预期：FAIL，提示 `guarded_upgrade` 尚未定义。

- [ ] **步骤 3：实现纯校验函数**

在 `tools/alembic_guard.py` 增加：

```python
def validate_production_preconditions(
    repo_root: Path,
    *,
    remote_sha: str,
    database_revision: str | None,
) -> MigrationReport:
    if current_branch(repo_root) != "main":
        raise GuardError("生产迁移必须从 main 分支执行")
    if tracked_worktree_changes(repo_root):
        raise GuardError("生产迁移前工作树不干净")
    report = validate_repository(repo_root)
    head_sha = run_git(repo_root, "rev-parse", "HEAD")
    if head_sha != remote_sha:
        raise GuardError("当前 HEAD 尚未同步至 manjuxuanpin/main")
    if database_revision is not None and database_revision not in report.revisions:
        raise GuardError(
            f"数据库 revision {database_revision} 不存在于本地迁移图"
        )
    return report
```

数据库 revision 为 `None` 表示尚无 `alembic_version` 表或空库，允许从 baseline
升级；任何非空未知值都失败。

- [ ] **步骤 4：实现受控生产升级 CLI**

在 `scripts/upgrade_production_database.py` 中：

- 从脚本位置定位仓库根目录。
- 使用 `git ls-remote --exit-code manjuxuanpin refs/heads/main` 只读取得远端 SHA；
  命令失败、输出为空或格式错误统一抛出 `GuardError("无法确认 manjuxuanpin/main")`。
- 使用 `SystemDatabaseSettings.from_environment()` 取得 URL，并通过 SQLAlchemy 查询
  `alembic_version`；捕获“表不存在”作为 `None`，连接失败则拒绝迁移。
- `read_remote_main_sha(repo_root) -> str` 与
  `read_database_revision(database_url) -> str | None` 放在
  `tools/alembic_guard.py`，供只读 CLI 和升级脚本共同使用。
- 升级脚本定义以下可注入入口，测试以 lambda 替换外部访问：

```python
def guarded_upgrade(
    repo_root: Path,
    *,
    read_remote_sha: Callable[[], str],
    read_database_revision: Callable[[], str | None],
    upgrade: Callable[[str], None],
) -> MigrationReport:
    report = validate_production_preconditions(
        repo_root,
        remote_sha=read_remote_sha(),
        database_revision=read_database_revision(),
    )
    upgrade("head")
    return report
```

- `main()` 调用 `guarded_upgrade()`，且校验完成后才执行
  `alembic.command.upgrade(Config(<repo>/alembic.ini), "head")`。
- 日志只输出 redacted URL、远端 SHA、数据库当前 revision 和目标 head。
- 成功退出码为 0；任一 `GuardError` 返回 1，且不执行 Alembic。

同时在 `tools/check_alembic_integrity.py` 增加只读的 `production` 子命令。它复用
相同的远端 SHA 和数据库 revision 读取逻辑，只运行
`validate_production_preconditions()` 并报告目标 head，不调用 Alembic；生产升级脚本
在其后执行相同校验和升级。这样 CI 或运维可以先单独运行门禁检查：

```powershell
uv run python tools/check_alembic_integrity.py production
```

- [ ] **步骤 5：编写成功路径与凭证脱敏测试**

```python
def test_guarded_upgrade_calls_head_once_when_remote_and_database_are_valid(
    production_repo: Path,
) -> None:
    calls: list[str] = []

    result = guarded_upgrade(
        production_repo,
        read_remote_sha=lambda: git(production_repo, "rev-parse", "HEAD"),
        read_database_revision=lambda: "0020_user_presence",
        upgrade=lambda target: calls.append(target),
    )

    assert result.head == "0020_user_presence"
    assert calls == ["head"]
```

另测异常输出不包含测试 URL 中的密码 `secret-production-password`。

- [ ] **步骤 6：更新生产命令、运行测试并提交**

README 将生产升级命令明确替换为：

```powershell
uv run python scripts/upgrade_production_database.py
```

并说明直接执行 `uv run alembic upgrade head` 仅用于隔离测试数据库，不是生产入口。

运行：

```powershell
uv run pytest tests/test_production_migration_guard.py tests/test_alembic_guard.py -v
uv run pre-commit run --files tools/alembic_guard.py tools/check_alembic_integrity.py scripts/upgrade_production_database.py tests/test_production_migration_guard.py README.md
```

预期：全部通过。

提交：

```powershell
git add tools/alembic_guard.py tools/check_alembic_integrity.py scripts/upgrade_production_database.py tests/test_production_migration_guard.py README.md
git commit -m "feat: gate production alembic upgrades"
```

### 任务 5：增加 manjuxuanpin 远端 CI 并完成端到端验证

**文件：**
- 创建：`.gitea/workflows/alembic-integrity.yml`
- 修改：`tests/test_alembic_guard_configuration.py`
- 修改：`README.md`

- [ ] **步骤 1：编写 Gitea workflow 契约测试**

测试 workflow 存在，触发 push/pull_request，包含 PostgreSQL 16 服务、非生产测试库
URL、迁移图检查、空库升级及保护测试：

```python
def test_gitea_alembic_workflow_runs_remote_guards() -> None:
    workflow = (
        PROJECT_ROOT / ".gitea/workflows/alembic-integrity.yml"
    ).read_text("utf-8")

    assert "postgres:16" in workflow
    assert "AIWORKER_TEST_SYSTEM_DATABASE_URL" in workflow
    assert "tools/check_alembic_integrity.py repository" in workflow
    assert "alembic upgrade head" in workflow
    assert "tests/test_alembic_guard.py" in workflow
    assert "tests/test_production_migration_guard.py" in workflow
```

- [ ] **步骤 2：运行 workflow 测试确认失败**

运行：

```powershell
uv run pytest tests/test_alembic_guard_configuration.py -k gitea -v
```

预期：FAIL，提示 `.gitea/workflows/alembic-integrity.yml` 不存在。

- [ ] **步骤 3：实现 Gitea Actions 工作流**

创建：

```yaml
name: Alembic integrity

on:
  push:
  pull_request:

jobs:
  verify-migrations:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: aiworker_test
          POSTGRES_PASSWORD: aiworker_test
          POSTGRES_DB: aiworker_migration_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U aiworker_test -d aiworker_migration_test"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 12
    env:
      AIWORKER_SYSTEM_DATABASE_URL: postgresql+psycopg://aiworker_test:aiworker_test@127.0.0.1:5432/aiworker_migration_test
      AIWORKER_TEST_SYSTEM_DATABASE_URL: postgresql+psycopg://aiworker_test:aiworker_test@127.0.0.1:5432/aiworker_migration_test
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.11"
          enable-cache: true
      - run: uv sync --locked
      - run: uv run python tools/check_alembic_integrity.py repository
      - run: uv run alembic upgrade head
      - run: uv run pytest tests/test_alembic_guard.py tests/test_production_migration_guard.py tests/test_alembic_guard_configuration.py tests/test_system_database_alembic.py -q
```

workflow 仅使用隔离测试库。README 注明需要在 `manjuxuanpin/main` 分支保护中把
`verify-migrations` 设置为必需检查；若服务器未启用 Gitea Actions，必须先启用或在
现有服务器 CI 中调用相同命令，不能把未执行的 workflow 当作保障已生效。

- [ ] **步骤 4：运行定向测试与空库升级**

运行：

```powershell
uv run pytest tests/test_alembic_guard.py tests/test_production_migration_guard.py tests/test_alembic_guard_configuration.py -v
uv run python tools/check_alembic_integrity.py repository
uv run alembic heads
uv run alembic upgrade head
uv run pytest tests/test_system_database_alembic.py -v
```

预期：唯一 head 可解析；当前已配置的隔离 PostgreSQL 从空库升级到 head；迁移测试
全部通过。若本机没有 `AIWORKER_TEST_SYSTEM_DATABASE_URL`，先记录跳过证据，再使用
项目已有安全测试库配置重新运行，最终验收不接受 PostgreSQL 用例全部跳过。

- [ ] **步骤 5：运行完整质量门禁**

运行：

```powershell
uv run pytest tests/ -q
uv run pre-commit run --all-files
uv run pre-commit run --hook-stage pre-push check-alembic-pre-push --all-files
git diff --check
git status --short
```

预期：`tests/` 与 pre-commit 全部通过；pre-push hook 能正常启动；只有本任务预期
文件发生变化。不得创建仓库内 `.uv-cache*`，不得中断当前运行中的网页服务。

- [ ] **步骤 6：提交 CI 与文档**

```powershell
git add .gitea/workflows/alembic-integrity.yml tests/test_alembic_guard_configuration.py README.md
git commit -m "ci: verify alembic migrations remotely"
```

- [ ] **步骤 7：安装主工作区 hooks、推送并核验远端**

实现分支通过审查并合入 `main` 后，在主工作区运行：

```powershell
uv run python tools/install_git_hooks.py
git push manjuxuanpin main
git ls-remote manjuxuanpin refs/heads/main
git rev-parse HEAD
```

预期：`ls-remote` 与本地 `HEAD` SHA 完全一致，`manjuxuanpin` 上
`verify-migrations` 成功。推送前不得执行生产迁移；远端检查成功并启用必需状态检查
后，才使用 `uv run python scripts/upgrade_production_database.py`。
