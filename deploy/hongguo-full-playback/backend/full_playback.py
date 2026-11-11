# SPDX-License-Identifier: GPL-3.0-only
"""Bounded, process-local bridge to the full playback implementation. No preview fallback."""
import atexit
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading

ROOT = Path(__file__).resolve().parent
_lock = threading.Lock()
_process = None
_responses = queue.Queue()
_failed = False


class PlaybackError(RuntimeError):
    """Safe fixed diagnostic: never attach upstream responses or credentials."""


def _close():
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _process.kill()


atexit.register(_close)


def _read_pipe(process):
    try:
        for line in process.stdout:
            _responses.put(line)
    finally:
        _responses.put(None)


def get_video_model(vid):
    global _process, _failed
    vid = str(vid)
    if not re.fullmatch(r"[0-9]{8,24}", vid):
        raise PlaybackError("INVALID_EPISODE_ID")
    with _lock:
        if _failed:
            raise PlaybackError("PLAYBACK_WORKER_UNAVAILABLE")
        if _process is None:
            node = os.environ.get("HONGGUO_PLAYBACK_NODE") or shutil.which("node")
            if not node:
                raise PlaybackError("NODE_RUNTIME_MISSING")
            try:
                _process = subprocess.Popen(
                    [node, str(ROOT / "playback_recovery" / "worker.cjs")],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    encoding="utf-8", bufsize=1, cwd=str(ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                raise PlaybackError("PLAYBACK_WORKER_START_FAILED") from None
            threading.Thread(target=_read_pipe, args=(_process,), daemon=True).start()
        try:
            _process.stdin.write(json.dumps({"vid": vid}) + "\n")
            _process.stdin.flush()
            line = _responses.get(timeout=45)
            if line is None or len(line) > 4 * 1024 * 1024:
                raise ValueError
            result = json.loads(line)
        except (OSError, ValueError, queue.Empty):
            _failed = True
            _close()
            raise PlaybackError("PLAYBACK_WORKER_UNAVAILABLE") from None
        if result.get("ok") is not True:
            code = result.get("error", "PLAYBACK_FAILED")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z_]{3,64}", code):
                code = "PLAYBACK_FAILED"
            raise PlaybackError(code)
        model = result.get("model")
        if not isinstance(model, dict) or not model.get("video_list"):
            raise PlaybackError("INVALID_MODEL")
        return model


def verify_media(path, expected_duration):
    """Do not publish truncated/preview files as successful full-episode caches."""
    import av
    try:
        expected = float(expected_duration)
        with av.open(str(path)) as media:
            duration = float(media.duration or 0) / av.time_base
            valid = bool(media.streams.video) and bool(media.streams.audio)
        if expected <= 0 or not valid or abs(duration - expected) > max(2.0, expected * .02):
            raise ValueError
        return duration
    except Exception:
        raise PlaybackError("FULL_EPISODE_VALIDATION_FAILED") from None
