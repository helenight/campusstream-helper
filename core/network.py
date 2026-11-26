"""模块 A：网络环境检查、校园网诊断、IPv6 修复与防火墙放行。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from core.common import CommandRunner, OperationResult

AdapterKind = Literal["ethernet", "wifi", "virtual", "other"]
NetworkScenario = Literal["ethernet_ready", "wifi_only", "offline"]

FIREWALL_RULE_NAME = "CampusStream Helper - Sunshine"

# Sunshine 的默认端口，供 UI 提示与防火墙排查使用。
SUNSHINE_PORTS: tuple[int, ...] = (47984, 47989, 48010)

CAMPUS_WIFI_WARNING = (
    "检测到当前仅通过无线网络在线。高校校园网通常启用了 AP 隔离，"
    "iPad / 手机可能无法直连本机；建议 PC 优先连接宿舍有线网，"
    "或改用 Tailscale / 星空组网等跨网方案。"
)


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    """一块物理 / 虚拟网卡的可用地址摘要。"""

    name: str
    description: str
    kind: AdapterKind
    is_up: bool
    ipv4: str | None
    global_ipv6: tuple[str, ...]
    ipv6_binding_enabled: bool


@dataclass(frozen=True, slots=True)
class NetworkReport:
    """Step 1 环境自检使用的网络快照。"""

    adapters: tuple[AdapterInfo, ...]
    scenario: NetworkScenario
    preferred_ipv4: str | None
    preferred_ipv6: str | None
    firewall_rule_present: bool
    warnings: tuple[str, ...]
    generated_at: str

    @property
    def recommended_client_ip(self) -> str | None:
        """给 Moonlight 填写的地址：有线 IPv4 → 无线 IPv4 → 全局 IPv6。"""
        return self.preferred_ipv4 or self.preferred_ipv6

    @property
    def is_campus_wifi_only(self) -> bool:
        return self.scenario == "wifi_only"


def collect_network_report(
    runner: CommandRunner,
    *,
    sunshine_exe: Path | None = None,
) -> NetworkReport:
    """汇总网卡、IPv4 / IPv6 与防火墙状态。

    Phase 2 实现要点：
    - 通过 ``Get-NetAdapter`` / ``Get-NetIPAddress`` 区分 Ethernet 与 Wi-Fi；
    - IPv4 仅接受在线网卡上的非 APIPA（``169.254.*``）地址；
    - 全局 IPv6 排除 ``fe80::/10``、``::1`` 与 Temporary 地址，优先 ``2000::/3``；
    - 仅无线在线时写入 :data:`CAMPUS_WIFI_WARNING`；
    - ``sunshine_exe`` 不为 None 时检查同名防火墙规则是否存在。
    """
    raise NotImplementedError("Phase 2 实现")


def enable_ipv6_binding(runner: CommandRunner, adapter_name: str) -> OperationResult:
    """为指定网卡重新启用 ``ms_tcpip6`` 绑定。

    对应 PowerShell：
    ``Enable-NetAdapterBinding -Name <adapter_name> -ComponentID ms_tcpip6``

    Dry-Run 下只输出将要执行的命令，不修改系统。
    """
    raise NotImplementedError("Phase 2 实现")


def ensure_firewall_rule(
    runner: CommandRunner,
    sunshine_exe: Path,
    *,
    rule_name: str = FIREWALL_RULE_NAME,
) -> OperationResult:
    """为 ``sunshine.exe`` 添加入站放行规则（专用 + 公用网络）。

    使用 ``netsh advfirewall``，按程序放行，覆盖 Sunshine 的全部端口。
    已存在同名规则时视为成功，不重复添加。
    """
    raise NotImplementedError("Phase 2 实现")


def remove_firewall_rule(
    runner: CommandRunner,
    *,
    rule_name: str = FIREWALL_RULE_NAME,
) -> OperationResult:
    """删除本工具创建的防火墙规则，用于一键回滚。"""
    raise NotImplementedError("Phase 2 实现")
