# 红果完整取流修复包（2026-11-11）

将本次已经部署的独立后端修复纳入 Git 管理；**不是把共享目录整个复制进 AiWorker，也不切换 AiWorker 的默认下载来源**。本包新增模块与现用部署逐字节一致，通过五个文件的补丁接入原后端。

## 内容与许可证边界

- `backend/full_playback.py`、`backend/playback_recovery/`：18 个新增文件，包含 Kotlin 源码、通用 stdin 签名桥、Node worker、离线测试、构建脚本、许可证及运行 JAR。
- `full-playback.patch`：修改 `sixgod.py`、`hongguo.py`、`offline_dl.py`、`server.py` 和 `frida/offline_decrypt.py`，不包含原后端全量源码。
- 两份 manifest 记录更新前五个文件和更新后全部 23 个文件的 SHA256；用于阻止覆盖未知版本/用户修改，以及确认已部署版本。
- 上游固定为 [tsukiyo-zero/hongguoTV](https://github.com/tsukiyo-zero/hongguoTV/tree/77a99f509b8a5c586bf6ef453147cf1003203970)（PR #104，`kotlin-v0.1.31`）。来源和继承的第三方声明见 `backend/playback_recovery/NOTICE.md`。
- **本目录的新增实现、移植代码及本次补丁增量按 GPL-3.0-only 分发**，完整文本见 `backend/playback_recovery/LICENSE`；根目录的非商业许可证不应附加到这些文件。本目录作为独立源码/补丁集合保存，不修改 AiWorker 其他代码的许可证。
- 本包不重新许可原后端，也不宣称原后端全量再分发或将其与其他程序组合分发已经完成许可证兼容审查；发布完整后端前仍须核验其全部许可与对应源码。独立进程本身不免除许可义务。
- 不含 API key、Cookie、运行时身份/签名、内容密钥、媒体鉴权 URL 或媒体。GPL 签名 JAR 对应源码全部在 `src/`；Kotlin 标准库的 Apache-2.0/第三方许可及 Bouncy Castle 的 MIT 声明另附于 `third-party/`，不是假定所有 JAR 都内嵌许可证。

## 行为

新 `video_tab` GET 接口使用 Argus/Ladon/Gorgon 签名；同一后端进程复用一次匿名注册，不在拒绝后更换身份。严格按请求 vid 对应键取模型，只接受支持的 H264/HEVC，拒绝 ByteVC2。上游 HTTP/业务拒绝直接失败，不回退旧接口或镜像预览。

缓存改为 `full-playback-v1`，不复用旧预览；下载/解密写唯一 partial，音视频与模型时长校验后原子发布，失败清理临时文件。保留原固定密钥鉴权和 `/stream` 的 H264/AAC 输出；日志不输出内容密钥。

## 检查与安装

要求 Windows PowerShell、Git、Node 22+、Java 17，以及原后端自带的 Python/PyAV 和媒体依赖。Node 需在 PATH，或在原启动环境设置 `HONGGUO_PLAYBACK_NODE`；不会自动下载运行时。

```powershell
$root = '\\Chandao\共享\软件\Hongguoxiazaiqi1\Hongguoxiazaiqi'
& .\install.ps1 -BackendRoot $root -Check
```

若输出 `ALREADY_DEPLOYED`，全部 23 文件与本次已验收版本一致，**无需覆盖或重启**。若为 `BASELINE_MATCHED`，先在原启动终端停止后端，并确认其所属 Node/Java 子进程退出及 11800 释放，再执行：

```powershell
& .\install.ps1 -BackendRoot $root
```

安装先在 `data/deployment-backups/` 内保存原五文件并生成候选结果，23 文件全部校验后才覆盖安装目录；遇到未知基线或已存在的新模块则停止。安装不自动启停服务、不接触 8899/数据库。写入中断时保持停服，并检查备份；不要直接启动部分更新的服务。

更新后使用原命令、原目录、原密钥环境启动；不要把密钥放进命令行或仓库。恢复时在停服后运行**安装生成的备份目录中**的 `rollback.ps1 -BackendRoot $root`，它仅恢复五个原文件，保留不再引用的新增模块和缓存，且拒绝覆盖后续修改。回退后不能直接重装，需人工检查保留模块。

## 离线回归与构建

在本包目录运行：

```powershell
$env:HONGGUO_PLAYBACK_JAVA = (Get-Command java).Source
node .\backend\playback_recovery\worker.test.cjs
java -cp 'backend/playback_recovery/playback.jar;backend/playback_recovery/lib/*' com.hongguotv.core.PlaybackCliKt --selftest
```

这两项仅为离线验证，不请求上游。若有安装前五文件的备份，可执行 `test.ps1 -OriginalFiles <原五文件所在目录> -Python <Python路径> -Node <Node路径> -Java <Java17路径>`，重建并核验 23 文件全部 SHA256、Python 语法、禁止预览/旧接口兜底、合成时长/音轨校验及上述 Node/Java 测试；不会读私有设备配置或访问网络，临时结果留在系统 Temp。

源码重建运行 `backend/playback_recovery/build.ps1 -CompilerHome <依赖目录> -Java <Java17路径>`。编译器依赖名称/版本见脚本；`compiler-curl.jar` 对应 Maven `org.jetbrains.kotlin:kotlin-compiler-embeddable:2.2.20`，SHA1 为 `bb8331b7585e36ea311825b01a1c06860c055fd1`。其余依赖均为脚本内同名 Maven JAR；运行依赖已随包保留。重新构建会改变 JAR 的归档字节，不应假设仍与现用部署 manifest 相同；更新 manifest 前须重新验证。

## 真实验收记录（此前部署阶段）

已部署全部 23 文件的 SHA256 一致。真实重启后的 11800 `/stream`：无密钥 401、有密钥 200；第 1 集时长 **183.6 秒**，H264 1080×1920 + AAC，完整音视频严格解码通过。输出 SHA256：`b966f5522520bf997604f1b8ea84aafb1bf2cf16d14f47a97f7ed0a1486fb99c`。旧预览保留但不再使用。

本次 Git 同步只整理已部署版本，不再次调用上游或重启服务。上述单集证据不代表全剧、批量或所有清晰度验收通过。
