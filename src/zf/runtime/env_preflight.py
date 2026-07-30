"""环境层 preflight(审计 P0-6 / D12,裁决 B environment 类首批 producer)。

D12 判词:环境故障零事件化,整层伪装成调度故障——R20 shim 事故(zf 命令
指向无 zf 包的 python,hook 全灭)只能靠 operator memory;avbs-r5 的
root 属主文件把 parity 证据永久卡死,伪装成语义 gap 烧了 10 圈 rework。

四探针,全部秒级:

- hook 命令可执行性(R20 类):渲染进 pane/hook 的 zf 命令真的能跑;
- tmux 二进制(启动硬依赖,此前只在 web 投影里查);
- workdir 属主一致性(avbs-r5 类):state_dir 下出现非当前 uid 文件
  即报(docker 挂载跑测试的典型残留);
- 浏览器依赖(轻探):项目声明 playwright 而浏览器缓存缺失时提示。

`zf validate --cold-start` 展示全部结果;`zf start` 对前两项硬门,
后两项 WARN + emit `env.preflight.failed`(problem_class=environment,
经 registry 进 Supervisor/Run Manager 语言)。
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvCheck:
    name: str
    ok: bool
    hard: bool  # True = start 应拒启;False = WARN + 事件
    detail: str = ""


def check_hook_command(zf_cmd: str) -> EnvCheck:
    tokens = shlex.split(zf_cmd or "zf")
    binary = tokens[0]
    if not shutil.which(binary) and not Path(binary).exists():
        return EnvCheck(
            "hook_command", False, True,
            f"hook command executable is unavailable: {binary!r}",
        )
    try:
        proc = subprocess.run(
            [*tokens, "events", "--help"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return EnvCheck(
            "hook_command", False, True,
            f"hook command could not run: {exc}",
        )
    if proc.returncode != 0:
        tail = (proc.stderr or b"").decode(errors="replace")[-200:]
        return EnvCheck(
            "hook_command", False, True,
            f"hook command exited with status {proc.returncode}: {tail}",
        )
    return EnvCheck("hook_command", True, True, zf_cmd)


def check_tmux() -> EnvCheck:
    if not shutil.which("tmux"):
        return EnvCheck(
            "tmux", False, True,
            "tmux is not available on PATH; it is required to start this workflow",
        )
    return EnvCheck("tmux", True, True, "")


def check_workdir_ownership(state_dir: Path) -> EnvCheck:
    workdirs = Path(state_dir) / "workdirs"
    if not workdirs.exists():
        return EnvCheck("workdir_ownership", True, False, "no workdirs yet")
    uid = os.getuid()
    for root, dirs, files in os.walk(workdirs):
        for name in [*dirs, *files]:
            path = Path(root) / name
            try:
                if path.lstat().st_uid != uid:
                    return EnvCheck(
                        "workdir_ownership", False, False,
                        f"file is not owned by the current uid: {path}; "
                        "a stale container mount may block evidence writes",
                    )
            except OSError:
                continue
    return EnvCheck("workdir_ownership", True, False, "")


def check_browser_deps(project_root: Path) -> EnvCheck:
    has_playwright = any(
        (Path(project_root) / name).exists()
        for name in ("playwright.config.ts", "playwright.config.js")
    )
    if not has_playwright:
        return EnvCheck("browser_deps", True, False, "project has no playwright config")
    cache = Path.home() / ".cache" / "ms-playwright"
    if not cache.exists() or not any(cache.iterdir()):
        return EnvCheck(
            "browser_deps", False, False,
            "the project declares Playwright, but no browser is installed in "
            "~/.cache/ms-playwright; browser E2E will fail at startup",
        )
    return EnvCheck("browser_deps", True, False, "")


def run_env_preflight(
    *,
    zf_cmd: str,
    state_dir: Path,
    project_root: Path,
) -> list[EnvCheck]:
    return [
        check_hook_command(zf_cmd),
        check_tmux(),
        check_workdir_ownership(state_dir),
        check_browser_deps(project_root),
    ]
