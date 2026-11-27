"""模块 C：Sunshine 检测、虚拟屏 ``device_id`` 提取与 ``sunshine.conf`` 自动配置。"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping, Sequence

from core.common import CommandRunner, OperationResult

AddressFamily = Literal["ipv4", "ipv6", "both"]
# manual: 固定为 Step 2 选择的分辨率；auto: 跟随 Moonlight 客户端请求。
ResolutionMode = Literal["manual", "auto"]
DisplayConfiguration = Literal[
    "disabled",
    "verify_only",
    "ensure_active",
    "ensure_primary",
    "ensure_only_display",
]
ServiceState = Literal[
    "running",
    "stopped",
    "start_pending",
    "stop_pending",
    "not_installed",
    "unknown",
]

DEFAULT_SERVICE_NAME = "SunshineService"
DEFAULT_INSTALL_DIR = Path(r"C:\Program Files\Sunshine")
CONFIG_FILENAME = "sunshine.conf"
LOG_FILENAME = "sunshine.log"
PAIRING_PORT = 47990

# 以下键名已对照 Sunshine 官方 configuration.md（Windows 专用 dd_* 选项）。
KEY_ADDRESS_FAMILY = "address_family"
KEY_OUTPUT_NAME = "output_name"
KEY_DD_CONFIGURATION = "dd_configuration_option"
KEY_DD_REVERT = "dd_config_revert_on_disconnect"
KEY_DD_RESOLUTION = "dd_resolution_option"
KEY_DD_MANUAL_RESOLUTION = "dd_manual_resolution"
KEY_DD_REFRESH_RATE = "dd_refresh_rate_option"

_GUID_RE = re.compile(
    r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}"
)
_BARE_GUID_RE = re.compile(
    r"(?<!\{)[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?!\})"
)
_VIRTUAL_DISPLAY_KEYWORDS = ("vdd", "mtt", "virtual", "indirect", "idd")
_DEVICE_ID_KEY_RE = re.compile(r'"device_id"\s*:', re.IGNORECASE)
_STARTUP_VERSION_RE = re.compile(r"Sunshine version:", re.IGNORECASE)
_STARTUP_TRAY_RE = re.compile(r"Starting system tray", re.IGNORECASE)
_DISPLAY_PNP_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-PnpDevice -Class Display |
    Select-Object Status, FriendlyName, InstanceId |
    Format-Table -AutoSize |
    Out-String -Width 300
""".strip()
_CONFIG_LINE_RE = re.compile(
    r"^(?P<indent>\s*)(?P<key>[A-Za-z0-9_.-]+)\s*=\s*(?P<value>.*)$"
)


@dataclass(frozen=True, slots=True)
class SunshineStatus:
    """Sunshine 安装、服务与配置文件状态。"""

    installed: bool
    install_dir: Path | None
    executable: Path | None
    config_path: Path | None
    service_name: str
    service_state: ServiceState
    version: str | None
    detail: str
    # 国内杀软可能在 MSI 安装阶段误删 tools\sunshinesvc.exe（Error 1310）。
    sunshinesvc_path: Path | None = None
    sunshinesvc_exists: bool = True
    service_image_path: Path | None = None
    service_image_exists: bool = True

    @property
    def service_binary_missing(self) -> bool:
        """服务文件缺失，或服务 ImagePath 指向的文件不存在。"""
        if self.executable is None:
            return False
        if self.sunshinesvc_path is not None and not self.sunshinesvc_exists:
            return True
        if self.service_image_path is not None and not self.service_image_exists:
            return True
        return False


@dataclass(frozen=True, slots=True)
class SunshineConfigState:
    """``sunshine.conf`` 的当前内容快照。"""

    config_path: Path
    values: Mapping[str, str]
    raw_lines: tuple[str, ...]
    newline: str = "\n"


@dataclass(frozen=True, slots=True)
class SunshineConfigPlan:
    """要写入 ``sunshine.conf`` 的目标配置。

    键值映射（Phase 2 直接落盘）：
    - ``address_family`` → ``address_family``（ipv4 / ipv6 / both）
    - ``output_name`` → ``output_name``
    - ``display_configuration`` → ``dd_configuration_option``
    - ``revert_on_disconnect`` → ``dd_config_revert_on_disconnect = enabled|disabled``
    - ``resolution_mode=manual`` → ``dd_resolution_option=manual``、
      ``dd_manual_resolution=<宽>x<高>``、``dd_refresh_rate_option=disabled``
    - ``resolution_mode=auto`` → ``dd_resolution_option=auto``、
      ``dd_refresh_rate_option=auto``
    """

    address_family: AddressFamily = "ipv4"
    output_name: str | None = None
    display_configuration: DisplayConfiguration = "ensure_only_display"
    revert_on_disconnect: bool = True
    resolution_mode: ResolutionMode = "manual"
    manual_resolution: tuple[int, int] | None = None


