# 运行依赖许可来源

- `kotlin-stdlib-2.2.20/` 中 LICENSE、COPYRIGHT、NOTICE、README 及其 `third_party/` 下四份文件，原样取自 [JetBrains/kotlin v2.2.20](https://github.com/JetBrains/kotlin/tree/v2.2.20/license)。README 为上游整体许可索引，并非本包包含其中列出的全部组件；四份额外许可对应 JVM 标准库的 collections、time、UnsignedJVM、MathJVM。
- `bcprov-jdk18on-1.79-LICENSE.txt` 为随包 `bcprov-jdk18on-1.79.jar` 的 `org.bouncycastle.LICENSE` 类输出，保留其版权与 MIT 条款。
- `backend/playback_recovery/DEPLOYMENT.md` 是部署时原样记录，其中“JARs retain their embedded licenses/notices”并不适用于没有内嵌许可文件的 Kotlin Maven JAR。本 Git 修复包用此目录补齐，不改写已经运行并验收的二进制或部署 manifest。
- 本目录许可文本属于各自权利人，不能统一改成 GPL 或根目录非商业许可；GPL 适用于本次派生实现，不改变这些依赖本身的许可。
