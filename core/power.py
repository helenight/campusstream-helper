"""模块 D：电源保活管理（插电永不休眠、定时关屏、一键还原）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.common import CommandRunner, OperationResult

REMOTE_DISPLAY_OFF_MINUTES = 10


@dataclass(frozen=True, slots=True)
class PowerState:
    """当前电源方案的 AC / DC 超时（秒）；0 表示永不。"""

    scheme_guid: str
    scheme_name: str
    ac_sleep_seconds: int
    ac_display_off_seconds: int
    dc_sleep_seconds: int
    dc_display_off_seconds: int

    @property
    def remote_ready(self) -> bool:
        """插电永不休眠即视为满足远控保活。"""
        return self.ac_sleep_seconds == 0


def read_power_state(runner: CommandRunner) -> PowerState:
    """读取当前电源方案的睡眠与关屏超时。

    通过 ``powercfg /getactivescheme`` 与 ``powercfg /query`` 解析，
    不修改任何设置。
    """
    raise NotImplementedError("Phase 2 实现")


def apply_remote_profile(
    runner: CommandRunner,
    *,
    state_path: Path | None = None,
) -> OperationResult:
    """应用「插电永不休眠，10 分钟后关闭显示器」。

    修改前把原 AC 超时写入 ``state_path``（None 表示
    ``%LOCALAPPDATA%\\CampusStreamHelper\\power_state.json``）；
    DC（电池）设置保持不变。

    对应命令：
    ``powercfg /change standby-timeout-ac 0``
    ``powercfg /change monitor-timeout-ac 10``
    """
    raise NotImplementedError("Phase 2 实现")


def restore_previous_profile(
    runner: CommandRunner,
    *,
    state_path: Path | None = None,
) -> OperationResult:
    """读取状态文件并还原应用前的 AC 睡眠与关屏超时。"""
    raise NotImplementedError("Phase 2 实现")
