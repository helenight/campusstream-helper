"""模块 B：虚拟显示器检测、设备预设与 ``vdd_settings.xml`` 安全注入。"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

from core.common import (
    CommandRunner,
    OperationResult,
    default_state_dir,
    resource_path,
)

Platform = Literal["ipados", "android_tablet", "android_phone", "ios"]
VddSchema = Literal["resolutions", "monitor-list", "unknown"]
MonitorSource = Literal["preset", "custom"]

DEFAULT_PRESETS_PATH = resource_path("assets/device_presets.json")

# Phase 2 会结合注册表与环境变量扩展这份候选列表。
DEFAULT_VDD_SETTINGS_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\VirtualDisplayDriver\vdd_settings.xml"),
    Path(r"C:\Program Files\Virtual Display Driver\vdd_settings.xml"),
)

_MANIFEST_NAME = "vdd_managed.json"
_MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class DevicePreset:
    """``assets/device_presets.json`` 中的一条机型预设。"""

    id: str
    platform: Platform
    label: str
    width: int
    height: int
    refresh_options: tuple[int, ...]
    default_refresh_hz: int
    windows_scaling_percent: int
    aspect_ratio: str
    notes: str = ""

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"设备 {self.id} 的分辨率必须为正数")
        if not self.refresh_options:
            raise ValueError(f"设备 {self.id} 至少需要一个刷新率选项")
        if self.default_refresh_hz not in self.refresh_options:
            raise ValueError(f"设备 {self.id} 的默认刷新率不在 refresh_options 中")
        if not 100 <= self.windows_scaling_percent <= 300:
            raise ValueError(f"设备 {self.id} 的 Windows 缩放比例应在 100~300 之间")

    @property
    def default_mode(self) -> str:
        """形如 ``2360x1640@60`` 的展示字符串。"""
        return f"{self.width}x{self.height}@{self.default_refresh_hz}"

    def to_monitor(self, refresh_hz: int | None = None) -> "MonitorSpec":
        """把预设转换为可注入的显示器规格。"""
        hz = self.default_refresh_hz if refresh_hz is None else refresh_hz
        if hz not in self.refresh_options:
            raise ValueError(f"设备 {self.id} 不支持 {hz}Hz")
        return MonitorSpec(
            width=self.width,
            height=self.height,
            refresh_hz=hz,
            label=self.label,
            source="preset",
            windows_scaling_percent=self.windows_scaling_percent,
        )


@dataclass(frozen=True, slots=True)
class MonitorSpec:
    """一台要注入虚拟显示器驱动的分辨率规格。"""

    width: int
    height: int
    refresh_hz: int
    label: str = ""
    source: MonitorSource = "preset"
    windows_scaling_percent: int = 150

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("宽度与高度必须为正数")
        if self.refresh_hz <= 0:
            raise ValueError("刷新率必须为正数")
        if not 100 <= self.windows_scaling_percent <= 300:
            raise ValueError("Windows 缩放比例应在 100~300 之间")

    @property
    def key(self) -> tuple[int, int, int]:
        """用于去重与回滚匹配的稳定键。"""
        return (self.width, self.height, self.refresh_hz)

    @property
    def mode(self) -> str:
        return f"{self.width}x{self.height}@{self.refresh_hz}"


@dataclass(frozen=True, slots=True)
class VddStatus:
    """Virtual Display Driver 的安装与配置状态。"""

    installed: bool
    install_dir: Path | None
    settings_path: Path | None
    driver_version: str | None
    schema: VddSchema
    configured_monitor_count: int
    configured_resolution_count: int
    detail: str
    # Get-PnpDevice -Class Display 中是否至少有一个虚拟屏状态为 OK。
    device_ready: bool = True


def load_device_presets(path: Path | None = None) -> tuple[DevicePreset, ...]:
    """读取并校验设备预设。

    Raises:
        FileNotFoundError: 预设文件不存在。
        ValueError: JSON 结构或字段不合法。
    """
    presets_path = Path(path) if path is not None else DEFAULT_PRESETS_PATH
    if not presets_path.exists():
        raise FileNotFoundError(f"设备预设文件不存在: {presets_path}")

    try:
        payload = json.loads(presets_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"设备预设 JSON 格式错误: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("设备预设根节点必须是对象")

    devices = payload.get("devices")
    if not isinstance(devices, list):
        raise ValueError("设备预设缺少 devices 数组")

    allowed_platforms = {"ipados", "android_tablet", "android_phone", "ios"}
    seen: set[str] = set()
    result: list[DevicePreset] = []
    for index, item in enumerate(devices):
        if not isinstance(item, dict):
            raise ValueError(f"devices[{index}] 必须是对象")
        device_id = str(item.get("id") or "").strip()
        if not device_id:
            raise ValueError(f"devices[{index}] 缺少 id")
        if device_id in seen:
            raise ValueError(f"设备 id 重复: {device_id}")
        seen.add(device_id)

        platform = str(item.get("platform") or "")
        if platform not in allowed_platforms:
            raise ValueError(f"设备 {device_id} 的 platform 不合法: {platform}")

        try:
            width = int(item["width"])
            height = int(item["height"])
            default_refresh = int(item["default_refresh_hz"])
            scaling = int(item["windows_scaling_percent"])
            refresh_options = tuple(int(value) for value in item["refresh_options"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"设备 {device_id} 的字段不完整或类型错误") from exc

        result.append(
            DevicePreset(
                id=device_id,
                platform=platform,  # type: ignore[arg-type]
                label=str(item.get("label") or device_id),
                width=width,
                height=height,
                refresh_options=refresh_options,
                default_refresh_hz=default_refresh,
                windows_scaling_percent=scaling,
                aspect_ratio=str(item.get("aspect_ratio") or ""),
                notes=str(item.get("notes") or ""),
            )
        )
    return tuple(result)


def presets_for_platform(
    presets: Sequence[DevicePreset],
    platform: Platform,
) -> tuple[DevicePreset, ...]:
    """返回某客户端平台下的全部机型，供 UI 的 Step 2 使用。"""
    return tuple(preset for preset in presets if preset.platform == platform)


def detect_vdd(
    runner: CommandRunner,
    *,
    extra_search_roots: Sequence[Path] = (),
) -> VddStatus:
    """检测 Virtual Display Driver 是否安装，并定位 ``vdd_settings.xml``。

    检测顺序：PnP 设备名 / 驱动服务 → 常见安装目录 → 用户自定义路径。
    ``schema`` 判定：``resolutions`` 为官方新版，``monitor-list`` 为旧版。
    """
    settings_path = _find_settings_path(extra_search_roots)
    devices, controllers = _query_vdd_devices(runner)

    matched_devices = [
        device for device in devices if _is_virtual_display_name(str(device.get("FriendlyName") or ""))
    ]
    matched_controllers = [
        controller
        for controller in controllers
        if _is_virtual_display_name(str(controller.get("Name") or ""))
    ]
    installed = bool(matched_devices or matched_controllers or settings_path)
    ready_devices = [
        device
        for device in matched_devices
        if str(device.get("Status") or "").strip().casefold() == "ok"
    ]
    device_ready = bool(ready_devices)

    driver_version = None
    if matched_controllers:
        driver_version = str(matched_controllers[0].get("DriverVersion") or "") or None

    schema: VddSchema = "unknown"
    monitor_count = 0
    resolution_count = 0
    parse_detail = ""
    if settings_path is not None:
        schema, monitor_count, resolution_count, parse_detail = _inspect_settings(settings_path)

    if installed and not device_ready:
        detail = (
            "已部署 Virtual Display Driver 文件，但 Get-PnpDevice 中"
            "没有状态为 OK 的虚拟屏设备；请运行 VDD Control 完成驱动安装。"
        )
    elif installed and settings_path is None:
        detail = "已检测到 Virtual Display Driver，但未找到 vdd_settings.xml，请手动指定安装目录。"
    elif installed:
        detail = (
            f"已安装；配置文件 {settings_path}；结构 {schema}；"
            f"虚拟屏 {monitor_count} 个；分辨率 {resolution_count} 条"
        )
        if parse_detail:
            detail += f"；{parse_detail}"
    else:
        detail = "未检测到 Virtual Display Driver，请先安装驱动。"

    status = VddStatus(
        installed=installed,
        install_dir=settings_path.parent if settings_path is not None else None,
        settings_path=settings_path,
        driver_version=driver_version,
        schema=schema,
        configured_monitor_count=monitor_count,
        configured_resolution_count=resolution_count,
        detail=detail,
        device_ready=device_ready,
    )
    runner.log(f"[VDD] {detail}")
    return status


def apply_resolutions(
    runner: CommandRunner,
    settings_path: Path,
    monitors: Sequence[MonitorSpec],
    *,
    monitor_count: int | None = None,
    state_dir: Path | None = None,
) -> OperationResult:
    """把选中的分辨率安全写入 ``vdd_settings.xml``。

    安全策略：
    - 解析失败立即中止，绝不覆盖无法识别的格式；
    - 只追加缺失的 ``<resolution>``，已存在的不重复添加；
    - 保留注释、GPU、options 等原有节点；
    - ``<monitors><count>`` 设为本次需要的虚拟屏数量（默认 ``len(monitors)``）；
    - 把本次写入的指纹记录到 ``state_dir``，供回滚精确删除。
    """
    settings_path = Path(settings_path)
    if not monitors:
        return OperationResult.failure("至少需要选择一个设备分辨率")
    if not settings_path.exists():
        return OperationResult.failure(f"找不到 VDD 配置文件：{settings_path}")

    specs = _unique_specs(monitors)
    desired_count = len(specs) if monitor_count is None else int(monitor_count)
    if desired_count <= 0:
        return OperationResult.failure("虚拟屏数量必须大于 0")

    try:
        original_text = runner.read_text(settings_path, encoding="utf-8-sig")
        root = _parse_xml(original_text)
    except (OSError, ET.ParseError) as exc:
        return OperationResult.failure(
            "无法解析 vdd_settings.xml，已中止写入",
            details=(str(exc),),
        )

    if not isinstance(root.tag, str) or _local_name(root.tag) != "vdd_settings" or "}" in root.tag:
        return OperationResult.failure("vdd_settings.xml 根节点不是 vdd_settings，已中止写入")

    schema, container = _detect_schema(root)
    if schema == "unknown":
        return OperationResult.failure(
            "无法识别 vdd_settings.xml 的结构，已中止写入",
            details=("支持的结构：<resolutions> 或 <monitors><monitor>…",),
        )

    state_directory = Path(state_dir) if state_dir is not None else default_state_dir()
    state_path = state_directory / _MANIFEST_NAME
    manifest = _load_manifest(state_path)
    if manifest is not None and not _same_path(manifest.get("settings_path"), settings_path):
        manifest = None

    added: list[tuple[int, int, int]] = []
    new_created_resolutions = False
    new_created_monitors = False
    new_created_count = False
    original_count: int | None = None
    count_changed = False

    if schema == "resolutions":
        if container is None:
            container = ET.Element("resolutions")
            _insert_before_options(root, container)
            new_created_resolutions = True
        for spec in specs:
            if _add_resolution(container, spec):
                added.append(spec.key)
        original_count, new_created_monitors, new_created_count, count_changed = _ensure_monitor_count(
            root, desired_count
        )
    else:
        assert container is not None
        for spec in specs:
            if _add_legacy_monitor(container, spec):
                added.append(spec.key)
        original_count, count_changed = _set_existing_monitor_count(root, desired_count)

    # 本次是否有真实改动；不能把历史 manifest 标记算进 changed，否则每次都会重写。
    changed = (
        bool(added)
        or count_changed
        or new_created_resolutions
        or new_created_monitors
        or new_created_count
    )
    created_resolutions = new_created_resolutions
    created_monitors = new_created_monitors
    created_count = new_created_count

    if manifest is not None:
        supported = _manifest_triples(manifest)
        if supported:
            added = _merge_triples(supported, added)
        original_count = manifest.get("original_monitor_count", original_count)
        created_resolutions = bool(manifest.get("created_resolutions_element")) or created_resolutions
        created_monitors = bool(manifest.get("created_monitors_element")) or created_monitors
        created_count = bool(manifest.get("created_count_element")) or created_count
    if not changed:
        return OperationResult.success(
            "VDD 配置已是最新，无需修改",
            simulated=runner.dry_run,
        )

    xml_text = _serialize_xml(root)
    backup = runner.write_text_atomic(settings_path, xml_text, encoding="utf-8")

    if not runner.dry_run:
        _write_manifest(
            runner,
            state_path,
            {
                "schema_version": _MANIFEST_VERSION,
                "settings_path": str(settings_path),
                "schema": schema,
                "managed": [list(key) for key in added],
                "original_monitor_count": original_count,
                "created_resolutions_element": created_resolutions,
                "created_monitors_element": created_monitors,
                "created_count_element": created_count,
                "applied_at": _now_iso(),
            },
        )

    details = [f"注入分辨率: {key[0]}x{key[1]}@{key[2]}" for key in added]
    details.append(f"虚拟屏数量: {desired_count}")
    prefix = "将写入" if runner.dry_run else "已写入"
    runner.log(f"[VDD] {prefix} {settings_path}，新增 {len(added)} 条分辨率")
    return OperationResult.success(
        f"{prefix} VDD 配置（新增 {len(added)} 条分辨率）",
        details=tuple(details),
        backup_path=backup,
        simulated=runner.dry_run,
    )


def reset_managed_resolutions(
    runner: CommandRunner,
    settings_path: Path,
    *,
    state_dir: Path | None = None,
) -> OperationResult:
    """仅移除本工具注入的条目，保留用户原有配置。"""
    settings_path = Path(settings_path)
    state_directory = Path(state_dir) if state_dir is not None else default_state_dir()
    state_path = state_directory / _MANIFEST_NAME
    manifest = _load_manifest(state_path)
    if manifest is None:
        return OperationResult.failure("没有找到 VDD 回滚记录")
    if not _same_path(manifest.get("settings_path"), settings_path):
        return OperationResult.failure("回滚记录与当前 VDD 配置文件不一致，已中止")
    if not settings_path.exists():
        return OperationResult.failure(f"找不到 VDD 配置文件：{settings_path}")

    managed = _manifest_triples(manifest)
    if not managed:
        return OperationResult.success("回滚记录为空，无需修改")

    try:
        root = _parse_xml(runner.read_text(settings_path, encoding="utf-8-sig"))
    except (OSError, ET.ParseError) as exc:
        return OperationResult.failure("无法解析 vdd_settings.xml，已中止回滚", details=(str(exc),))

    schema = str(manifest.get("schema") or "unknown")
    removed = 0
    if schema == "resolutions":
        container = _find_child(root, "resolutions")
        if container is not None:
            for key in managed:
                if _remove_resolution(container, key):
                    removed += 1
    elif schema == "monitor-list":
        container = _find_child(root, "monitors")
        if container is not None:
            for key in managed:
                if _remove_legacy_monitor(container, key):
                    removed += 1

    count_changed = _restore_manifest_count(root, manifest)
    if not removed and not count_changed:
        return OperationResult.success(
            "VDD 配置中没有找到本工具注入的条目",
            simulated=runner.dry_run,
        )

    xml_text = _serialize_xml(root)
    backup = runner.write_text_atomic(settings_path, xml_text, encoding="utf-8")
    if not runner.dry_run:
        state_path.unlink(missing_ok=True)
    prefix = "将回滚" if runner.dry_run else "已回滚"
    runner.log(f"[VDD] {prefix} {removed} 条注入分辨率")
    return OperationResult.success(
        f"{prefix} VDD 配置（移除 {removed} 条分辨率）",
        details=(f"配置文件: {settings_path}",),
        backup_path=backup,
        simulated=runner.dry_run,
    )


def reload_driver(runner: CommandRunner) -> OperationResult:
    """重载虚拟显示器驱动，使新分辨率立即生效。"""
    script = r"""
