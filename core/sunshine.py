"""模块 C：Sunshine 检测、虚拟屏 ``device_id`` 提取与 ``sunshine.conf`` 自动配置。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from core.common import CommandRunner, OperationResult

AddressFamily = Literal["ipv4", "ipv6", "both"]
ResolutionMode = Literal["do_not_change", "follow_client"]
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
KEY_DD_REFRESH_RATE = "dd_refresh_rate_option"

_GUID_RE = re.compile(
    r"\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}"
)
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
    - ``resolution_mode=do_not_change`` → ``dd_resolution_option=disabled``、
      ``dd_refresh_rate_option=disabled``
    - ``resolution_mode=follow_client`` → ``dd_resolution_option=auto``、
      ``dd_refresh_rate_option=auto``
    """

    address_family: AddressFamily = "ipv4"
    output_name: str | None = None
    display_configuration: DisplayConfiguration = "ensure_only_display"
    revert_on_disconnect: bool = True
    resolution_mode: ResolutionMode = "do_not_change"


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
    if isinstance(service, dict):
        service_name = str(service.get("Name") or DEFAULT_SERVICE_NAME)
        service_state = _normalize_service_state(service.get("Status"))
    else:
        service_state = "not_installed"

    version: str | None = None
    if apps:
        version = str(apps[0].get("DisplayVersion") or "") or None

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

    优先解析 Sunshine 启动日志中与虚拟显示器相关的设备行；
    日志不可用时调用安装目录下的 ``dxgi-info.exe``。
    返回带花括号的完整 ID（如 ``{a1b2c3...}``）；未找到返回 None。
    """
    for candidate in _log_candidates(status, log_path):
        if not candidate.is_file():
            continue
        try:
            text = _read_tail(candidate)
        except OSError as exc:
            runner.log(f"[Sunshine] 读取日志失败 {candidate}: {exc}")
            continue
        found = _best_guid_from_text(text)
        if found is not None:
            runner.log(f"[Sunshine] 从日志提取虚拟屏 ID: {found}（{candidate.name}）")
            return found

    dxgi = _find_dxgi_info(status)
    if dxgi is not None:
        result = runner.query([str(dxgi)], timeout_s=20)
        if result.ok:
            found = _best_guid_from_text(result.stdout)
            if found is not None:
                runner.log(f"[Sunshine] 从 dxgi-info.exe 提取虚拟屏 ID: {found}")
                return found
        else:
            runner.log(f"[Sunshine] dxgi-info.exe 执行失败: {_tail(result.stderr or result.stdout)}")

    runner.log("[Sunshine] 未能提取虚拟屏 device_id，将不写入 output_name")
    return None


def default_resolution_mode(monitor_count: int) -> ResolutionMode:
    """单设备默认不改变分辨率；多设备默认跟随客户端请求。"""
    return "do_not_change" if monitor_count <= 1 else "follow_client"


def build_config_plan(
    *,
    address_family: AddressFamily,
    output_name: str | None,
    resolution_mode: ResolutionMode,
    revert_on_disconnect: bool = True,
) -> SunshineConfigPlan:
    """组装待写入的配置计划，不产生任何副作用。"""
    return SunshineConfigPlan(
        address_family=address_family,
        output_name=output_name,
        display_configuration="ensure_only_display",
        revert_on_disconnect=revert_on_disconnect,
        resolution_mode=resolution_mode,
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


def pairing_url(ip: str, *, port: int = PAIRING_PORT) -> str:
    """生成 Sunshine Web 配对页地址，例如 ``https://192.168.1.10:47990``。

    传入 IPv6 时会自动加方括号。
    """
    host = f"[{ip}]" if ":" in ip else ip
    return f"https://{host}:{port}"


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
    $service = [pscustomobject]@{
        Name = $svc.Name
        Status = $svc.Status.ToString()
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
    if plan.resolution_mode == "follow_client":
        updates[KEY_DD_RESOLUTION] = "auto"
        updates[KEY_DD_REFRESH_RATE] = "auto"
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
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    if status.config_path is not None:
        candidates.append(Path(status.config_path).parent / LOG_FILENAME)
    if status.install_dir is not None:
        candidates.append(Path(status.install_dir) / "config" / LOG_FILENAME)
        candidates.append(Path(status.install_dir) / LOG_FILENAME)
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Sunshine" / "config" / LOG_FILENAME)
    return _dedupe_paths(candidates)


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
        matches = _GUID_RE.findall(line)
        if not matches:
            continue
        unique.update(matches)
        lowered = line.lower()
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
        ("vdd", 4),
        ("idd", 3),
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