def detect_sunshine(
    runner: CommandRunner,
    *,
    search_roots: Sequence[Path] = (),
) -> SunshineStatus:
    """检测 Sunshine 安装路径、版本、配置文件与服务状态。"""
    registry = _query_registry_and_service(runner)
    apps = _as_dict_list(registry.get("apps"))
    service = registry.get("service")

    roots = _candidate_roots(search_roots, apps)
    executable: Path | None = None
    install_dir: Path | None = None
    for root in roots:
        candidate = root / "sunshine.exe"
        if candidate.is_file():
            executable = candidate
            install_dir = root
            break
    if install_dir is None:
        for root in roots:
            if root.is_dir():
                install_dir = root
                break

    config_path: Path | None = None
    for root in _dedupe_paths([install_dir, *roots]):
        if root is None:
            continue
        for candidate in (root / "config" / CONFIG_FILENAME, root / CONFIG_FILENAME):
            if candidate.is_file():
                config_path = candidate
                break
        if config_path is not None:
            break
    if config_path is None and install_dir is not None:
        config_path = install_dir / "config" / CONFIG_FILENAME

    service_name = DEFAULT_SERVICE_NAME
    service_image_path: Path | None = None
    if isinstance(service, dict):
        service_name = str(service.get("Name") or DEFAULT_SERVICE_NAME)
        service_state = _normalize_service_state(service.get("Status"))
        service_image_path = _parse_service_image_path(service.get("ImagePath"))
    else:
        service_state = "not_installed"

    version: str | None = None
    if apps:
        version = str(apps[0].get("DisplayVersion") or "") or None

    sunshinesvc_path = (
        install_dir / "tools" / "sunshinesvc.exe" if install_dir is not None else None
    )
    sunshinesvc_exists = bool(sunshinesvc_path and sunshinesvc_path.is_file())
    service_image_exists = bool(service_image_path and service_image_path.is_file())

    installed = executable is not None or (install_dir is not None and install_dir.is_dir())
    if installed and config_path is not None and not config_path.exists():
        config_detail = f"配置文件预期位置 {config_path}（当前不存在）"
    elif config_path is not None:
        config_detail = f"配置文件 {config_path}"
    else:
        config_detail = "未找到 sunshine.conf"

    if not installed:
        detail = "未检测到 Sunshine，请先安装。"
    else:
        detail = (
            f"安装目录 {install_dir or '未知'}；服务 {service_name}（{service_state}）；"
            f"{config_detail}"
        )
        if version:
            detail += f"；版本 {version}"
        detail += (
            f"；服务程序 {sunshinesvc_path or '未知'}"
            f"（{'存在' if sunshinesvc_exists else '缺失'}）"
        )
        if service_image_path is not None:
            detail += (
                f"；ImagePath {service_image_path}"
                f"（{'存在' if service_image_exists else '缺失'}）"
            )
    runner.log(f"[Sunshine] {detail}")

    return SunshineStatus(
        installed=installed,
        install_dir=install_dir,
        executable=executable,
        config_path=config_path,
        service_name=service_name,
        service_state=service_state,
        version=version,
        detail=detail,
        sunshinesvc_path=sunshinesvc_path,
        sunshinesvc_exists=sunshinesvc_exists,
        service_image_path=service_image_path,
        service_image_exists=service_image_exists,
    )


