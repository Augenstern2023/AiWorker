# Local full-playback integration (2026-11-11)

Upstream: https://github.com/tsukiyo-zero/hongguoTV
Pinned commit: 77a99f509b8a5c586bf6ef453147cf1003203970 (PR #104, kotlin-v0.1.31).
Argus.kt, Bytes.kt, Signer.kt, AesV3.kt and VendorConstants.kt are preserved upstream sources.
playback-device.js and request-signal.js retain upstream SPDX and attribution.
worker.cjs includes URL decoding adapted from upstream src/native/app-source.js.
Local changes: generic stdin signer bridge, process-only registration, bounded
requests, exact episode-key matching, no identity rotation or legacy fallback,
compatible-codec filtering, full-duration validation and atomic versioned caches.

Derived files are GPL-3.0-only; see LICENSE and upstream NOTICE.md. This is a
private local deployment, not a conclusion about compatibility for redistribution
of the complete backend. Separate processes do not waive license obligations.
Keep all source, build instructions and notices with any permitted distribution.
Runtime dependencies: Java 17 (existing backend/jre), Node 22+ (PATH or
HONGGUO_PLAYBACK_NODE), Kotlin standard library 2.2.20 (Apache-2.0),
Bouncy Castle 1.79 (MIT). JARs retain their embedded licenses/notices.

Build: run build.ps1 -CompilerHome <Kotlin-2.2.20-dependencies> -Java <java.exe>.
CompilerHome needs the Maven jars listed in build.ps1; compiler-curl.jar is
org.jetbrains.kotlin:kotlin-compiler-embeddable:2.2.20 (SHA1
bb8331b7585e36ea311825b01a1c06860c055fd1). Other dependency versions are explicit
in build.ps1. Runtime jars are under lib/. No account/session/signing data is
written to disk. Anonymous registration is shared per backend process; explicit
HTTP or business refusals stop playback without trying another identity.

Only files successfully checked against the model duration are published under
data/stream-cache/full-playback-v1. Old preview caches remain untouched and unused.
The /stream endpoint preserves its existing fixed-key authentication and H264/AAC
desktop output. This deployment does not change AiWorker main, port 8899 or DBs.
