# -*- coding: utf-8 -*-
# Copyright (c) 2025 relakkes@gmail.com
#
# This file is part of MediaCrawler project.
# Repository: https://github.com/NanmiCoder/MediaCrawler/blob/main/tests/test_webgui_autostart_scripts.py
# GitHub: https://github.com/NanmiCoder
# Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
#
# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import json
import os
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"


def describe_script(name: str) -> dict:
    script_path = SCRIPTS_ROOT / name
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Describe",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def powershell_literal(path: Path) -> str:
    return str(path).replace("'", "''")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only integration")
def test_background_launcher_describes_all_interfaces_webgui_runtime() -> None:
    description = describe_script("start_webgui_background.ps1")

    assert description["project_root"] == str(PROJECT_ROOT)
    assert description["python"] == str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    assert description["module"] == "WebGUI:app"
    assert description["host"] == "0.0.0.0"
    assert description["port"] == 8899
    assert description["log"] == str(PROJECT_ROOT / "logs" / "webgui-autostart.log")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only integration")
def test_installer_describes_resilient_logon_task() -> None:
    description = describe_script("install_webgui_autostart.ps1")

    assert description["task_name"] == "AiWorker WebGUI"
    assert description["trigger"] == "logon"
    assert description["delay_seconds"] == 15
    assert description["restart_count"] == 3
    assert description["restart_interval_minutes"] == 1
    assert description["multiple_instances"] == "IgnoreNew"
    assert description["run_level"] == "Limited"
    assert description["launcher"] == str(SCRIPTS_ROOT / "start_webgui_background.ps1")


@pytest.mark.skipif(os.name != "nt", reason="Windows-only integration")
def test_uninstaller_targets_only_the_aiworker_task() -> None:
    description = describe_script("uninstall_webgui_autostart.ps1")

    assert description == {
        "task_name": "AiWorker WebGUI",
        "operation": "unregister",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows-only integration")
def test_logged_process_keeps_native_stderr_as_log_output(tmp_path: Path) -> None:
    module_path = SCRIPTS_ROOT / "webgui_autostart_common.psm1"
    log_path = tmp_path / "native-stderr.log"
    probe = f"""
    $ErrorActionPreference = 'Stop'
    Import-Module '{powershell_literal(module_path)}' -Force
    $powershell = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $arguments = @('-NoProfile', '-Command', 'Write-Error normal-stderr; exit 0')
    $exitCode = Invoke-LoggedProcess -FilePath $powershell -ArgumentList $arguments -LogPath '{powershell_literal(log_path)}'
    [ordered]@{{
        exit_code = $exitCode
        log = [string](Get-Content -Raw -LiteralPath '{powershell_literal(log_path)}')
    }} | ConvertTo-Json -Compress
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", probe],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    outcome = json.loads(result.stdout)
    assert outcome["exit_code"] == 0
    assert "normal-stderr" in outcome["log"]
