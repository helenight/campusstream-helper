"""模块 C：Sunshine 检测、虚拟屏 ``device_id`` 提取与 ``sunshine.conf`` 自动配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence

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
    """检测 Sunshine 安装路径、版本、配置文件与服务状态。

    检测顺序：常见安装目录 → 注册表卸载信息 → 用户指定目录；
    服务状态通过 ``Get-Service`` 查询 ``SunshineService``。
    """
    raise NotImplementedError("Phase 2 实现")


def read_config(runner: CommandRunner, config_path: Path) -> SunshineConfigState:
    """解析 ``key = value`` 配置，保留注释与原始行顺序。"""
    raise NotImplementedError("Phase 2 实现")


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
    raise NotImplementedError("Phase 2 实现")


def default_resolution_mode(monitor_count: int) -> ResolutionMode:
    """单设备默认不改变分辨率；多设备默认跟随客户端请求。

    单设备保住 Windows 缩放比例最稳；多设备（平板 + 手机）才需要自动切换。
    UI 仍允许用户手动改回。
    """
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
    raise NotImplementedError("Phase 2 实现")


def restart_service(runner: CommandRunner, status: SunshineStatus) -> OperationResult:
    """重启 Sunshine Windows 服务，使配置生效；Dry-Run 下只记录。"""
    raise NotImplementedError("Phase 2 实现")


def pairing_url(ip: str, *, port: int = PAIRING_PORT) -> str:
    """生成 Sunshine Web 配对页地址，例如 ``https://192.168.1.10:47990``。

    传入 IPv6 时会自动加方括号。
    """
    host = f"[{ip}]" if ":" in ip else ip
    return f"https://{host}:{port}"