def read_config(runner: CommandRunner, config_path: Path) -> SunshineConfigState:
    """解析 ``key = value`` 配置，保留注释与原始行顺序。"""
    text = runner.read_text(config_path, encoding="utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    values: dict[str, str] = {}
    for line in lines:
        match = _CONFIG_LINE_RE.match(line)
        if not match:
            continue
        key = match.group("key").strip()
        value = _strip_inline_comment(match.group("value").strip())
        if key:
            values[key] = value
    return SunshineConfigState(
        config_path=Path(config_path),
        values=values,
        raw_lines=tuple(lines),
        newline=newline,
    )


def find_virtual_display_id(
    runner: CommandRunner,
    status: SunshineStatus,
    *,
    log_path: Path | None = None,
) -> str | None:
    """提取虚拟屏永久 ``{device_id}``。

    优先从 Sunshine 日志末尾向前解析显示器 JSON 块，并按虚拟屏关键词选择；
    日志不可用时再用 ``dxgi-info.exe`` 兜底。返回值始终保留花括号。
    """
    candidates = _log_candidates(status, log_path)
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if existing:
        for candidate in existing:
            runner.log(f"[Sunshine] 检查日志文件：{candidate}")
    else:
        runner.log(
            "[Sunshine] 未找到 Sunshine 日志，已检查："
            + " | ".join(str(candidate) for candidate in candidates)
        )

    fallback_id: str | None = None
    fallback_source: str | None = None
    for candidate in existing:
        try:
            text = _read_tail(candidate)
        except OSError as exc:
            runner.log(f"[Sunshine] 读取日志失败 {candidate}: {exc}")
            continue

        latest = _latest_startup_text(text)
        devices = _scan_display_devices(latest)
        if not devices:
            runner.log(
                f"[Sunshine] 最新启动后的日志中没有解析到包含 "
                f"device_id/display_name/friendly_name 的对象：{candidate}"
            )
            legacy = _best_guid_from_text(latest)
            if legacy is not None:
                runner.log(
                    f"[Sunshine] 从旧格式日志行提取虚拟屏 ID：{legacy}"
                    f"（{candidate.name}）"
                )
                return legacy
            continue

        runner.log(
            f"[Sunshine] 从日志解析到 {len(devices)} 个显示器对象"
            f"（{candidate.name}，按最近出现优先）："
        )
        _log_devices(runner, devices)
        matched, reason = _match_virtual_device(devices)
        if matched is not None:
            device_id = _device_id(matched)
            if device_id:
                runner.log(
                    f"[Sunshine] 从日志选择虚拟屏 ID：{device_id}（{reason}）"
                )
                return device_id
        if fallback_id is None:
            candidate_device = _fallback_display_device(devices)
            candidate_id = (
                _device_id(candidate_device) if candidate_device else None
            )
            if candidate_id:
                fallback_id = candidate_id
                fallback_source = candidate.name

    if fallback_id is not None:
        runner.log(
            f"[Sunshine] 未命中虚拟屏关键词，选择非主屏候选：{fallback_id}"
            f"（{fallback_source}）"
        )
        return fallback_id

    dxgi = _find_dxgi_info(status)
    if dxgi is not None:
        runner.log("[Sunshine] 日志中未提取到虚拟屏，尝试 dxgi-info.exe…")
        result = runner.query([str(dxgi)], timeout_s=20)
        if result.ok:
            runner.log("[Sunshine] dxgi-info.exe 原始显示器输出：\n" + result.stdout.strip())
            found = _best_guid_from_text(result.stdout)
            if found is not None:
                runner.log(f"[Sunshine] 从 dxgi-info.exe 提取虚拟屏 ID: {found}")
                return found
            runner.log(
                "[Sunshine] dxgi-info.exe 输出中没有 device_id；"
                "若当前是「仅电脑屏幕」，虚拟屏可能未激活。"
            )
        else:
            runner.log(
                f"[Sunshine] dxgi-info.exe 执行失败: "
                f"{_tail(result.stderr or result.stdout)}"
            )

    runner.log("[Sunshine] 未能提取虚拟屏 device_id，将保留现有 output_name")
    return None


def configured_output_name(
    runner: CommandRunner,
    status: SunshineStatus,
) -> str | None:
    """读取 ``sunshine.conf`` 中已有的有效 ``output_name``。

    用于本次未找到新设备时沿用旧值，避免把用户手工配置清空。
    """
    config_path = status.config_path
    if config_path is None or not Path(config_path).is_file():
        return None
    try:
        state = read_config(runner, Path(config_path))
    except (OSError, UnicodeError) as exc:
        runner.log(f"[Sunshine] 读取现有 sunshine.conf 失败：{exc}")
        return None
    value = str(state.values.get(KEY_OUTPUT_NAME, "")).strip()
    if not value:
        return None
    device_id = _normalize_device_id(value)
    if device_id is None:
        runner.log(f"[Sunshine] 现有 output_name 不是有效 GUID，忽略：{value}")
    return device_id


def snapshot_log_sizes(
    status: SunshineStatus,
    *,
    log_path: Path | None = None,
) -> dict[Path, int]:
    """重启服务前记录日志大小，作为轮询新增内容的起始偏移。"""
    sizes: dict[Path, int] = {}
    for candidate in _log_candidates(status, log_path):
        try:
            sizes[candidate] = candidate.stat().st_size
        except OSError:
            sizes[candidate] = 0
    return sizes


def wait_for_virtual_display_id(
    runner: CommandRunner,
    status: SunshineStatus,
    *,
    offsets: Mapping[Path, int],
    timeout_s: float = 10.0,
    interval_s: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    cancel: threading.Event | None = None,
    log_path: Path | None = None,
) -> str | None:
    """轮询重启后新增的日志，命中虚拟屏后立即返回。

    Args:
        offsets: ``snapshot_log_sizes`` 在重启前记录的文件大小。
        timeout_s: 最长等待秒数。
        interval_s: 轮询间隔秒数。
        sleep: 可注入的等待函数，便于测试。
        cancel: 用户取消事件。
    """
    candidates = _log_candidates(status, log_path)
    interval = max(0.05, interval_s)
    attempts = max(1, int(timeout_s / interval) + 1)
    runner.log(
        f"[Sunshine] 轮询等待日志刷新（最长 {timeout_s:.0f} 秒，"
        f"每 {interval:.1f} 秒检查）…"
    )
    for candidate in candidates:
        runner.log(
            f"[Sunshine] 轮询日志：{candidate}（起始偏移 {offsets.get(candidate, 0)}）"
        )

    saw_device_key = False
    for attempt in range(1, attempts + 1):
        if cancel is not None and cancel.is_set():
            runner.log("[Sunshine] 等待日志刷新已取消")
            return None
        for candidate in candidates:
            try:
                size = candidate.stat().st_size
            except OSError:
                continue
            start = offsets.get(candidate, 0)
            if size < start:
                # 日志被轮转或截断，从头读取当前文件。
                start = 0
            if size <= start:
                continue
            try:
                new_text = _read_range(candidate, start, size).decode(
                    "utf-8", errors="replace"
                )
            except OSError as exc:
                runner.log(f"[Sunshine] 读取新增日志失败 {candidate}: {exc}")
                continue
            if '"device_id"' not in new_text:
                continue
            saw_device_key = True
            devices = _scan_display_devices(_latest_startup_text(new_text))
            matched, reason = _match_virtual_device(devices)
            if matched is not None:
                device_id = _device_id(matched)
                if device_id:
                    runner.log(
                        f"[Sunshine] 新增日志第 {attempt} 次轮询命中虚拟屏："
                        f"{device_id}（{reason}）"
                    )
                    return device_id
        if attempt < attempts:
            sleep(interval)

    if saw_device_key:
        runner.log(
            f"[Sunshine] {timeout_s:.0f} 秒内新增日志出现了 device_id，"
            "但没有解析到虚拟屏"
        )
    else:
        runner.log(f"[Sunshine] {timeout_s:.0f} 秒内日志没有新增 device_id")
    report_display_diagnostics(runner, status, log_path=log_path)
    return None


def report_display_diagnostics(
    runner: CommandRunner,
    status: SunshineStatus,
    *,
    log_path: Path | None = None,
    tail_lines: int = 30,
) -> None:
    """轮询超时后输出 PnP 显示器状态与日志末尾，避免继续盲猜。"""
    runner.log("[Sunshine] 轮询超时，开始诊断显示器与 Sunshine 日志…")
    result = runner.query_powershell(_DISPLAY_PNP_SCRIPT, timeout_s=20)
    if result.ok and result.stdout.strip():
        runner.log(
            "[Sunshine] Get-PnpDevice -Class Display：\n"
            + result.stdout.strip()
        )
    else:
        runner.log(
            "[Sunshine] Get-PnpDevice -Class Display 查询失败："
            + _tail(result.stderr or result.stdout)
        )

    existing = [
        candidate
        for candidate in _log_candidates(status, log_path)
        if candidate.is_file()
    ]
    if not existing:
        runner.log("[Sunshine] 诊断时没有找到可读取的 sunshine.log")
        return
    for candidate in existing:
        try:
            lines = _read_tail(candidate).splitlines()
        except OSError as exc:
            runner.log(f"[Sunshine] 读取诊断日志失败 {candidate}: {exc}")
            continue
        tail = lines[-tail_lines:]
        runner.log(
            f"[Sunshine] {candidate} 最后 {len(tail)} 行：\n"
            + "\n".join(tail)
        )


def default_resolution_mode(monitor_count: int) -> ResolutionMode:
    """单设备默认固定为所选分辨率；多设备默认跟随客户端请求。"""
    return "manual" if monitor_count <= 1 else "auto"


def build_config_plan(
    *,
    address_family: AddressFamily,
    output_name: str | None,
    resolution_mode: ResolutionMode,
    manual_resolution: tuple[int, int] | None = None,
    revert_on_disconnect: bool = True,
) -> SunshineConfigPlan:
    """组装待写入的配置计划，不产生任何副作用。"""
    return SunshineConfigPlan(
        address_family=address_family,
        output_name=output_name,
        display_configuration="ensure_only_display",
        revert_on_disconnect=revert_on_disconnect,
        resolution_mode=resolution_mode,
        manual_resolution=manual_resolution,
    )


def apply_config(
    runner: CommandRunner,
    status: SunshineStatus,
    plan: SunshineConfigPlan,
) -> OperationResult:
    """把计划合并写入 ``sunshine.conf``。

    只更新受管键，保留其他键、注释与行顺序；写入前自动生成 ``.bak``。
    配置文件不存在时返回失败，不擅自创建。
    """
    config_path = status.config_path
    if config_path is None or not Path(config_path).is_file():
        return OperationResult.failure("找不到 sunshine.conf，已中止写入")

    state = read_config(runner, Path(config_path))
    updates = _plan_updates(plan)
    lines = list(state.raw_lines)
    updated: set[str] = set()

    for index, line in enumerate(lines):
        match = _CONFIG_LINE_RE.match(line)
        if not match:
            continue
        key = match.group("key")
        lower = key.lower()
        if lower not in updates:
            continue
        lines[index] = f"{match.group('indent')}{key} = {updates[lower]}"
        updated.add(lower)

    for key, value in updates.items():
        if key not in updated:
            lines.append(f"{key} = {value}")

    content = state.newline.join(lines)
    if not content.endswith(("\n", "\r")):
        content += state.newline

    original = state.newline.join(state.raw_lines)
    if original and not original.endswith(("\n", "\r")):
        original += state.newline
    if content == original:
        return OperationResult.success(
            "Sunshine 配置已是最新，无需修改",
            simulated=runner.dry_run,
        )

    lower_values = {key.lower(): value for key, value in state.values.items()}
    old_values = {key: lower_values.get(key, "") for key in updates}
    for key, value in updates.items():
        previous = old_values.get(key, "")
        if previous != value:
            runner.log(f"[Sunshine] {key}: {previous or '<空>'} -> {value}")

    backup = runner.write_text_atomic(Path(config_path), content, encoding="utf-8")
    prefix = "将写入" if runner.dry_run else "已写入"
    return OperationResult.success(
        f"{prefix} {len(updates)} 项 Sunshine 配置",
        details=tuple(f"{key} = {value}" for key, value in updates.items()),
        backup_path=backup,
        simulated=runner.dry_run,
    )


def start_service(
    runner: CommandRunner,
    service_name: str = DEFAULT_SERVICE_NAME,
) -> OperationResult:
    """启动 Sunshine 服务并等待其进入 Running，供安装后自动拉起。"""
    safe_name = _ps_quote(service_name or DEFAULT_SERVICE_NAME)
    script = f"""
$ErrorActionPreference = 'Stop'
$deadline = (Get-Date).AddSeconds(20)
$svc = $null
while (-not $svc -and (Get-Date) -lt $deadline) {{
    $svc = Get-Service -Name '{safe_name}' -ErrorAction SilentlyContinue
    if (-not $svc) {{ Start-Sleep -Milliseconds 500 }}
}}
if (-not $svc) {{ throw 'SunshineService 未安装' }}
if ($svc.Status -ne 'Running') {{ Start-Service -Name '{safe_name}' }}
while ($svc.Status -ne 'Running' -and (Get-Date) -lt $deadline) {{
    Start-Sleep -Milliseconds 500
    $svc.Refresh()
}}
if ($svc.Status -ne 'Running') {{
    throw ('服务状态仍为 ' + $svc.Status.ToString())
}}
$svc.Status.ToString()
""".strip()
    result = runner.mutate_powershell(script, timeout_s=60)
    if result.simulated:
        return OperationResult.success(
            f"[DRY-RUN] 将启动服务「{service_name}」",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            f"启动服务「{service_name}」失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(
        f"服务「{service_name}」已在后台启动",
        details=(f"当前状态: {_tail(result.stdout)}",),
    )


def restart_service(runner: CommandRunner, status: SunshineStatus) -> OperationResult:
    """重启 Sunshine Windows 服务，使配置生效；Dry-Run 下只记录。"""
    if status.service_state == "not_installed":
        return OperationResult.failure("未检测到 Sunshine 服务，请先安装并启动 Sunshine")

    safe_name = _ps_quote(status.service_name or DEFAULT_SERVICE_NAME)
    script = (
        f"Restart-Service -Name '{safe_name}' -Force -ErrorAction Stop\n"
        f"(Get-Service -Name '{safe_name}').Status.ToString()\n"
    )
    result = runner.mutate_powershell(script, timeout_s=60)
    if result.simulated:
        return OperationResult.success(
            f"[DRY-RUN] 将重启服务「{status.service_name}」",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            f"重启服务「{status.service_name}」失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(
        f"已重启服务「{status.service_name}」",
        details=(f"当前状态: {_tail(result.stdout)}",),
    )


def pairing_port(status: SunshineStatus | None = None) -> int:
    """返回 Sunshine 本机 Web UI 端口。

    默认 Web UI 为 ``port + 1``（默认 47990）。如果 ``sunshine.conf``
    自定义了 ``port``，则按该值加一计算，避免打开错误端口。
    """
    config_path = status.config_path if status is not None else None
    if config_path is None or not Path(config_path).is_file():
        return PAIRING_PORT
    try:
        text = Path(config_path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return PAIRING_PORT
    for line in text.splitlines():
        match = _CONFIG_LINE_RE.match(line)
        if not match or match.group("key").strip().lower() != "port":
            continue
        value = _strip_inline_comment(match.group("value").strip())
        try:
            base_port = int(value)
        except ValueError:
            return PAIRING_PORT
        if 1 <= base_port <= 65534:
            return base_port + 1
        return PAIRING_PORT
    return PAIRING_PORT


def pairing_url(
    status: SunshineStatus | None = None,
    *,
    port: int | None = None,
) -> str:
    """生成仅在**本机**打开的 Sunshine 配对页地址。

    Sunshine 默认只允许 localhost 访问 Web UI，因此不要使用局域网 IP：
    ``https://localhost:<端口>/pin``。

    Args:
        status: 用于读取 ``sunshine.conf`` 中自定义的 ``port``。
        port: 直接指定 Web UI 端口，主要用于测试或特殊部署。
    """
    resolved_port = port if port is not None else pairing_port(status)
    return f"https://localhost:{resolved_port}/pin"


# ----------------------------------------------------------------------
# 内部实现
# ----------------------------------------------------------------------


def _query_registry_and_service(runner: CommandRunner) -> dict[str, Any]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$apps = @(Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' |
    Where-Object { $_.DisplayName -like '*Sunshine*' } |
    Select-Object DisplayName, DisplayVersion, InstallLocation)
$svc = Get-Service -Name 'SunshineService' -ErrorAction SilentlyContinue
if (-not $svc) {
    $svc = Get-Service -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like '*Sunshine*' } |
        Select-Object -First 1
}
$service = $null
if ($svc) {
    $cim = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $svc.Name } |
        Select-Object -First 1
    $service = [pscustomobject]@{
        Name = $svc.Name
        Status = $svc.Status.ToString()
        ImagePath = if ($cim) { $cim.PathName } else { $null }
    }
}
[pscustomobject]@{ apps = $apps; service = $service } |
    ConvertTo-Json -Depth 4 -Compress
