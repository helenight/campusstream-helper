"""模块 B：虚拟显示器检测、设备预设与 ``vdd_settings.xml`` 安全注入。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from core.common import CommandRunner, OperationResult

Platform = Literal["ipados", "android_tablet", "android_phone", "ios"]
VddSchema = Literal["resolutions", "monitor-list", "unknown"]
MonitorSource = Literal["preset", "custom"]

DEFAULT_PRESETS_PATH = Path(__file__).resolve().parents[1] / "assets" / "device_presets.json"

# Phase 2 会结合注册表与环境变量扩展这份候选列表。
DEFAULT_VDD_SETTINGS_CANDIDATES: tuple[Path, ...] = (
    Path(r"C:\VirtualDisplayDriver\vdd_settings.xml"),
    Path(r"C:\Program Files\Virtual Display Driver\vdd_settings.xml"),
)


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
        )


@dataclass(frozen=True, slots=True)
class MonitorSpec:
    """一台要注入虚拟显示器驱动的分辨率规格。"""

    width: int
    height: int
    refresh_hz: int
    label: str = ""
    source: MonitorSource = "preset"

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("宽度与高度必须为正数")
        if self.refresh_hz <= 0:
            raise ValueError("刷新率必须为正数")

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


def load_device_presets(path: Path | None = None) -> tuple[DevicePreset, ...]:
    """读取并校验设备预设。

    校验规则：
    - ``platform`` 必须是 ipados / android_tablet / android_phone / ios；
    - ``refresh_options`` 非空且包含 ``default_refresh_hz``；
    - ``windows_scaling_percent`` 在 100~300 之间；
    - 设备 ``id`` 全局唯一。

    Raises:
        FileNotFoundError: 预设文件不存在。
        ValueError: JSON 结构或字段不合法。
    """
    raise NotImplementedError("Phase 2 实现")


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
    raise NotImplementedError("Phase 2 实现")


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
    raise NotImplementedError("Phase 2 实现")


def reset_managed_resolutions(
    runner: CommandRunner,
    settings_path: Path,
    *,
    state_dir: Path | None = None,
) -> OperationResult:
    """仅移除本工具注入的条目，保留用户原有配置。"""
    raise NotImplementedError("Phase 2 实现")


def reload_driver(runner: CommandRunner) -> OperationResult:
    """重载虚拟显示器驱动，使新分辨率立即生效。"""
    raise NotImplementedError("Phase 2 实现")


def restore_native_display(runner: CommandRunner) -> OperationResult:
    """切回「仅电脑屏幕」，防止虚拟屏残留导致窗口跑到看不见的屏幕。

    对应命令：``DisplaySwitch.exe /internal``
    """
    raise NotImplementedError("Phase 2 实现")
