"""测试用的假 Runner：记录调用，不触碰真实系统。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from core.common import CommandResult, OperationResult


class FakeRunner:
    """与 CommandRunner 同接口的内存实现。"""

    def __init__(
        self,
        *,
        dry_run: bool = False,
        powershell_response: str = "{}",
        command_response: str | Callable[[tuple[str, ...]], CommandResult] = "",
    ) -> None:
        self.dry_run = dry_run
        self.logs: list[str] = []
        self.powershell_scripts: list[str] = []
        self.powershell_mutations: list[str] = []
        self.commands: list[tuple[str, ...]] = []
        self.writes: list[tuple[Path, str]] = []
        self._powershell_response = powershell_response
        self._command_response = command_response

    def log(self, message: str) -> None:
        self.logs.append(message)

    def query_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        self.powershell_scripts.append(script)
        return CommandResult(("pwsh",), 0, self._powershell_response)

    def mutate_powershell(self, script: str, *, timeout_s: float | None = None) -> CommandResult:
        self.powershell_mutations.append(script)
        if self.dry_run:
            return CommandResult(("pwsh",), 0, simulated=True)
        return CommandResult(("pwsh",), 0, "OK")

    def query(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        argv_tuple = tuple(str(item) for item in argv)
        self.commands.append(argv_tuple)
        if callable(self._command_response):
            return self._command_response(argv_tuple)
        return CommandResult(argv_tuple, 0, self._command_response)

    def mutate(self, argv: Sequence[str], *, timeout_s: float | None = None) -> CommandResult:
        argv_tuple = tuple(str(item) for item in argv)
        self.commands.append(argv_tuple)
        if self.dry_run:
            return CommandResult(argv_tuple, 0, simulated=True)
        return CommandResult(argv_tuple, 0, "OK")

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return Path(path).read_text(encoding=encoding)

    def write_text_atomic(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> Path | None:
        target = Path(path)
        self.writes.append((target, content))
        if self.dry_run:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return None

    def backup_file(self, path: Path) -> Path | None:
        return None

    def latest_backup(self, path: Path) -> Path | None:
        return None

    def restore_latest_backup(self, path: Path) -> OperationResult:
        return OperationResult.failure("FakeRunner 不支持还原")
