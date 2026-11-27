"""QThread 后台任务：环境检测、配置执行、回滚与单次操作。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, Signal

from core.common import CommandRunner, OperationResult, default_state_dir
from core.downloader import (
    DEFAULT_VDD_DIR,
    SUNSHINE_PAGE,
    VDD_PAGE,
    DownloadCancelled,
    DownloadError,
    HttpTransport,
    ManualDownloadRequired,
    Package,
    PackagePlan,
    default_download_dir,
    download_asset,
    find_vdd_control,
    install_command,
    install_local_package,
    install_sunshine,
    install_vdd,
    resolve_package,
)
from core.detection import (
    DetectionItem,
    DetectionState,
    ITEM_ORDER,
    admin_item,
    failed_item,
    gpu_item,
    network_item,
    running_item,
    sunshine_item,
    vdd_item,
)
from core.gpu import GpuInfo, detect_gpu
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
from core.privileges import is_admin
from core.sunshine import (
    SunshineStatus,
    apply_config,
    build_config_plan,
    configured_output_name,
    detect_sunshine,
    find_virtual_display_id,
    pairing_url,
    restart_service,
    snapshot_log_sizes,
    wait_for_virtual_display_id,
)
from core.uninstaller import (
    UninstallOptions,
    UninstallReport,
    run_uninstall,
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
    """Step 1 一次完整环境检测的结果；单项失败时对应字段为 ``None``。"""

    network: NetworkReport | None
    sunshine: SunshineStatus | None
    vdd: VddStatus | None
    power: PowerState | None = None
    gpu: GpuInfo | None = None
    items: tuple[DetectionItem, ...] = ()
    finished_at: datetime | None = None
    error: str | None = None


class DetectionWorker(QObject):
    """在后台逐项执行只读环境检测，并把每一项状态实时回传界面。"""

    item_changed = Signal(object)   # DetectionItem
    progress = Signal(int, int)     # completed, total
    busy_changed = Signal(bool)
    finished = Signal(object)       # EnvironmentData
    failed = Signal(str)
    log = Signal(str)

    def __init__(self, *, dry_run: bool = False) -> None:
        super().__init__()
        self._dry_run = dry_run
        self._items: dict[str, DetectionItem] = {}

    def _publish(self, item: DetectionItem) -> None:
        self._items[item.key] = item
        self.item_changed.emit(item)
        completed = sum(
            1
            for current in self._items.values()
            if current.state in {
                DetectionState.READY,
                DetectionState.WARNING,
                DetectionState.FAILED,
            }
        )
        self.progress.emit(completed, len(ITEM_ORDER))

    def run(self) -> None:
        runner = CommandRunner(dry_run=self._dry_run, log_sink=self.log.emit)
        self.busy_changed.emit(True)
        sunshine: SunshineStatus | None = None
        vdd: VddStatus | None = None
        network: NetworkReport | None = None
        power: PowerState | None = None
        gpu: GpuInfo | None = None

        def step(key: str, label: str, detect) -> DetectionItem:
            self._publish(running_item(key))  # type: ignore[arg-type]
            try:
                return detect()
            except Exception as exc:
                runner.log(f"[检测] {label} 失败：{type(exc).__name__}: {exc}")
                return failed_item(key, f"{type(exc).__name__}: {exc}")  # type: ignore[arg-type]

        def detect_admin() -> DetectionItem:
            nonlocal power
            ok = is_admin()
            try:
                power = read_power_state(runner)
            except Exception as exc:
                runner.log(f"[电源] 读取失败，跳过电源状态展示: {exc}")
            return admin_item(ok, power)

        def detect_sunshine_item() -> DetectionItem:
            nonlocal sunshine
            sunshine = detect_sunshine(runner)
            return sunshine_item(sunshine)

        def detect_vdd_item() -> DetectionItem:
            nonlocal vdd
            vdd = detect_vdd(runner)
            return vdd_item(vdd)

        def detect_gpu_item() -> DetectionItem:
            nonlocal gpu
            gpu = detect_gpu(runner)
            return gpu_item(gpu)

        def detect_network_item() -> DetectionItem:
            nonlocal network
            network = collect_network_report(
                runner,
                sunshine_exe=sunshine.executable if sunshine else None,
            )
            return network_item(
                network,
                can_add_firewall=bool(sunshine and sunshine.executable),
            )

        try:
            self._publish(
                step("admin", "管理员权限", detect_admin)
            )
            self._publish(
                step("gpu", "显卡", detect_gpu_item)
            )
            self._publish(
                step("sunshine", "Sunshine 服务端", detect_sunshine_item)
            )
            self._publish(
                step("vdd", "虚拟显示驱动", detect_vdd_item)
            )
            self._publish(
                step("network", "网络与防火墙", detect_network_item)
            )
            items = tuple(
                self._items[key] for key in ITEM_ORDER if key in self._items
            )
            data = EnvironmentData(
                network=network,
                sunshine=sunshine,
                vdd=vdd,
                power=power,
                gpu=gpu,
                items=items,
                finished_at=datetime.now().astimezone(),
            )
            self.busy_changed.emit(False)
            self.finished.emit(data)
        except Exception as exc:
            self.busy_changed.emit(False)
            self.failed.emit(f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True, slots=True)
class PackageUpdate:
    """下载安装对话框使用的进度快照。"""

    stage: str
    percent: int
    received: int
    total: int
    speed_bps: float
    message: str


class PackageWorker(QObject):
    """在 QThread 中解析、下载并安装 Sunshine / VDD。"""

    update = Signal(object)          # PackageUpdate
    log = Signal(str)
    finished = Signal(object)        # OperationResult
    failed = Signal(str)
    cancelled = Signal()
    manual_required = Signal(object)  # ManualDownloadRequired
    confirm_required = Signal(str)

    def __init__(
        self,
        package: Package,
        *,
        dry_run: bool = False,
        proxy_prefix: str = "",
        local_path: Path | None = None,
        vdd_install_dir: Path | None = None,
        download_dir: Path | None = None,
        transport: HttpTransport | None = None,
        require_confirmation: bool = True,
        runner_factory: Callable[[bool], CommandRunner] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        vdd_wait_timeout_s: float = 300.0,
        vdd_poll_interval_s: float = 2.0,
    ) -> None:
        super().__init__()
        self._package = package
        self._dry_run = dry_run
        self._proxy = proxy_prefix
        self._local_path = Path(local_path) if local_path is not None else None
        self._vdd_dir = Path(vdd_install_dir) if vdd_install_dir else DEFAULT_VDD_DIR
        self._download_dir = (
            Path(download_dir) if download_dir is not None else None
        )
        self._transport = transport
        self._require_confirmation = require_confirmation
        self._runner_factory = runner_factory or (
            lambda dry: CommandRunner(dry_run=dry, log_sink=self.log.emit)
        )
        self._sleep = sleep
        self._vdd_wait_timeout_s = max(0.0, vdd_wait_timeout_s)
        self._vdd_poll_interval_s = max(0.1, vdd_poll_interval_s)
        self._cancel = threading.Event()
        self._approval = threading.Event()
        self._approved = False
        self._last_time: float | None = None
        self._last_bytes = 0
        self._last_emit = 0.0
        self._speed = 0.0

    def request_cancel(self) -> None:
        self._cancel.set()
        self._approved = False
        self._approval.set()

    def approve(self, approved: bool) -> None:
        self._approved = bool(approved)
        self._approval.set()

    def run(self) -> None:
        runner = self._runner_factory(self._dry_run)
        try:
            if self._cancel.is_set():
                raise DownloadCancelled("下载已取消")
            result = self._execute(runner)
        except DownloadCancelled:
            self.cancelled.emit()
            return
        except ManualDownloadRequired as exc:
            self.log.emit(f"[下载] {exc}")
            for detail in exc.details:
                self.log.emit(f"[下载] {detail}")
            self.manual_required.emit(exc)
            self.failed.emit(str(exc))
            return
        except DownloadError as exc:
            # 下载超时、HTTP 错误或本地文件缺失等，也应引导用户手动兜底。
            self.log.emit(f"[下载] {exc}")
            error = ManualDownloadRequired(
                str(exc),
                package=self._package,
                release_page=(
                    SUNSHINE_PAGE
                    if self._package == "sunshine"
                    else VDD_PAGE
                ),
            )
            self.manual_required.emit(error)
            self.failed.emit(str(exc))
            return
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(result)

    def _execute(self, runner: CommandRunner) -> OperationResult:
        if self._local_path is not None:
            return self._install_local(runner)

        self.update.emit(
            PackageUpdate("resolve", 2, 0, 0, 0.0, "正在获取 GitHub 最新版本…")
        )
        plan = resolve_package(
            self._package,
            transport=self._transport,
            proxy_prefix=self._proxy,
        )
        size_text = _format_size(plan.asset.size)
        self.log.emit(
            f"[解析] {plan.display_name} {plan.release.tag} · "
            f"{plan.asset.name}（{size_text}）"
        )
        self.log.emit(f"[解析] 下载地址：{plan.download_url}")
        self.update.emit(
            PackageUpdate(
                "resolve",
                8,
                0,
                plan.asset.size,
                0.0,
                f"已找到 {plan.release.tag} · {plan.asset.name}",
            )
        )
        if self._cancel.is_set():
            raise DownloadCancelled("下载已取消")

        target = (self._download_dir or default_download_dir()) / plan.asset.name
        if self._dry_run:
            self.update.emit(
                PackageUpdate(
                    "verify",
                    100,
                    0,
                    plan.asset.size,
                    0.0,
                    "Dry-Run：跳过下载，仅记录安装命令",
                )
            )
            result = self._install(runner, plan.package, target)
            self.update.emit(
                PackageUpdate("done", 100, 0, 0, 0.0, result.message)
            )
            return result

        path = download_asset(
            plan,
            self._download_dir,
            transport=self._transport,
            on_chunk=self._on_chunk,
            cancel=self._cancel,
            log=self.log.emit,
        )
        total = path.stat().st_size
        self.update.emit(
            PackageUpdate(
                "verify",
                100,
                total,
                total,
                0.0,
                "下载完成，文件大小校验通过",
            )
        )
        if self._cancel.is_set():
            raise DownloadCancelled("下载已取消")
        if not self._confirm(plan, path):
            raise DownloadCancelled("已取消安装")
        self.update.emit(
            PackageUpdate("install", 100, total, total, 0.0, "正在安装…")
        )
        result = self._install(runner, plan.package, path)
        self.update.emit(PackageUpdate("done", 100, total, total, 0.0, result.message))
        return result

    def _install(
        self,
        runner: CommandRunner,
        package: Package,
        path: Path,
    ) -> OperationResult:
        if package == "sunshine":
            return install_sunshine(runner, path, dry_run=self._dry_run)
        result = install_vdd(
            runner,
            path,
            install_dir=self._vdd_dir,
            dry_run=self._dry_run,
        )
        return self._after_vdd_install(runner, result)

    def _after_vdd_install(
        self,
        runner: CommandRunner,
        result: OperationResult,
    ) -> OperationResult:
        """VDD 文件部署后确认 PnP 状态，未就绪时等待用户在向导中安装。"""
        if not result.ok or self._dry_run:
            return result
        control = find_vdd_control(self._vdd_dir)
        if control is None:
            return result
        try:
            status = detect_vdd(runner, extra_search_roots=[self._vdd_dir])
        except Exception as exc:
            self.log.emit(f"[VDD] PnP 预检查失败，继续等待驱动：{exc}")
            status = None
        if status is not None and status.device_ready:
            return OperationResult.success(
                "VDD 驱动已就绪，无需再运行安装向导",
                details=result.details,
            )

        self.update.emit(
            PackageUpdate(
                "vdd_prompt",
                100,
                0,
                0,
                0.0,
                "已自动打开 VDD Control，请按提示安装驱动",
            )
        )
        self.log.emit(
            "[VDD] 已自动拉起 VDD.Control.exe；"
            "请点击 Virtual Display Driver → System → Install Driver"
        )
        return self._wait_for_vdd_ready(runner, result)

    def _wait_for_vdd_ready(
        self,
        runner: CommandRunner,
        result: OperationResult,
    ) -> OperationResult:
        self.update.emit(
            PackageUpdate(
                "vdd_wait",
                100,
                0,
                0,
                0.0,
                "等待 VDD 驱动就绪，完成后会自动刷新 Step 1",
            )
        )
        deadline = time.monotonic() + self._vdd_wait_timeout_s
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return OperationResult.success(
                    "已启动 VDD 安装向导；等待已取消，完成安装后请重新检测",
                    details=result.details,
                )
            try:
                status = detect_vdd(runner, extra_search_roots=[self._vdd_dir])
            except Exception as exc:
                self.log.emit(f"[VDD] 等待期间 PnP 检测失败：{exc}")
            else:
                if status.device_ready:
                    self.log.emit(
                        "[VDD] Get-PnpDevice 已检测到状态 OK 的虚拟屏，"
                        "驱动安装完成"
                    )
                    return OperationResult.success(
                        "VDD 驱动已就绪，Step 1 将自动刷新",
                        details=result.details,
                    )
            self._sleep(self._vdd_poll_interval_s)
        return OperationResult.success(
            "VDD Control 已启动，但暂未检测到驱动就绪；"
            "完成安装后请点击「重新检测」",
            details=result.details,
        )

    def _install_local(self, runner: CommandRunner) -> OperationResult:
        assert self._local_path is not None
        path = self._local_path
        self.log.emit(f"[安装] 使用本地安装包：{path}")
        self.update.emit(
            PackageUpdate("install", 10, 0, 0, 0.0, "使用本地安装包")
        )
        if not self._dry_run and not path.is_file():
            raise DownloadError(f"本地安装包不存在：{path}")
        if not self._confirm_local(path):
            raise DownloadCancelled("已取消安装")
        result = install_local_package(
            runner,
            self._package,
            path,
            install_dir=self._vdd_dir,
            dry_run=self._dry_run,
        )
        if self._package == "vdd" and result.ok and not self._dry_run:
            result = self._after_vdd_install(runner, result)
        self.update.emit(
            PackageUpdate("done", 100, 0, 0, 0.0, result.message)
        )
        return result

    def _confirm(self, plan: PackagePlan, path: Path) -> bool:
        if self._dry_run or not self._require_confirmation:
            return True
        lines = [
            f"即将安装 {plan.display_name} {plan.release.tag}",
            f"来源：{plan.download_url}",
            f"文件：{path}",
        ]
        if plan.package == "sunshine":
            argv = install_command("sunshine", path)
            if argv is not None:
                lines.append("命令：" + _command_text(argv))
        else:
            lines.append(f"目标目录：{self._vdd_dir}")
            lines.append("安装入口：解压后自动检测 VDD.Control.exe / 安装脚本")
        lines.append("安装过程可能请求管理员权限。")
        lines.append("是否继续？")
        return self._wait_for_approval("\n".join(lines))

    def _confirm_local(self, path: Path) -> bool:
        if self._dry_run or not self._require_confirmation:
            return True
        lines = [
            "即将使用本地安装包",
            f"文件：{path}",
            "安装过程可能请求管理员权限。",
            "是否继续？",
        ]
        return self._wait_for_approval("\n".join(lines))

    def _wait_for_approval(self, message: str) -> bool:
        self._approval.clear()
        self._approved = False
        self.confirm_required.emit(message)
        while not self._approval.wait(0.2):
            if self._cancel.is_set():
                return False
        return self._approved and not self._cancel.is_set()

    def _on_chunk(self, received: int, total: int) -> None:
        now = time.monotonic()
        if self._last_time is None:
            self._last_time = now
            self._last_bytes = received
            self._last_emit = now
        else:
            elapsed = now - self._last_time
            if elapsed >= 0.2:
                instant = max(0.0, (received - self._last_bytes) / elapsed)
                self._speed = (
                    instant
                    if self._speed <= 0
                    else self._speed * 0.7 + instant * 0.3
                )
                self._last_time = now
                self._last_bytes = received
        percent = int(received * 100 / total) if total > 0 else 0
        percent = max(0, min(99, percent))
        if now - self._last_emit >= 0.1 or received == total:
            self._last_emit = now
            self.update.emit(
                PackageUpdate(
                    "download",
                    percent,
                    received,
                    total,
                    self._speed,
                    f"正在下载… {percent}%",
                )
            )


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _command_text(argv: tuple[str, ...]) -> str:
    import subprocess

    return subprocess.list2cmdline(list(argv))


class SetupWorker(QObject):
    """按计划执行全部配置写入。"""

    finished = Signal(object)
    failed = Signal(str)
    log = Signal(str)
    progress = Signal(int, str)

    def __init__(
        self,
        plan: SetupPlan,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        super().__init__()
        self._plan = plan
        self._cancel = threading.Event()
        self._sleep = sleep or time.sleep

    def request_cancel(self) -> None:
        self._cancel.set()

    def _pause(self, seconds: float, reason: str) -> bool:
        """等待显示器枚举；Dry-Run 与取消请求下不阻塞。"""
        if self._plan.dry_run:
            self.log.emit(f"[等待] Dry-Run 跳过等待 {seconds:.1f} 秒（{reason}）")
            return not self._cancel.is_set()
        self.log.emit(f"[等待] {reason}，等待 {seconds:.1f} 秒…")
        ticks = max(1, int(round(seconds / 0.2)))
        for _ in range(ticks):
            if self._cancel.is_set():
                return False
            self._sleep(0.2)
        return not self._cancel.is_set()

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
                # 配对页必须从本机 localhost 打开；局域网 IP 只用于 Moonlight 连接。
                pairing_url=(
                    pairing_url(sunshine) if sunshine is not None else pairing_url()
                ),
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
            if not vdd.installed or not vdd.device_ready or vdd.settings_path is None:
                outcomes.append(
                    StepOutcome(
                        key="vdd_detect",
                        label="虚拟显示器驱动",
                        ok=False,
                        message="VDD 驱动未就绪，或未找到 vdd_settings.xml",
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
                emit_progress(28, "重载虚拟显示器驱动")
                record("driver_reload", "重载虚拟显示器驱动", reload_driver(runner))
                if cancelled():
                    finish()
                    return
                emit_progress(34, "等待虚拟显示器驱动稳定")
                if not self._pause(2.0, "虚拟显示器驱动重载后"):
                    cancelled()
                    finish()
                    return

            # 记录重启前偏移，重启后轮询新增日志，避免双显卡机器启动慢时死等。
            log_offsets = snapshot_log_sizes(sunshine)
            emit_progress(46, "重启 Sunshine 以枚举显示器")
            restart_result = restart_service(runner, sunshine)
            record(
                "service_initial",
                "重启 Sunshine（枚举显示器）",
                restart_result,
            )
            if cancelled():
                finish()
                return

            if self._plan.dry_run:
                runner.log(
                    "[Sunshine] Dry-Run 不会重启服务，直接读取现有日志，跳过轮询"
                )
                device_id = find_virtual_display_id(runner, sunshine)
            elif not restart_result.ok:
                runner.log(
                    "[Sunshine] 首次重启失败，跳过日志轮询，改为读取现有日志"
                )
                device_id = find_virtual_display_id(runner, sunshine)
            else:
                emit_progress(54, "轮询等待 Sunshine 日志刷新")
                device_id = wait_for_virtual_display_id(
                    runner,
                    sunshine,
                    offsets=log_offsets,
                    timeout_s=10.0,
                    interval_s=0.5,
                    cancel=self._cancel,
                )
                if cancelled():
                    finish()
                    return
                if device_id is None:
                    emit_progress(62, "从完整日志中继续查找虚拟屏 device_id")
                    device_id = find_virtual_display_id(runner, sunshine)
                else:
                    runner.log(
                        f"[Sunshine] 日志轮询提取到虚拟屏 device_id：{device_id}"
                    )

            if device_id is not None:
                message = f"已从日志提取：{device_id}"
                runner.log(f"[Sunshine] 提取到虚拟屏 device_id：{device_id}")
            else:
                existing_id = configured_output_name(runner, sunshine)
                if existing_id is not None:
                    device_id = existing_id
                    message = f"未找到新设备，沿用现有 output_name：{existing_id}"
                    runner.log(
                        "[Sunshine] 未找到新的虚拟屏设备，沿用 sunshine.conf "
                        f"中已有的 output_name：{existing_id}"
                    )
                else:
                    message = "未能从日志或 dxgi-info.exe 提取设备 ID"
            outcomes.append(
                StepOutcome(
                    key="device_id",
                    label="提取虚拟屏 device_id",
                    ok=device_id is not None,
                    message=message,
                )
            )

            emit_progress(74, "写入 sunshine.conf")
            manual_resolution: tuple[int, int] | None = None
            if self._plan.resolution_mode == "manual" and self._plan.monitors:
                first = self._plan.monitors[0]
                manual_resolution = (first.width, first.height)
                runner.log(
                    "[Sunshine] 单设备固定分辨率："
                    f"dd_manual_resolution = {first.width}x{first.height}"
                )
            config_plan = build_config_plan(
                address_family=self._plan.address_family,
                output_name=device_id,
                resolution_mode=self._plan.resolution_mode,
                manual_resolution=manual_resolution,
                revert_on_disconnect=True,
            )
            result = apply_config(runner, sunshine, config_plan)
            record("sunshine_config", "写入 sunshine.conf", result)
            if result.ok:
                emit_progress(86, "再次重启 Sunshine 使配置生效")
                record("service", "重启 Sunshine 服务", restart_service(runner, sunshine))
            if cancelled():
                finish()
                return

            if self._plan.enable_firewall:
                emit_progress(92, "添加防火墙放行规则")
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
                emit_progress(96, "应用电源保活配置")
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


class UninstallWorker(QObject):
    """在 QThread 中执行一键还原与卸载，并实时回传进度与步骤结果。"""

    started = Signal()
    progress = Signal(int, str)
    step = Signal(object)            # UninstallStep
    log = Signal(str)
    finished = Signal(object)        # UninstallReport
    failed = Signal(str)

    def __init__(
        self,
        options: UninstallOptions,
        *,
        dry_run: bool = False,
        runner_factory: Callable[[bool], CommandRunner] | None = None,
    ) -> None:
        super().__init__()
        self._options = options
        self._dry_run = dry_run
        self._runner_factory = runner_factory or (
            lambda dry: CommandRunner(dry_run=dry, log_sink=self.log.emit)
        )

    def run(self) -> None:
        self.progress.emit(0, "准备清理任务…")
        self.started.emit()
        try:
            runner = self._runner_factory(self._dry_run)
            report: UninstallReport = run_uninstall(
                runner,
                self._options,
                progress=self.progress.emit,
                on_step=self.step.emit,
            )
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
            return
        self.finished.emit(report)