""".strip()
    result = runner.query_powershell(script, timeout_s=20)
    if not result.ok:
        runner.log(f"[Sunshine] 注册表 / 服务查询失败: {_tail(result.stderr or result.stdout)}")
        return {}
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_roots(
    search_roots: Sequence[Path],
    apps: Sequence[dict[str, Any]],
) -> list[Path]:
    roots: list[Path] = [Path(root) for root in search_roots]
    for app in apps:
        location = str(app.get("InstallLocation") or "").strip()
        if location:
            roots.insert(0, Path(location))
    roots.append(DEFAULT_INSTALL_DIR)
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            roots.append(Path(base) / "Sunshine")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Programs" / "Sunshine")
    return _dedupe_paths(roots)


def _dedupe_paths(paths: Sequence[Path | None]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        if path is None:
            continue
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _parse_service_image_path(value: Any) -> Path | None:
    """从服务 ``ImagePath`` 提取可执行文件路径。

    兼容带引号、不带引号、带启动参数以及 ``%ProgramFiles%`` 环境变量形式。
    """
    text = str(value or "").strip()
    if not text:
        return None
    if text.startswith('"'):
        end = text.find('"', 1)
        raw = text[1:end] if end > 0 else text[1:]
    else:
        match = re.match(r"^(.*?\.exe)(?:\s|$)", text, re.IGNORECASE)
        raw = match.group(1) if match else text.split()[0]
    raw = raw.strip().strip('"')
    if raw.startswith("\\??\\"):
        raw = raw[4:]
    raw = os.path.expandvars(raw)
    return Path(raw) if raw else None


def _normalize_service_state(value: Any) -> ServiceState:
    if isinstance(value, int):
        return {
            1: "stopped",
            2: "start_pending",
            3: "stop_pending",
            4: "running",
            5: "start_pending",
            6: "stop_pending",
            7: "stopped",
        }.get(value, "unknown")
    text = str(value or "").strip().lower()
    return {
        "running": "running",
        "stopped": "stopped",
        "startpending": "start_pending",
        "start_pending": "start_pending",
        "stoppending": "stop_pending",
        "stop_pending": "stop_pending",
    }.get(text, "unknown")


def _plan_updates(plan: SunshineConfigPlan) -> dict[str, str]:
    updates: dict[str, str] = {
        KEY_ADDRESS_FAMILY: plan.address_family,
        KEY_DD_CONFIGURATION: plan.display_configuration,
        KEY_DD_REVERT: "enabled" if plan.revert_on_disconnect else "disabled",
    }
    if plan.output_name:
        updates[KEY_OUTPUT_NAME] = plan.output_name.strip()
    if plan.resolution_mode == "auto":
        updates[KEY_DD_RESOLUTION] = "auto"
        updates[KEY_DD_REFRESH_RATE] = "auto"
    elif plan.manual_resolution is not None and all(
        value > 0 for value in plan.manual_resolution
    ):
        width, height = plan.manual_resolution
        updates[KEY_DD_RESOLUTION] = "manual"
        updates[KEY_DD_MANUAL_RESOLUTION] = f"{width}x{height}"
        updates[KEY_DD_REFRESH_RATE] = "disabled"
    else:
        updates[KEY_DD_RESOLUTION] = "disabled"
        updates[KEY_DD_REFRESH_RATE] = "disabled"
    return updates


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {'"', "'"}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        elif char == "#" and quote is None:
            if index == 0 or value[index - 1].isspace():
                return value[:index].rstrip()
    return value.strip()


def _log_candidates(status: SunshineStatus, explicit: Path | None) -> list[Path]:
    if explicit is not None:
        # 显式指定日志时只读取该文件，便于测试与故障复现。
        return [Path(explicit)]
    candidates: list[Path] = [DEFAULT_INSTALL_DIR / "config" / LOG_FILENAME]
    if status.config_path is not None:
        candidates.append(Path(status.config_path).parent / LOG_FILENAME)
    if status.install_dir is not None:
        candidates.append(Path(status.install_dir) / "config" / LOG_FILENAME)
        candidates.append(Path(status.install_dir) / LOG_FILENAME)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base = Path(local_app_data) / "LizardByte" / "Sunshine"
        candidates.append(base / LOG_FILENAME)
        candidates.append(base / "config" / LOG_FILENAME)
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Sunshine" / "config" / LOG_FILENAME)
    return _dedupe_paths(candidates)


def _latest_startup_text(text: str) -> str:
    """只保留最新一次 Sunshine 启动之后的日志。

    优先使用 ``Sunshine version:`` 作为启动边界；旧版本日志没有该行时，
    退化为 ``Starting system tray``；两者都没有则扫描全文。
    """
    starts = [
        match.start()
        for pattern in (_STARTUP_VERSION_RE, _STARTUP_TRAY_RE)
        for match in pattern.finditer(text)
    ]
    if starts:
        return text[max(starts):]
    return text


def _scan_display_devices(text: str) -> list[dict[str, Any]]:
    """扫描任意位置的显示器 JSON 对象，不依赖固定标题文本。

    使用花括号栈配对完整对象，跳过字符串内的 ``{`` / ``}``，因此
    ``"info": {...}`` 等嵌套结构不会截断解析。最新出现的设备排在前面，
    并按归一化后的 device_id 去重。
    """
    devices: list[dict[str, Any]] = []
    seen: set[str] = set()

    for start, end in reversed(list(_iter_json_objects(text))):
        if _DEVICE_ID_KEY_RE.search(text, start, end) is None:
            continue
        try:
            value = json.loads(text[start:end])
        except (json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(value, dict):
            continue
        if "display_name" not in value and "friendly_name" not in value:
            # 排除 Scheduling display device configuration 这类只有 device_id 的配置块。
            continue
        device_id = _device_id(value)
        if device_id is None or device_id in seen:
            continue
        seen.add(device_id)
        devices.append(value)
        if len(devices) >= 64:
            break
    return devices


def _iter_json_objects(text: str) -> Iterator[tuple[int, int]]:
    """按括号栈产出完整 JSON 对象 ``(start, end)``。

    忽略字符串内部的花括号，并正确处理反斜杠转义；未闭合的对象不会产出，
    便于从被截断的日志中继续使用上一个完整设备块。
    """
    stack: list[int] = []
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            stack.append(index)
        elif char == "}" and stack:
            yield stack.pop(), index + 1


def _read_range(path: Path, start: int, end: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(start)
        return handle.read(max(0, end - start))


def _device_id(device: Mapping[str, Any] | None) -> str | None:
    if not device:
        return None
    return _normalize_device_id(str(device.get("device_id") or ""))


def _normalize_device_id(value: str) -> str | None:
    match = _GUID_RE.search(value or "")
    if match:
        return match.group(0)
    match = _BARE_GUID_RE.search(value or "")
    if match:
        return "{" + match.group(0) + "}"
    return None


def _device_search_text(device: Mapping[str, Any]) -> str:
    # 整个设备对象都参与关键词匹配，兼容 friendly_name / display_name /
    # info / edid（VDD 的 edid 制造商 ID 为 MTT）以及未来新增字段。
    return json.dumps(device, ensure_ascii=False, default=str).casefold()


def _is_primary(device: Mapping[str, Any]) -> bool | None:
    info = device.get("info")
    if isinstance(info, dict):
        value = info.get("primary")
        if isinstance(value, bool):
            return value
    return None


def _display_name(device: Mapping[str, Any]) -> str:
    return str(device.get("display_name") or "")


def _friendly_name(device: Mapping[str, Any]) -> str:
    return str(device.get("friendly_name") or "")


def _preferred_device(devices: Sequence[dict[str, Any]]) -> dict[str, Any]:
    for device in devices:
        if _is_primary(device) is False:
            return device
    return devices[0]


def _match_virtual_device(
    devices: Sequence[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    # 首选 friendly_name，符合 VDD / MTT / Virtual / Indirect / Idd 的命名习惯。
    for keyword in _VIRTUAL_DISPLAY_KEYWORDS:
        matches = [
            device
            for device in devices
            if keyword in _friendly_name(device).casefold()
        ]
        if matches:
            return _preferred_device(matches), f"friendly_name 关键词 {keyword}"
    # 兼容 friendly_name 为空、但 EDID / info 中带 MTT 等标记的驱动。
    for keyword in _VIRTUAL_DISPLAY_KEYWORDS:
        matches = [
            device
            for device in devices
            if keyword in _device_search_text(device)
        ]
        if matches:
            return _preferred_device(matches), f"对象关键词 {keyword}"
    return None, ""


def _fallback_display_device(
    devices: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not devices:
        return None
    if len(devices) == 1:
        only = devices[0]
        # 单屏且明显是主屏（通常是物理 \\.\DISPLAY1）时不要误写 output_name。
        if _is_primary(only) is True:
            return None
        if (
            _is_primary(only) is None
            and _display_name(only).casefold() == r"\\.\display1"
        ):
            return None
        return only
    non_primary = [
        device for device in devices if _is_primary(device) is False
    ]
    if non_primary:
        return non_primary[0]
    non_default = [
        device
        for device in devices
        if _display_name(device).casefold() != r"\\.\display1"
    ]
    if non_default:
        return non_default[0]
    return None


def _log_devices(
    runner: CommandRunner,
    devices: Sequence[dict[str, Any]],
) -> None:
    if not devices:
        runner.log("[Sunshine] 显示器列表为空")
        return
    for index, device in enumerate(devices, start=1):
        primary = _is_primary(device)
        runner.log(
            f"[Sunshine] 显示器[{index}] "
            f"friendly_name={device.get('friendly_name')!r} "
            f"display_name={_display_name(device)!r} "
            f"device_id={_device_id(device)} primary={primary}"
        )
    runner.log(
        "[Sunshine] 原始显示器列表：\n"
        + json.dumps(devices, ensure_ascii=False, indent=2, default=str)
    )


def _find_dxgi_info(status: SunshineStatus) -> Path | None:
    if status.install_dir is None:
        return None
    for candidate in (
        Path(status.install_dir) / "dxgi-info.exe",
        Path(status.install_dir) / "tools" / "dxgi-info.exe",
    ):
        if candidate.is_file():
            return candidate
    return None


def _read_tail(path: Path, max_bytes: int = 2_000_000) -> str:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        data = handle.read()
    return data.decode("utf-8", errors="replace")


def _best_guid_from_text(text: str) -> str | None:
    best_guid: str | None = None
    best_score = -1
    unique: set[str] = set()
    saw_display = False

    for line in text.splitlines():
        matches = [
            normalized
            for guid in _GUID_RE.findall(line)
            if (normalized := _normalize_device_id(guid)) is not None
        ]
        if not matches:
            continue
        unique.update(matches)
        lowered = line.casefold()
        if "display" in lowered or "output" in lowered or "monitor" in lowered:
            saw_display = True
        score = _line_score(lowered)
        for guid in matches:
            if score >= best_score:
                best_guid = guid
                best_score = score

    if best_guid is not None and best_score >= 5:
        return best_guid
    if saw_display and len(unique) == 1:
        return next(iter(unique))
    return None


def _line_score(lowered: str) -> int:
    score = 0
    for marker, weight in (
        ("virtual", 5),
        ("vdd", 5),
        ("mtt", 5),
        ("idd", 4),
        ("indirect", 4),
        ("display", 2),
        ("output", 1),
        ("monitor", 1),
    ):
        if marker in lowered:
            score += weight
    return score


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]
