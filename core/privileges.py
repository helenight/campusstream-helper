"""Windows UAC 管理员权限检测与自动提权。

启动流程（Phase 3）：
1. 调用 :func:`is_admin`；
2. 非管理员时调用 :func:`relaunch_as_admin` 触发 UAC；
3. 用户取消或提权失败时，提示「Dry-Run 仍可使用，但无法写入系统配置」。
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

_SW_SHOWNORMAL = 1


def is_admin() -> bool:
    """当前进程是否已拥有管理员权限；非 Windows 平台始终返回 False。"""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(argv: Sequence[str] | None = None) -> bool:
    """通过 ``ShellExecuteW`` 的 ``runas`` 动词重新启动当前程序。

    Args:
        argv: 传给新进程的参数；None 表示沿用 ``sys.argv[1:]``。

    Returns:
        True 表示当前已是管理员，或用户同意 UAC 且新进程已启动；
        False 表示用户取消 UAC 或提权失败，调用方应提示后退出。
    """
    if os.name != "nt":
        return False
    if is_admin():
        return True

    args = list(sys.argv[1:] if argv is None else argv)
    executable = sys.executable
    if getattr(sys, "frozen", False):
        parameters = subprocess.list2cmdline(args)
    else:
        script = str(Path(sys.argv[0]).resolve())
        parameters = subprocess.list2cmdline([script, *args])

    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_int,
    ]
    shell32.ShellExecuteW.restype = ctypes.c_void_p

    try:
        result = shell32.ShellExecuteW(
            None,
            "runas",
            executable,
            parameters,
            str(Path.cwd()),
            _SW_SHOWNORMAL,
        )
    except OSError:
        return False

    # ShellExecuteW 返回值 > 32 表示成功；1223 表示用户取消 UAC。
    return int(result or 0) > 32
