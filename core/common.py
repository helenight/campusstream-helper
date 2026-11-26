"""跨模块共用的命令执行、结果对象与安全写入工具。

全项目的安全约束：
1. 所有会改变系统状态的命令必须经过 ``CommandRunner.mutate_*``；
2. 只读检测走 ``query_*``，Dry-Run 下仍然执行；
3. Dry-Run 下 ``mutate_*`` / ``write_text_atomic`` 只记录日志，不产生副作用；
4. 覆盖已有配置文件前，自动生成带时间戳的 ``.bak`` 备份。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

LogSink = Callable[[str], None]

_LOGGER_NAME = "campusstream"


@dataclass(frozen=True, slots=True)
class CommandResult:
    """一次外部命令的完整结果。"""

    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    simulated: bool = False

    @property
    def ok(self) -> bool:
        """``returncode`` 为 0 视为成功；Dry-Run 模拟结果同样为 True。"""
        return self.returncode == 0


@dataclass(frozen=True, slots=True)
class OperationResult:
    """面向 UI 的高层操作结果。"""

    ok: bool
    message: str
    details: tuple[str, ...] = ()
    backup_path: Path | None = None
    simulated: bool = False


class CommandRunner:
    """系统命令与文件写入的唯一出口。

    Args:
        dry_run: True 时所有写操作只记录、不执行；只读查询照常执行。
        log_sink: UI 日志回调，接收已经格式化好的中文日志行。
        timeout_s: 单条命令默认超时秒数。
    """

    def __init__(
        self,
        *,
        dry_run: bool = False,
        log_sink: LogSink | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._dry_run = dry_run
        self._log_sink = log_sink
        self._timeout_s = timeout_s
        self._logger = logging.getLogger(_LOGGER_NAME)

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    def log(self, message: str) -> None:
        """同时写入 Python 日志与 UI 日志面板。"""
        self._logger.info(message)
        if self._log_sink is not None:
            self._log_sink(message)

    def query(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        """执行只读命令；Dry-Run 下仍会执行，否则无法完成环境检测。"""
        raise NotImplementedError("Phase 2 实现")

    def mutate(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        """执行写命令；Dry-Run 下返回 simulated=True 的模拟成功结果。"""
        raise NotImplementedError("Phase 2 实现")

    def query_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        """以 UTF-8 输出执行只读 PowerShell 脚本。"""
        raise NotImplementedError("Phase 2 实现")

    def mutate_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        """执行写 PowerShell 脚本；Dry-Run 下不执行。"""
        raise NotImplementedError("Phase 2 实现")

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        """读取文本文件；只读操作，Dry-Run 下照常执行。"""
        return path.read_text(encoding=encoding)

    def backup_file(self, path: Path) -> Path | None:
        """为 ``path`` 生成同目录的 ``<name>.<时间戳>.bak``，返回备份路径。

        文件不存在时返回 None；Dry-Run 下不创建任何文件。
        """
        raise NotImplementedError("Phase 2 实现")

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> Path | None:
        """先备份、再以临时文件 + 原子替换写入，返回本次备份路径。"""
        raise NotImplementedError("Phase 2 实现")

    def latest_backup(self, path: Path) -> Path | None:
        """返回 ``path`` 最近一次自动备份；没有则返回 None。"""
        raise NotImplementedError("Phase 2 实现")

    def restore_latest_backup(self, path: Path) -> OperationResult:
        """把 ``path`` 还原到最近一次备份。

        还原前会为当前内容再生成一次备份，避免回滚本身造成不可逆丢失。
        """
        raise NotImplementedError("Phase 2 实现")