$devices = @(Get-PnpDevice -Class Display -ErrorAction Stop |
    Where-Object { $_.FriendlyName -match 'Virtual Display|VirtualDisplay|VDD|IddSample' })
if ($devices.Count -eq 0) { throw '未找到虚拟显示设备' }
foreach ($device in $devices) {
    Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction Stop
    Start-Sleep -Milliseconds 600
    Enable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction Stop
}
$devices.Count
"""
    result = runner.mutate_powershell(script, timeout_s=60)
    if result.simulated:
        return OperationResult.success(
            "[DRY-RUN] 将重载虚拟显示器驱动",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            "重载虚拟显示器驱动失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(
        "已重载虚拟显示器驱动",
        details=(f"设备数量: {_tail(result.stdout)}",),
    )


def restore_native_display(runner: CommandRunner) -> OperationResult:
    """切回「仅电脑屏幕」，防止虚拟屏残留导致窗口跑到看不见的屏幕。

    对应命令：``DisplaySwitch.exe /internal``
    """
    result = runner.mutate(["DisplaySwitch.exe", "/internal"], timeout_s=15)
    if result.simulated:
        return OperationResult.success(
            "[DRY-RUN] 将切回「仅电脑屏幕」",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            "切回「仅电脑屏幕」失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success("已切回「仅电脑屏幕」")


# ----------------------------------------------------------------------
# VDD 检测与 XML 操作
# ----------------------------------------------------------------------


def _find_settings_path(extra_search_roots: Sequence[Path]) -> Path | None:
    candidates: list[Path] = []
    for root in extra_search_roots:
        path = Path(root)
        if path.suffix.lower() == ".xml":
            candidates.append(path)
        else:
            candidates.append(path / "vdd_settings.xml")
            candidates.append(path / "VirtualDisplayDriver" / "vdd_settings.xml")
    for env_name in ("ProgramData", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if base:
            candidates.append(Path(base) / "VirtualDisplayDriver" / "vdd_settings.xml")
    candidates.extend(DEFAULT_VDD_SETTINGS_CANDIDATES)

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(str(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            return candidate
    return None


def _query_vdd_devices(runner: CommandRunner) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$devices = @(Get-PnpDevice -Class Display | Select-Object FriendlyName, Status, InstanceId)
$controllers = @(Get-CimInstance Win32_VideoController | Select-Object Name, DriverVersion)
[pscustomobject]@{ devices = $devices; controllers = $controllers } |
    ConvertTo-Json -Depth 4 -Compress
""".strip()
    result = runner.query_powershell(script, timeout_s=20)
    if not result.ok:
        runner.log(f"[VDD] PnP 检测失败: {_tail(result.stderr or result.stdout)}")
        return [], []
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return [], []
    if not isinstance(payload, dict):
        return [], []
    devices = payload.get("devices")
    controllers = payload.get("controllers")
    return _as_dict_list(devices), _as_dict_list(controllers)


