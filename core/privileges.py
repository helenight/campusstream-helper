"""Windows UAC 管理员权限检测与自动提权。

启动流程（Phase 3）：
1. 调用 :func:`is_admin`；
2. 非管理员时调用 :func:`relaunch_as_admin` 触发 UAC；
3. 用户取消或提权失败时，提示「Dry-Run 仍可使用，但无法写入系统配置」。
"""

from __future__ import annotations

import os
from typing import Sequence


def is_admin() -> bool:
    """当前进程是否已拥有管理员权限；非 Windows 平台始终返回 False。"""
    if os.name != "nt":
        return False

    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def relaunch_as_admin(argv: Sequence[str] | None = None) -> bool:
    """通过 ``ShellExecuteW`` 的 ``runas`` 动词重新启动当前程序。

    Args:
        argv: 传给新进程的参数；None 表示沿用 ``sys.argv[1:]``。

    Returns:
        False 表示用户取消了 UAC 或提权失败，调用方应提示后退出。
    """
    raise NotImplementedError("Phase 2 实现")
