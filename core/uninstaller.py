"""一键还原与卸载：按安全顺序清理本工具写入的系统配置。

执行顺序固定为：

1. ``DisplaySwitch.exe /internal`` 切回仅电脑屏幕；
2. 停止 ``SunshineService``（仅在勾选卸载 Sunshine 时）；
3. 卸载 Virtual Display Driver，必要时删除部署目录；
4. 通过 MSI ProductCode 卸载 Sunshine，并清理服务残留；
5. 删除防火墙规则；
6. 还原电源计划；
7. 清理本软件缓存目录。

所有写操作都经过 :class:`CommandRunner`，Dry-Run 下只记录命令、不产生副作用。
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from core.common import CommandRunner, OperationResult, default_state_dir
from core.downloader import DEFAULT_VDD_DIR
from core.network import remove_firewall_rule
from core.power import restore_previous_profile
from core.vdd import restore_native_display

_PRODUCT_CODE_RE = re.compile(
    r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}"
)
_VDD_WORD_RE = re.compile(r"(?<![a-z0-9])vdd(?![a-z0-9])")

ProgressSink = Callable[[int, str], None]
StepSink = Callable[["UninstallStep"], None]


class UninstallState(StrEnum):
    """单个清理步骤的结果状态。"""

    SUCCESS = "success"
    WARNING = "warning"
    SKIPPED = "skipped"
    FAILED = "failed"
    SIMULATED = "simulated"


@dataclass(frozen=True, slots=True)
class UninstallStep:
    """一个清理步骤的结果。"""

    key: str
    label: str
    state: UninstallState
    message: str
    details: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """失败以外都视为可继续；警告会保留在最终汇总中。"""
        return self.state != UninstallState.FAILED


@dataclass(frozen=True, slots=True)
class UninstallOptions:
    """用户在弹窗中勾选的清理项目。"""

    restore_display: bool = True
    restore_power: bool = True
    remove_firewall: bool = True
    uninstall_sunshine: bool = True
    uninstall_vdd: bool = True
    delete_vdd_directory: bool = True
    clean_cache: bool = True
    state_dir: Path | None = None
    vdd_install_dir: Path | None = None

    @property
    def selected_keys(self) -> tuple[str, ...]:
        keys: list[str] = []
        if self.restore_display:
            keys.append("display")
        if self.restore_power:
            keys.append("power")
        if self.remove_firewall:
            keys.append("firewall")
        if self.uninstall_sunshine:
            keys.append("sunshine")
        if self.uninstall_vdd:
            keys.append("vdd")
            if self.delete_vdd_directory:
                keys.append("vdd_directory")
        if self.clean_cache:
            keys.append("cache")
        return tuple(keys)

    @property
    def has_selection(self) -> bool:
        return bool(self.selected_keys)

    def warnings(self) -> tuple[str, ...]:
        """返回应在确认弹窗和进度页展示的安全提醒。"""
        warnings: list[str] = []
        if self.uninstall_vdd and not self.restore_display:
            warnings.append(
                "未勾选「恢复显示模式」，卸载 VDD 后当前画面可能留在虚拟屏，"
                "存在黑屏风险。"
            )
        if self.uninstall_vdd and not self.uninstall_sunshine:
            warnings.append(
                "将卸载 VDD 但保留 Sunshine；Sunshine 仍在运行时会失去虚拟屏。"
            )
        if self.clean_cache and not self.restore_power:
            warnings.append(
                "将清理缓存目录，其中可能包含 power_state.json；"
                "之后无法再自动还原电源计划。"
            )
        if self.delete_vdd_directory and not self.uninstall_vdd:
            warnings.append("未勾选卸载 VDD，删除 VDD 目录会被忽略。")
        return tuple(warnings)


@dataclass(frozen=True, slots=True)
class UninstallReport:
    """一次清理流程的完整报告。"""

    options: UninstallOptions
    steps: tuple[UninstallStep, ...]
    dry_run: bool

    @property
    def failed_steps(self) -> tuple[UninstallStep, ...]:
        return tuple(step for step in self.steps if step.state == UninstallState.FAILED)

    @property
    def warning_steps(self) -> tuple[UninstallStep, ...]:
        return tuple(step for step in self.steps if step.state == UninstallState.WARNING)

    @property
    def ok(self) -> bool:
        return not self.failed_steps

    @property
    def summary(self) -> str:
        if not self.steps:
            return "未选择任何清理项目"
        if self.failed_steps:
            return f"清理完成：{len(self.failed_steps)} 项失败，请查看日志"
        if self.warning_steps:
            return f"清理完成：{len(self.warning_steps)} 项需要留意"
        if self.dry_run:
            return "Dry-Run 完成：仅记录命令，未修改系统"
        return "清理完成：所选项目均已处理"


def run_uninstall(
    runner: CommandRunner,
    options: UninstallOptions,
    *,
    progress: ProgressSink | None = None,
    on_step: StepSink | None = None,
) -> UninstallReport:
    """按固定安全顺序执行清理，并实时回调进度与步骤结果。"""
    if not options.has_selection:
        return UninstallReport(options, (), runner.dry_run)

    state_dir = (
        Path(options.state_dir)
        if options.state_dir is not None
        else default_state_dir()
    )
    vdd_dir = (
        Path(options.vdd_install_dir)
        if options.vdd_install_dir is not None
        else DEFAULT_VDD_DIR
    )

    total = _planned_step_count(options)
    completed = 0
    steps: list[UninstallStep] = []

    def begin(label: str) -> None:
        nonlocal completed
        if progress is not None:
            percent = int(completed * 100 / total) if total else 0
            progress(percent, f"正在{label}…")
        completed += 1

    def record(step: UninstallStep) -> None:
        steps.append(step)
        if on_step is not None:
            # UI 会同时接收命令日志和结构化步骤结果，避免同一行打印两次。
            on_step(step)
            return
        runner.log(f"[清理] {step.label}：{step.message}")
        for detail in step.details:
            if detail:
                runner.log(f"[清理]   {detail}")

    display_step: UninstallStep | None = None
    stop_step: UninstallStep | None = None
    vdd_step: UninstallStep | None = None
    power_step: UninstallStep | None = None
    vdd_skipped_for_safety = False

    # 1. 必须先切回主屏，再卸载虚拟显示器。
    if options.restore_display:
        begin("切回仅电脑屏幕")
        display_step = _operation_step(
            "display",
            "切回仅电脑屏幕",
            restore_native_display(runner),
        )
        record(display_step)

    # 2. 卸载 Sunshine 时，先停止服务，避免 VDD 卸载时仍被占用。
    if options.uninstall_sunshine:
        begin("停止 Sunshine 服务")
        stop_step = _stop_sunshine_service(runner)
        record(stop_step)

    # 3. 卸载 VDD。若主屏切换失败或服务未停止，跳过后面的 VDD 步骤。
    if options.uninstall_vdd:
        skip_reason: str | None = None
        if (
            display_step is not None
            and display_step.state == UninstallState.FAILED
        ):
            skip_reason = "切回主屏失败，为避免黑屏已跳过 VDD 卸载"
        elif stop_step is not None and stop_step.state == UninstallState.FAILED:
            skip_reason = "SunshineService 未能停止，为避免驱动占用已跳过 VDD 卸载"

        begin("卸载虚拟显示器驱动")
        if skip_reason is not None:
            vdd_skipped_for_safety = True
            vdd_step = UninstallStep(
                "vdd",
                "卸载虚拟显示器驱动",
                UninstallState.SKIPPED,
                skip_reason,
            )
        else:
            vdd_step = _uninstall_vdd(runner)
        record(vdd_step)

        if options.delete_vdd_directory:
            begin("删除 VDD 目录")
            if vdd_skipped_for_safety:
                record(
                    UninstallStep(
                        "vdd_directory",
                        "删除 VDD 目录",
                        UninstallState.SKIPPED,
                        "VDD 卸载因安全原因跳过，已保留部署目录",
                        details=(f"目录: {vdd_dir}",),
                    )
                )
            elif vdd_step.state in {
                UninstallState.FAILED,
                UninstallState.WARNING,
            }:
                record(
                    UninstallStep(
                        "vdd_directory",
                        "删除 VDD 目录",
                        UninstallState.SKIPPED,
                        "VDD 驱动未完全卸载，已跳过目录删除",
                        details=(f"目录: {vdd_dir}",),
                    )
                )
            else:
                record(_remove_vdd_directory(runner, vdd_dir))

    # 4. 卸载 Sunshine，并清理可能残留的失效服务。
    if options.uninstall_sunshine:
        begin("卸载 Sunshine")
        record(_uninstall_sunshine(runner))

    # 5. 删除防火墙规则。
    if options.remove_firewall:
        begin("移除防火墙规则")
        record(
            _operation_step(
                "firewall",
                "移除防火墙规则",
                remove_firewall_rule(runner),
            )
        )

    # 6. 还原电源计划并删除备份文件。
    if options.restore_power:
        begin("还原电源计划")
        power_step = _restore_power(runner, state_dir / "power_state.json")
        record(power_step)

    # 7. 最后清理缓存目录，避免提前删掉仍需要的回滚清单与日志。
    if options.clean_cache:
        begin("清理本软件缓存")
        if (
            power_step is not None
            and power_step.state == UninstallState.FAILED
        ):
            record(
                UninstallStep(
                    "cache",
                    "清理本软件缓存",
                    UninstallState.SKIPPED,
                    "电源计划还原失败，已保留缓存与 power_state.json 以便手动恢复",
                    details=(f"目录: {state_dir}",),
                )
            )
        else:
            record(_clean_cache(runner, state_dir))

    if progress is not None:
        progress(100, "清理流程结束")
    return UninstallReport(options, tuple(steps), runner.dry_run)


# ----------------------------------------------------------------------
# 各清理步骤
# ----------------------------------------------------------------------


def _stop_sunshine_service(runner: CommandRunner) -> UninstallStep:
    label = "停止 Sunshine 服务"
    runner.log("[清理] 停止 SunshineService（若存在）")
    result = runner.mutate_powershell(_STOP_SERVICE_SCRIPT, timeout_s=45)
    if result.simulated:
        return UninstallStep(
            "sunshine_stop",
            label,
            UninstallState.SIMULATED,
            "Dry-Run：将停止 SunshineService 服务",
        )
    output = (result.stdout or "").strip().upper()
    if "NOT_INSTALLED" in output:
        return UninstallStep(
            "sunshine_stop",
            label,
            UninstallState.SKIPPED,
            "未检测到 SunshineService，跳过停止",
        )
    if not result.ok:
        return UninstallStep(
            "sunshine_stop",
            label,
            UninstallState.FAILED,
            "停止 SunshineService 失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return UninstallStep(
        "sunshine_stop",
        label,
        UninstallState.SUCCESS,
        "SunshineService 已停止",
    )


def _uninstall_vdd(runner: CommandRunner) -> UninstallStep:
    label = "卸载虚拟显示器驱动"
    devices, error = _query_virtual_display_devices(runner)
    if error is not None:
        return UninstallStep(
            "vdd",
            label,
            UninstallState.FAILED,
            "读取虚拟显示器设备失败",
            details=(error,),
        )
    if not devices:
        return UninstallStep(
            "vdd",
            label,
            UninstallState.SKIPPED,
            "未检测到 Virtual Display Driver 设备",
        )

    details: list[str] = []
    failures: list[str] = []
    simulated = False
    removed = 0
    for device in devices:
        name = str(device.get("FriendlyName") or "虚拟显示器").strip()
        instance_id = str(device.get("InstanceId") or "").strip()
        if not instance_id:
            failures.append(f"{name}: 缺少 InstanceId，无法卸载")
            continue
        result = runner.mutate(
            ("pnputil.exe", "/remove-device", instance_id),
            timeout_s=120,
        )
        simulated = simulated or result.simulated
        if result.simulated:
            details.append(f"将移除 {name}（{instance_id}）")
        elif result.ok:
            removed += 1
            details.append(f"已移除 {name}（{instance_id}）")
        else:
            failures.append(
                f"{name}: {_tail(result.stderr or result.stdout)}"
            )

    if simulated:
        return UninstallStep(
            "vdd",
            label,
            UninstallState.SIMULATED,
            f"Dry-Run：将卸载 {len(devices)} 个虚拟显示设备",
            details=tuple(details),
        )
    if failures:
        state = (
            UninstallState.FAILED
            if removed == 0
            else UninstallState.WARNING
        )
        return UninstallStep(
            "vdd",
            label,
            state,
            f"已卸载 {removed} 个虚拟显示设备，{len(failures)} 个失败",
            details=tuple(
                details
                + failures
                + ["可打开 VDD Control → System → Uninstall Driver 手动完成"]
            ),
        )
    return UninstallStep(
        "vdd",
        label,
        UninstallState.SUCCESS,
        f"已卸载 {removed} 个虚拟显示设备",
        details=tuple(
            details
            + ["如设备管理器中仍显示虚拟显示器，请重启后再检查"]
        ),
    )


def _uninstall_sunshine(runner: CommandRunner) -> UninstallStep:
    label = "卸载 Sunshine"
    product_code, app, error = _query_sunshine_product(runner)
    if error is not None:
        return UninstallStep(
            "sunshine",
            label,
            UninstallState.FAILED,
            "读取 Sunshine 注册表卸载信息失败",
            details=(error,),
        )

    if product_code is None:
        cleanup_state, cleanup_message, cleanup_details = _cleanup_sunshine_service(
            runner
        )
        if cleanup_state == UninstallState.SKIPPED:
            return UninstallStep(
                "sunshine",
                label,
                UninstallState.SKIPPED,
                "未检测到 Sunshine 安装记录或服务残留",
            )
        if cleanup_state == UninstallState.FAILED:
            return UninstallStep(
                "sunshine",
                label,
                UninstallState.FAILED,
                "未找到 MSI ProductCode，且服务残留清理失败",
                details=cleanup_details,
            )
        if cleanup_state == UninstallState.SIMULATED:
            return UninstallStep(
                "sunshine",
                label,
                UninstallState.SIMULATED,
                "Dry-Run：未找到 MSI ProductCode，将清理服务残留",
            )
        return UninstallStep(
            "sunshine",
            label,
            UninstallState.WARNING,
            "未找到 MSI ProductCode，已清理残留服务；安装目录可能需要手动删除",
            details=cleanup_details,
        )

    display_name = str((app or {}).get("DisplayName") or "Sunshine")
    version = str((app or {}).get("DisplayVersion") or "").strip()
    result = runner.mutate(
        ("msiexec", "/x", product_code, "/passive", "/norestart"),
        timeout_s=600,
    )
    if result.simulated:
        return UninstallStep(
            "sunshine",
            label,
            UninstallState.SIMULATED,
            f"Dry-Run：将静默卸载 {display_name}",
            details=(
                f"ProductCode: {product_code}",
                "随后清理 SunshineService 服务残留",
            ),
        )

    if result.returncode == 1605:
        cleanup_state, cleanup_message, cleanup_details = _cleanup_sunshine_service(
            runner
        )
        if cleanup_state == UninstallState.FAILED:
            state = UninstallState.WARNING
            message = "Sunshine 已不在系统中，但 SunshineService 残留清理失败"
        else:
            state = UninstallState.SKIPPED
            message = "Sunshine 已不在系统中，无需再次卸载"
        return UninstallStep(
            "sunshine",
            label,
            state,
            message,
            details=(
                "MSI 返回 1605（产品未安装）",
                cleanup_message,
                *cleanup_details,
            ),
        )

    if result.returncode not in (0, 3010):
        return UninstallStep(
            "sunshine",
            label,
            UninstallState.FAILED,
            f"Sunshine 卸载失败（msiexec 退出码 {result.returncode}）",
            details=(
                f"{display_name}{' ' + version if version else ''}",
                _tail(result.stderr or result.stdout),
            ),
        )

    cleanup_state, cleanup_message, cleanup_details = _cleanup_sunshine_service(
        runner
    )
    message = "Sunshine 已卸载"
    if result.returncode == 3010:
        message += "，需要重启完成清理"
    if cleanup_state == UninstallState.FAILED:
        message += "，但 SunshineService 残留清理失败"
        state = UninstallState.WARNING
    elif cleanup_state == UninstallState.SUCCESS:
        message += "，SunshineService 残留已清理"
        state = UninstallState.SUCCESS
    elif cleanup_state == UninstallState.SKIPPED:
        message += "，SunshineService 已不存在"
        state = UninstallState.SUCCESS
    else:
        state = UninstallState.WARNING
    return UninstallStep(
        "sunshine",
        label,
        state,
        message,
        details=(f"{display_name}{' ' + version if version else ''}", cleanup_message),
    )


def _cleanup_sunshine_service(
    runner: CommandRunner,
) -> tuple[UninstallState, str, tuple[str, ...]]:
    runner.log("[清理] 删除 SunshineService 服务残留（若存在）")
    result = runner.mutate_powershell(_CLEANUP_SERVICE_SCRIPT, timeout_s=45)
    if result.simulated:
        return (
            UninstallState.SIMULATED,
            "Dry-Run：将清理 SunshineService 服务残留",
            (),
        )
    output = (result.stdout or "").strip().upper()
    if "MISSING" in output:
        return (UninstallState.SKIPPED, "SunshineService 已不存在", ())
    if not result.ok:
        return (
            UninstallState.FAILED,
            "SunshineService 服务残留清理失败",
            (_tail(result.stderr or result.stdout),),
        )
    return (UninstallState.SUCCESS, "SunshineService 服务残留已删除", ())


def _restore_power(runner: CommandRunner, state_path: Path) -> UninstallStep:
    label = "还原电源计划"
    if not state_path.is_file():
        return UninstallStep(
            "power",
            label,
            UninstallState.SKIPPED,
            "未找到 power_state.json，无需还原",
            details=(f"预期位置: {state_path}",),
        )
    result = restore_previous_profile(runner, state_path=state_path)
    return _operation_step("power", label, result)


def _remove_vdd_directory(runner: CommandRunner, path: Path) -> UninstallStep:
    label = "删除 VDD 目录"
    if not _is_safe_cleanup_path(path):
        return UninstallStep(
            "vdd_directory",
            label,
            UninstallState.FAILED,
            "拒绝删除不安全的路径",
            details=(f"路径: {path}",),
        )
    if not path.exists():
        return UninstallStep(
            "vdd_directory",
            label,
            UninstallState.SKIPPED,
            f"{path} 不存在，无需删除",
        )
    if not _looks_like_vdd_directory(path):
        return UninstallStep(
            "vdd_directory",
            label,
            UninstallState.FAILED,
            "目录中未找到 VDD 标志文件，已拒绝删除",
            details=(f"路径: {path}",),
        )
    if runner.dry_run:
        runner.log(f"[DRY-RUN] 将删除目录: {path}")
        return UninstallStep(
            "vdd_directory",
            label,
            UninstallState.SIMULATED,
            f"Dry-Run：将删除 {path}",
        )
    try:
        shutil.rmtree(path)
    except OSError as exc:
        return UninstallStep(
            "vdd_directory",
            label,
            UninstallState.FAILED,
            f"删除 {path} 失败",
            details=(str(exc),),
        )
    return UninstallStep(
        "vdd_directory",
        label,
        UninstallState.SUCCESS,
        f"已删除 {path}",
    )


def _clean_cache(runner: CommandRunner, state_dir: Path) -> UninstallStep:
    label = "清理本软件缓存"
    if not _is_safe_cleanup_path(state_dir):
        return UninstallStep(
            "cache",
            label,
            UninstallState.FAILED,
            "拒绝删除不安全的缓存路径",
            details=(f"路径: {state_dir}",),
        )
    if state_dir.name.casefold() != "campusstreamhelper":
        return UninstallStep(
            "cache",
            label,
            UninstallState.FAILED,
            "目录名不是 CampusStreamHelper，已拒绝清理",
            details=(f"路径: {state_dir}",),
        )
    if not state_dir.exists():
        return UninstallStep(
            "cache",
            label,
            UninstallState.SKIPPED,
            f"{state_dir} 不存在，无需清理",
        )

    try:
        entries = list(state_dir.iterdir())
    except OSError as exc:
        return UninstallStep(
            "cache",
            label,
            UninstallState.FAILED,
            "读取缓存目录失败",
            details=(str(exc),),
        )

    names = tuple(entry.name for entry in entries)
    if runner.dry_run:
        runner.log(f"[DRY-RUN] 将清理缓存目录: {state_dir}")
        return UninstallStep(
            "cache",
            label,
            UninstallState.SIMULATED,
            f"Dry-Run：将清理 {state_dir}（{len(entries)} 项）",
            details=names,
        )

    removed = 0
    failures: list[str] = []
    for entry in entries:
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed += 1
        except OSError as exc:
            failures.append(f"{entry.name}: {exc}")

    try:
        state_dir.rmdir()
    except OSError:
        pass

    if failures:
        state = (
            UninstallState.FAILED
            if removed == 0
            else UninstallState.WARNING
        )
        return UninstallStep(
            "cache",
            label,
            state,
            f"已清理 {removed} 项，{len(failures)} 项失败",
            details=tuple(failures),
        )
    return UninstallStep(
        "cache",
        label,
        UninstallState.SUCCESS,
        f"已清理缓存目录（{removed} 项）",
        details=names,
    )


# ----------------------------------------------------------------------
# 查询与工具
# ----------------------------------------------------------------------


def _query_sunshine_product(
    runner: CommandRunner,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    result = runner.query_powershell(_SUNSHINE_PRODUCT_SCRIPT, timeout_s=20)
    if not result.ok:
        return None, None, _tail(result.stderr or result.stdout)
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None, None, "注册表查询返回的不是有效 JSON"
    if not isinstance(payload, dict):
        return None, None, "注册表查询返回结构异常"

    apps = [
        app
        for app in _as_dict_list(payload.get("apps"))
        if _is_sunshine_app(app)
    ]
    if not apps:
        return None, None, None
    for app in apps:
        candidates = (
            app.get("PSChildName"),
            app.get("UninstallString"),
            app.get("DisplayName"),
        )
        for candidate in candidates:
            match = _PRODUCT_CODE_RE.search(str(candidate or ""))
            if match:
                return match.group(0), app, None
    return None, apps[0], None


def _query_virtual_display_devices(
    runner: CommandRunner,
) -> tuple[list[dict[str, Any]], str | None]:
    result = runner.query_powershell(_VDD_DEVICES_SCRIPT, timeout_s=20)
    if not result.ok:
        return [], _tail(result.stderr or result.stdout)
    text = (result.stdout or "").strip()
    if not text:
        return [], None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return [], "PnP 查询返回的不是有效 JSON"
    devices = [
        device
        for device in _as_dict_list(payload)
        if _is_vdd_device_name(str(device.get("FriendlyName") or ""))
    ]
    return devices, None


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _operation_step(
    key: str,
    label: str,
    result: OperationResult,
) -> UninstallStep:
    if result.simulated:
        state = UninstallState.SIMULATED
    elif result.ok:
        state = UninstallState.SUCCESS
    else:
        state = UninstallState.FAILED
    return UninstallStep(
        key,
        label,
        state,
        result.message,
        details=result.details,
    )


def _planned_step_count(options: UninstallOptions) -> int:
    total = 0
    if options.restore_display:
        total += 1
    if options.uninstall_sunshine:
        total += 2  # 停止服务 + 卸载
    if options.uninstall_vdd:
        total += 1
        if options.delete_vdd_directory:
            total += 1
    if options.remove_firewall:
        total += 1
    if options.restore_power:
        total += 1
    if options.clean_cache:
        total += 1
    return total


def _is_safe_cleanup_path(path: Path) -> bool:
    """拒绝删除磁盘根目录、用户主目录或当前工作目录。"""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    if resolved == Path(resolved.anchor):
        return False
    if resolved == Path.home().resolve():
        return False
    try:
        if resolved == Path.cwd().resolve():
            return False
    except OSError:
        return False
    return len(resolved.parts) >= 2


def _is_sunshine_app(app: dict[str, Any]) -> bool:
    """避免把名字里带 Sunshine 的无关软件当成 Sunshine 卸载。"""
    name = str(app.get("DisplayName") or "").strip().casefold()
    if "sunshine" not in name:
        return False
    publisher = str(app.get("Publisher") or "").strip().casefold()
    location = str(app.get("InstallLocation") or "").strip().casefold()
    if name == "sunshine":
        return True
    if "lizardbyte" in publisher:
        return True
    return name.startswith("sunshine") and "sunshine" in location


def _is_vdd_device_name(name: str) -> bool:
    """只匹配 Virtual Display Driver 自身，避免误删其他虚拟显示器。

    例如 ``GameViewer Virtual Display Adapter`` 也含有 "Virtual Display"，
    但不是本工具安装的 VDD，不能出现在卸载列表中。
    """
    lowered = name.casefold().strip()
    if not lowered or "gameviewer" in lowered:
        return False
    return (
        "virtual display driver" in lowered
        or "virtualdisplaydriver" in lowered
        or "virtualdrivers" in lowered
        or bool(_VDD_WORD_RE.search(lowered))
    )


def _looks_like_vdd_directory(path: Path) -> bool:
    """只有目录名或标志文件符合 VDD 部署目录时才允许递归删除。"""
    if path.name.casefold() in {"virtualdisplaydriver", "virtual display driver"}:
        return True
    if (path / "vdd_settings.xml").is_file():
        return True
    if (path / "VDD.Control.exe").is_file():
        return True
    try:
        return any(
            item.suffix.casefold() == ".inf"
            and "vdd" in item.name.casefold()
            for item in path.iterdir()
            if item.is_file()
        )
    except OSError:
        return False


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in str(text or "").strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]


_STOP_SERVICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$svc = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue
if (-not $svc) { 'NOT_INSTALLED'; exit 0 }
if ($svc.Status -ne 'Stopped') {
    Stop-Service -Name 'SunshineService' -Force -ErrorAction Stop
}
$deadline = (Get-Date).AddSeconds(15)
while ($svc.Status -ne 'Stopped' -and (Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $svc.Refresh()
}
if ($svc.Status -ne 'Stopped') {
    throw ('服务状态仍为 ' + $svc.Status.ToString())
}
'STOPPED'
""".strip()


_CLEANUP_SERVICE_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$svc = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue
if (-not $svc) { 'MISSING'; exit 0 }
if ($svc.Status -ne 'Stopped') {
    Stop-Service -Name 'SunshineService' -Force -ErrorAction Stop
}
$output = sc.exe delete 'SunshineService' 2>&1
if ($LASTEXITCODE -ne 0) { throw ($output | Out-String) }
'REMOVED'
""".strip()


_SUNSHINE_PRODUCT_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$paths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
$apps = @(Get-ItemProperty -Path $paths |
    Where-Object { $_.DisplayName -like '*Sunshine*' } |
    Select-Object DisplayName, DisplayVersion, Publisher, InstallLocation, PSChildName, UninstallString)
[pscustomobject]@{ apps = $apps } |
    ConvertTo-Json -Depth 4 -Compress
""".strip()


_VDD_DEVICES_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
$devices = @(Get-PnpDevice -Class Display |
    Select-Object FriendlyName, InstanceId, Status)
if ($devices.Count -eq 0) { '[]' } else { $devices | ConvertTo-Json -Depth 4 -Compress }
""".strip()
