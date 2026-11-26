"""CampusStream Helper 程序入口。

Phase 1：仅验证包结构与版本号。
Phase 3：在此创建 ``QApplication``，执行 UAC 检测后打开四步向导。
"""

from __future__ import annotations

import sys

from core import __version__


def main(argv: list[str] | None = None) -> int:
    """启动应用，返回进程退出码。"""
    _ = argv
    print(f"CampusStream Helper v{__version__}")
    print("当前为 Phase 1 项目骨架，图形界面将在 Phase 3 接入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
