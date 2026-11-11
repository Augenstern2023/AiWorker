# 独立红果后端修复包

- 本目录为外部后端的独立修复包，不是 AiWorker 进程内模块；AiWorker 不导入这里的 Python/JS。
- `backend/` 保留已部署文件的原始字节及 SHA256，禁止自动格式化或添加项目非商业许可证头。
- GPL 派生源码按 `backend/playback_recovery/LICENSE` 和 `NOTICE.md` 保留许可；不要将它改成根目录许可证。
- 不包含原后端全量源码，只包含新增模块和五个文件的最小补丁。不要加入设备配置、账户、Cookie、密钥、鉴权 URL、日志、数据库、缓存或媒体。
- 校验后端使用 `install.ps1 -Check`；默认不启停服务、不修改数据库、不启动 8899。
