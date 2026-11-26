"""QThread 后台任务：环境检测、配置执行、回滚与单次操作。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, Signal

from core.common import CommandRunner, OperationResult, default_state_dir
from core.network import (
    NetworkReport,
    collect_network_report,
    ensure_firewall_rule,
    remove_firewall_rule,
)
from core.plan import SetupPlan, SetupSummary, StepOutcome
from core.power import (
    PowerState,
    apply_remote_profile,
    read_power_state,
    restore_previous_profile,
)
from core.sunshine import (
    SunshineStatus,
    apply_config,
    build_config_plan,
    detect_sunshine,
    find_virtual_display_id,
    pairing_url,
    restart_service,
)
from core.vdd import (
    VddStatus,
    apply_resolutions,
    detect_vdd,
    reload_driver,
    reset_managed_resolutions,
)


@dataclass(frozen=True, slots=True)
class EnvironmentData:
    """Step 1 一次完整环境检测的结果。"""

    network: NetworkReport
    sunshine: SunshineStatus
    vdd: VddStatus
    power: PowerState | None = None
    error: str | None = None


class DetectionWorker(QObject):
    """在后台执行只读环境检测，避免启动时界面卡顿。"""

    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__()
        self._dry_run = dry_run

    def run(self) -> None:
        runner = CommandRunner(dry_run=self._dry_run, log_sink=self.log.emit)
        try:
            sunshine = detect_sunshine(runner)
            vdd = detect_vdd(runner)
            network = collect_network_report(runner, sunshine_exe=sunshine.executable)
            power: PowerState | None = None
            try:
                power = read_power_state(runner)
            except Exception as exc:
                runner.log(f"[电源] 读取失败，跳过电源状态展示: {exc}")
            self.finished.emit(
                EnvironmentData(
                    network=network,
                    sunshine=sunshine,
                    vdd=vdd,
                    power=power,
                )
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SetupWorker(QObject):
    """按计划执行全部配置写入。"""

    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)
    progress = Signal(int, str)

    def __init__(self, plan: SetupPlan) -> None:
        super().__init__()
        self._plan = plan
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        runner = CommandRunner(dry_run=self._plan.dry_run, log_sink=self.log.emit)
        outcomes: list[StepOutcome] = []
        network: NetworkReport | None = None
        sunshine: SunshineStatus | None = None
        vdd: VddStatus | None = None
        device_id: str | None = None

        def emit_progress(value: int, text: str) -> None:
            self.progress.emit(max(0, min(100, value)), text)

        def record(key: str, label: str, result: OperationResult) -> None:
            outcomes.append(
                StepOutcome(
                    key=key,
                    label=label,
                    ok=result.ok,
                    message=result.message,
                    details=result.details,
                    simulated=result.simulated,
                )
            )

        def cancelled() -> bool:
            if not self._cancel.is_set():
                return False
            outcomes.append(
                StepOutcome(
                    key="cancelled",
                    label="用户取消",
                    ok=False,
                    message="配置流程已取消，已完成的部分可通过回滚撤销",
                )
            )
            return True

        def finish() -> None:
            recommended_ip = network.recommended_client_ip if network else None
            summary = SetupSummary(
                plan=self._plan,
                outcomes=tuple(outcomes),
                network=network,
                sunshine=sunshine,
                vdd=vdd,
                device_id=device_id,
                recommended_ip=recommended_ip,
                pairing_url=pairing_url(recommended_ip) if recommended_ip else None,
                rollback_available=(
                    not self._plan.dry_run
                    and any(
                        outcome.ok
                        for outcome in outcomes
                        if outcome.key in {"vdd", "sunshine_config", "firewall", "power"}
                    )
                ),
            )
            emit_progress(100, "配置流程结束")
            self.finished.emit(summary)

        try:
            emit_progress(3, "检测 Sunshine 与虚拟显示器驱动")
            sunshine = detect_sunshine(runner)
            vdd = detect_vdd(runner)
            network = collect_network_report(runner, sunshine_exe=sunshine.executable)

            prerequisites_ok = True
            if not sunshine.installed or sunshine.executable is None:
                outcomes.append(
                    StepOutcome(
                        key="sunshine_detect",
                        label="Sunshine 安装",
                        ok=False,
                        message="未检测到 Sunshine，请先安装并启动服务",
                        details=(sunshine.detail,),
                    )
                )
                prerequisites_ok = False
            if not vdd.installed or vdd.settings_path is None:
                outcomes.append(
                    StepOutcome(
                        key="vdd_detect",
                        label="虚拟显示器驱动",
                        ok=False,
                        message="未检测到 VDD 或未找到 vdd_settings.xml",
                        details=(vdd.detail,),
                    )
                )
                prerequisites_ok = False
            if not prerequisites_ok:
                finish()
                return
            if cancelled():
                finish()
                return

            assert vdd.settings_path is not None
            assert sunshine is not None

            emit_progress(18, "写入虚拟显示器分辨率")
            result = apply_resolutions(
                runner,
                vdd.settings_path,
                self._plan.monitors,
                monitor_count=len(self._plan.monitors),
                state_dir=default_state_dir(),
            )
            record("vdd", "虚拟显示器分辨率", result)
            if not result.ok:
                finish()
                return
            if cancelled():
                finish()
                return

            if self._plan.reload_driver:
                emit_progress(30, "重载虚拟显示器驱动")
                record("driver_reload", "重载虚拟显示器驱动", reload_driver(runner))
                if cancelled():
                    finish()
                    return

            emit_progress(46, "提取虚拟屏 device_id")
            device_id = find_virtual_display_id(runner, sunshine)
            outcomes.append(
                StepOutcome(
                    key="device_id",
                    label="提取虚拟屏 device_id",
                    ok=device_id is not None,
                    message=device_id or "未能从日志或 dxgi-info.exe 提取设备 ID",
                )
            )

            emit_progress(60, "写入 sunshine.conf")
            config_plan = build_config_plan(
                address_family=self._plan.address_family,
                output_name=device_id,
                resolution_mode=self._plan.resolution_mode,
                revert_on_disconnect=True,
            )
            result = apply_config(runner, sunshine, config_plan)
            record("sunshine_config", "写入 sunshine.conf", result)
            if result.ok:
                emit_progress(72, "重启 Sunshine 服务")
                record("service", "重启 Sunshine 服务", restart_service(runner, sunshine))
            if cancelled():
                finish()
                return

            if self._plan.enable_firewall:
                emit_progress(82, "添加防火墙放行规则")
                if sunshine.executable is None:
                    outcomes.append(
                        StepOutcome(
                            key="firewall",
                            label="防火墙放行",
                            ok=False,
                            message="找不到 sunshine.exe，无法添加规则",
                        )
                    )
                else:
                    record(
                        "firewall",
                        "防火墙放行",
                        ensure_firewall_rule(runner, sunshine.executable),
                    )

            if self._plan.apply_power_profile:
                emit_progress(90, "应用电源保活配置")
                try:
                    record("power", "电源保活", apply_remote_profile(runner))
                except Exception as exc:
                    outcomes.append(
                        StepOutcome(
                            key="power",
                            label="电源保活",
                            ok=False,
                            message=f"应用电源配置失败: {exc}",
                        )
                    )

            finish()
        except Exception as exc:
            outcomes.append(
                StepOutcome(
                    key="unexpected",
                    label="未预期错误",
                    ok=False,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            finish()


class RollbackWorker(QObject):
    """按执行摘要回滚本工具写入的内容。"""

    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)
    progress = Signal(int, str)

    def __init__(self, summary: SetupSummary) -> None:
        super().__init__()
        self._summary = summary

    def run(self) -> None:
        runner = CommandRunner(
            dry_run=self._summary.plan.dry_run,
            log_sink=self.log.emit,
        )
        outcomes: list[StepOutcome] = []
        applied = {outcome.key for outcome in self._summary.outcomes if outcome.ok}

        def step(key: str, label: str, func: Callable[[], OperationResult]) -> None:
            try:
                result = func()
            except Exception as exc:
                outcomes.append(
                    StepOutcome(
                        key=key,
                        label=label,
                        ok=False,
                        message=f"{type(exc).__name__}: {exc}",
                    )
                )
                return
            outcomes.append(
                StepOutcome(
                    key=key,
                    label=label,
                    ok=result.ok,
                    message=result.message,
                    details=result.details,
                    simulated=result.simulated,
                )
            )

        try:
            self.progress.emit(5, "恢复 Sunshine 配置")
            sunshine = self._summary.sunshine
            if "sunshine_config" in applied and sunshine and sunshine.config_path:
                if runner.latest_backup(sunshine.config_path) is None:
                    outcomes.append(
                        StepOutcome(
                            key="sunshine_config",
                            label="恢复 sunshine.conf",
                            ok=False,
                            message="没有找到 sunshine.conf 的备份",
                        )
                    )
                else:
                    step(
                        "sunshine_config",
                        "恢复 sunshine.conf",
                        lambda: runner.restore_latest_backup(sunshine.config_path),
                    )

            self.progress.emit(30, "移除注入的虚拟显示器分辨率")
            vdd = self._summary.vdd
            if "vdd" in applied and vdd and vdd.settings_path:
                step(
                    "vdd",
                    "回滚虚拟显示器配置",
                    lambda: reset_managed_resolutions(
                        runner,
                        vdd.settings_path,
                        state_dir=default_state_dir(),
                    ),
                )

            self.progress.emit(50, "还原电源配置")
            if "power" in applied:
                step(
                    "power",
                    "还原电源配置",
                    lambda: restore_previous_profile(runner),
                )

            self.progress.emit(70, "移除防火墙规则")
            if "firewall" in applied:
                step(
                    "firewall",
                    "移除防火墙规则",
                    lambda: remove_firewall_rule(runner),
                )

            if "service" in applied and sunshine:
                self.progress.emit(85, "重启 Sunshine 服务")
                step(
                    "service",
                    "重启 Sunshine 服务",
                    lambda: restart_service(runner, sunshine),
                )

            if not outcomes:
                outcomes.append(
                    StepOutcome(
                        key="rollback",
                        label="回滚",
                        ok=True,
                        message="没有需要回滚的配置",
                    )
                )
            self.progress.emit(100, "回滚结束")
            self.finished.emit(tuple(outcomes))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class FunctionWorker(QObject):
    """把单个同步操作放进 QThread，例如修复 IPv6 或切回仅电脑屏幕。"""

    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)

    def __init__(
        self,
        func: Callable[[CommandRunner], OperationResult],
        *,
        dry_run: bool = False,
    ) -> None:
        super().__init__()
        self._func = func
        self._dry_run = dry_run

    def run(self) -> None:
        runner = CommandRunner(dry_run=self._dry_run, log_sink=self.log.emit)
        try:
            self.finished.emit(self._func(runner))
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
