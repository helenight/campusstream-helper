"""CampusStream Helper 核心层。

本包只负责 Windows 检测、配置生成与系统操作，不依赖 PySide6，
便于在无界面环境下运行测试（Phase 2 的 Dry-Run 测试即基于此）。
"""

from __future__ import annotations

__version__ = "0.2.2"

__all__ = ["__version__"]