def _inspect_settings(path: Path) -> tuple[VddSchema, int, int, str]:
    try:
        root = _parse_xml(path.read_text(encoding="utf-8-sig"))
    except (OSError, ET.ParseError) as exc:
        return "unknown", 0, 0, f"XML 解析失败: {exc}"
    if not isinstance(root.tag, str) or _local_name(root.tag) != "vdd_settings":
        return "unknown", 0, 0, "根节点不是 vdd_settings"

    schema, _ = _detect_schema(root)
    if schema == "unknown":
        return schema, 0, 0, ""

    monitors_el = _find_child(root, "monitors")
    monitor_count = 0
    if monitors_el is not None:
        monitor_count = _parse_int(_text_of(monitors_el, "count")) or 0
        if schema == "monitor-list":
            monitor_count = max(monitor_count, len(_find_children(monitors_el, "monitor")))

    resolutions_el = _find_child(root, "resolutions")
    resolution_count = len(_find_children(resolutions_el, "resolution")) if resolutions_el is not None else 0
    return schema, monitor_count, resolution_count, ""


def _detect_schema(root: ET.Element) -> tuple[VddSchema, ET.Element | None]:
    if not isinstance(root.tag, str) or _local_name(root.tag) != "vdd_settings" or "}" in root.tag:
        return "unknown", None
    resolutions = _find_child(root, "resolutions")
    if resolutions is not None:
        return "resolutions", resolutions
    monitors = _find_child(root, "monitors")
    if monitors is not None:
        if _find_children(monitors, "monitor"):
            return "monitor-list", monitors
        return "resolutions", None
    return "unknown", None


