"""跨模块共用的命令执行、结果对象与安全写入工具。

全项目的安全约束：
1. 所有会改变系统状态的命令必须经过 ``CommandRunner.mutate_*``；
2. 只读检测走 ``query_*``，Dry-Run 下仍然执行；
3. Dry-Run 下 ``mutate_*`` / ``write_text_atomic`` 只记录日志，不产生副作用；
4. 覆盖已有配置文件前，自动生成带时间戳的 ``.bak`` 备份。
"""

from __future__ import annotations

import base64
import glob as glob_module
import locale
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

LogSink = Callable[[str], None]

_LOGGER_NAME = "campusstream"
_STATE_DIR_NAME = "CampusStreamHelper"


def default_state_dir() -> Path:
    """本工具的本地状态目录：``%LOCALAPPDATA%\\CampusStreamHelper``。"""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    return root / _STATE_DIR_NAME


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

    @classmethod
    def success(
        cls,
        message: str,
        *,
        details: Sequence[str] = (),
        backup_path: Path | None = None,
        simulated: bool = False,
    ) -> "OperationResult":
        return cls(True, message, tuple(details), backup_path, simulated)

    @classmethod
    def failure(
        cls,
        message: str,
        *,
        details: Sequence[str] = (),
    ) -> "OperationResult":
        return cls(False, message, tuple(details), None, False)


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
        self._powershell = _find_powershell()

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
            try:
                self._log_sink(message)
            except Exception:
                # UI 回调异常不能影响系统操作本身。
                self._logger.exception("日志回调执行失败")

    def query(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        """执行只读命令；Dry-Run 下仍会执行，否则无法完成环境检测。"""
        return self._execute(argv, timeout_s=timeout_s, action="查询")

    def mutate(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        """执行写命令；Dry-Run 下返回 simulated=True 的模拟成功结果。"""
        argv_tuple = tuple(str(item) for item in argv)
        if self._dry_run:
            self.log(f"[DRY-RUN] 跳过写命令: {subprocess.list2cmdline(argv_tuple)}")
            return CommandResult(argv_tuple, 0, simulated=True)
        return self._execute(argv_tuple, timeout_s=timeout_s, action="执行")

    def query_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        """以 UTF-8 输出执行只读 PowerShell 脚本。"""
        argv = self._build_powershell_argv(script)
        return self._execute(argv, timeout_s=timeout_s, action="查询", display="PowerShell 查询")

    def mutate_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        """执行写 PowerShell 脚本；Dry-Run 下不执行。"""
        argv = self._build_powershell_argv(script)
        if self._dry_run:
            self.log("[DRY-RUN] 跳过写操作: PowerShell 脚本")
            return CommandResult(tuple(argv), 0, simulated=True)
        return self._execute(argv, timeout_s=timeout_s, action="执行", display="PowerShell 操作")

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        """读取文本文件；只读操作，Dry-Run 下照常执行。"""
        return Path(path).read_text(encoding=encoding)

    def backup_file(self, path: Path) -> Path | None:
        """为 ``path`` 生成同目录的 ``<name>.<时间戳>.bak``，返回备份路径。

        文件不存在时返回 None；Dry-Run 下不创建任何文件。
        """
        path = Path(path)
        if self._dry_run:
            if path.exists():
                self.log(f"[DRY-RUN] 跳过备份: {path}")
            return None
        if not path.exists():
            return None

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        candidate = path.with_name(f"{path.name}.{stamp}.bak")
        index = 1
        while candidate.exists():
            candidate = path.with_name(f"{path.name}.{stamp}-{index}.bak")
            index += 1
        shutil.copy2(path, candidate)
        self.log(f"[备份] {path} -> {candidate.name}")
        return candidate

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> Path | None:
        """先备份、再以临时文件 + 原子替换写入，返回本次备份路径。"""
        path = Path(path)
        if self._dry_run:
            self.log(f"[DRY-RUN] 跳过写入: {path}（{len(content)} 字符）")
            return None

        backup = self.backup_file(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temp_path.open("w", encoding=encoding, newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        self.log(f"[写入] {path}")
        return backup

    def latest_backup(self, path: Path) -> Path | None:
        """返回 ``path`` 最近一次自动备份；没有则返回 None。"""
        path = Path(path)
        if not path.parent.exists():
            return None
        pattern = f"{glob_module.escape(path.name)}.*.bak"
        backups = [item for item in path.parent.glob(pattern) if item.is_file()]
        if not backups:
            return None
        return max(backups, key=lambda item: (item.stat().st_mtime, item.name))

    def restore_latest_backup(self, path: Path) -> OperationResult:
        """把 ``path`` 还原到最近一次备份。

        还原前会为当前内容再生成一次备份，避免回滚本身造成不可逆丢失。
        """
        path = Path(path)
        backup = self.latest_backup(path)
        if backup is None:
            return OperationResult.failure(f"未找到 {path.name} 的备份文件")

        if self._dry_run:
            self.log(f"[DRY-RUN] 将还原: {path} <- {backup.name}")
            return OperationResult.success(
                f"[DRY-RUN] 将还原 {path.name}",
                details=(f"来源: {backup}",),
                backup_path=backup,
                simulated=True,
            )

        safety = self.backup_file(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, path)
        details = [f"来源: {backup}"]
        if safety is not None:
            details.append(f"还原前备份: {safety}")
        self.log(f"[还原] {path} <- {backup.name}")
        return OperationResult.success(
            f"已还原 {path.name}",
            details=tuple(details),
            backup_path=backup,
        )

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _build_powershell_argv(self, script: str) -> list[str]:
        preamble = (
            "$ErrorActionPreference='Stop'; "
            "$ProgressPreference='SilentlyContinue'; "
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        )
        full_script = f"{preamble}\n{script}"
        encoded = base64.b64encode(full_script.encode("utf-16-le")).decode("ascii")
        return [
            self._powershell,
            "-NoProfile",
            "-NonInteractive",
            "-NoLogo",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ]

    def _execute(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float | None,
        action: str,
        display: str | None = None,
    ) -> CommandResult:
        argv_tuple = tuple(str(item) for item in argv)
        timeout = self._timeout_s if timeout_s is None else timeout_s
        shown = display or subprocess.list2cmdline(argv_tuple)
        self.log(f"[{action}] {shown}")

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            completed = subprocess.run(
                argv_tuple,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=creationflags,
            )
        except FileNotFoundError:
            message = f"找不到命令: {argv_tuple[0]}"
            self.log(f"[失败] {message}")
            return CommandResult(argv_tuple, -1, stderr=message)
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_bytes(exc.stdout or b"")
            stderr = _decode_bytes(exc.stderr or b"")
            message = f"命令超时（{timeout:g} 秒）"
            self.log(f"[失败] {message}: {shown}")
            return CommandResult(argv_tuple, -1, stdout, stderr or message)
        except OSError as exc:
            message = f"命令执行失败: {exc}"
            self.log(f"[失败] {message}")
            return CommandResult(argv_tuple, -1, stderr=message)

        stdout = _decode_bytes(completed.stdout)
        stderr = _decode_bytes(completed.stderr)
        result = CommandResult(argv_tuple, completed.returncode, stdout, stderr)
        if not result.ok:
            tail = (stderr or stdout).strip().splitlines()
            reason = tail[-1] if tail else "无错误输出"
            self.log(f"[失败] 退出码 {completed.returncode}: {reason}")
        return result


def _find_powershell() -> str:
    return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"


def _decode_bytes(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    encodings = ["utf-8-sig", locale.getpreferredencoding(False)]
    if os.name == "nt":
        encodings.append("mbcs")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