def _parse_xml(text: str) -> ET.Element:
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    return ET.fromstring(text, parser=parser)


def _serialize_xml(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")


def _add_resolution(container: ET.Element, spec: MonitorSpec) -> bool:
    for resolution in _find_children(container, "resolution"):
        if (
            _parse_int(_text_of(resolution, "width")) == spec.width
            and _parse_int(_text_of(resolution, "height")) == spec.height
        ):
            rates = {
                _parse_int(rate.text)
                for rate in _find_children(resolution, "refresh_rate")
            }
            if spec.refresh_hz in rates:
                return False
            rate = ET.SubElement(resolution, "refresh_rate")
            rate.text = str(spec.refresh_hz)
            return True

    resolution = ET.SubElement(container, "resolution")
    _set_text(resolution, "width", spec.width)
    _set_text(resolution, "height", spec.height)
    _set_text(resolution, "refresh_rate", spec.refresh_hz)
    return True


def _add_legacy_monitor(container: ET.Element, spec: MonitorSpec) -> bool:
    for monitor in _find_children(container, "monitor"):
        if (
            _parse_int(_text_of(monitor, "width")) == spec.width
            and _parse_int(_text_of(monitor, "height")) == spec.height
            and _parse_int(_text_of(monitor, "refresh_rate")) == spec.refresh_hz
        ):
            return False
    monitor = ET.SubElement(container, "monitor")
    _set_text(monitor, "width", spec.width)
    _set_text(monitor, "height", spec.height)
    _set_text(monitor, "refresh_rate", spec.refresh_hz)
    _set_text(monitor, "dpi", 100)
    return True


def _ensure_monitor_count(
    root: ET.Element,
    desired: int,
) -> tuple[int | None, bool, bool, bool]:
    monitors = _find_child(root, "monitors")
    created_monitors = False
    if monitors is None:
        monitors = ET.Element("monitors")
        root.insert(0, monitors)
        created_monitors = True

    count = _find_child(monitors, "count")
    created_count = False
    original: int | None = None
    if count is None:
        count = ET.Element("count")
        monitors.insert(0, count)
        created_count = True
    else:
        original = _parse_int(count.text)

    new_value = str(desired)
    changed = count.text != new_value
    if changed:
        count.text = new_value
    return original, created_monitors, created_count, changed


def _set_existing_monitor_count(root: ET.Element, desired: int) -> tuple[int | None, bool]:
    monitors = _find_child(root, "monitors")
    if monitors is None:
        return None, False
    count = _find_child(monitors, "count")
    if count is None:
        return None, False
    original = _parse_int(count.text)
    new_value = str(desired)
    changed = count.text != new_value
    if changed:
        count.text = new_value
    return original, changed


def _remove_resolution(container: ET.Element, key: tuple[int, int, int]) -> bool:
    width, height, refresh_hz = key
    removed = False
    for resolution in list(_find_children(container, "resolution")):
        if (
            _parse_int(_text_of(resolution, "width")) != width
            or _parse_int(_text_of(resolution, "height")) != height
        ):
            continue
        for rate in list(_find_children(resolution, "refresh_rate")):
            if _parse_int(rate.text) == refresh_hz:
                resolution.remove(rate)
                removed = True
        if not _find_children(resolution, "refresh_rate"):
            container.remove(resolution)
    return removed


def _remove_legacy_monitor(container: ET.Element, key: tuple[int, int, int]) -> bool:
    width, height, refresh_hz = key
    removed = False
    for monitor in list(_find_children(container, "monitor")):
        if (
            _parse_int(_text_of(monitor, "width")) == width
            and _parse_int(_text_of(monitor, "height")) == height
            and _parse_int(_text_of(monitor, "refresh_rate")) == refresh_hz
        ):
            container.remove(monitor)
            removed = True
    return removed


def _restore_manifest_count(root: ET.Element, manifest: dict[str, Any]) -> bool:
    monitors = _find_child(root, "monitors")
    if monitors is None:
        return False
    changed = False
    count = _find_child(monitors, "count")
    original = manifest.get("original_monitor_count")

    if original is not None:
        value = str(int(original))
        if count is None:
            count = ET.Element("count")
            monitors.insert(0, count)
        if count.text != value:
            count.text = value
            changed = True
    elif manifest.get("created_count_element") and count is not None:
        monitors.remove(count)
        changed = True

    if manifest.get("created_resolutions_element"):
        resolutions = _find_child(root, "resolutions")
        if resolutions is not None and not _element_children(resolutions):
            root.remove(resolutions)
            changed = True

    if manifest.get("created_monitors_element"):
        if not _element_children(monitors):
            root.remove(monitors)
            changed = True

    return changed


def _insert_before_options(root: ET.Element, element: ET.Element) -> None:
    for index, child in enumerate(list(root)):
        if isinstance(child.tag, str) and _local_name(child.tag) in {"options", "global"}:
            root.insert(index, element)
            return
    root.append(element)


def _set_text(parent: ET.Element, name: str, value: Any) -> ET.Element:
    child = _find_child(parent, name)
    if child is None:
        child = ET.SubElement(parent, name)
    child.text = str(value)
    return child


def _text_of(parent: ET.Element, name: str) -> str:
    child = _find_child(parent, name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _find_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent:
        if isinstance(child.tag, str) and _local_name(child.tag) == name:
            return child
    return None


def _find_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [
        child
        for child in parent
        if isinstance(child.tag, str) and _local_name(child.tag) == name
    ]


def _element_children(parent: ET.Element) -> list[ET.Element]:
    return [child for child in parent if isinstance(child.tag, str)]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unique_specs(monitors: Sequence[MonitorSpec]) -> list[MonitorSpec]:
    result: list[MonitorSpec] = []
    seen: set[tuple[int, int, int]] = set()
    for spec in monitors:
        if spec.key in seen:
            continue
        seen.add(spec.key)
        result.append(spec)
    return result


def _manifest_triples(manifest: dict[str, Any]) -> list[tuple[int, int, int]]:
    raw = manifest.get("managed")
    if not isinstance(raw, list):
        return []
    result: list[tuple[int, int, int]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 3:
            continue
        try:
            result.append((int(item[0]), int(item[1]), int(item[2])))
        except (TypeError, ValueError):
            continue
    return result


def _merge_triples(
    first: Sequence[tuple[int, int, int]],
    second: Sequence[tuple[int, int, int]],
) -> list[tuple[int, int, int]]:
    result: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    for key in (*first, *second):
        if key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def _load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_manifest(runner: CommandRunner, path: Path, payload: dict[str, Any]) -> None:
    runner.write_text_atomic(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _same_path(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return False
    return os.path.normcase(str(Path(str(first)).resolve())) == os.path.normcase(
        str(Path(str(second)).resolve())
    )


def _is_virtual_display_name(name: str) -> bool:
    lowered = name.lower()
    return any(
        marker in lowered
        for marker in ("virtual display", "virtualdisplay", "iddsample", "vdd", "virtualdrivers")
    )


def _as_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
