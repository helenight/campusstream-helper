"""模块 A：网络环境检查、校园网诊断、IPv6 修复与防火墙放行。"""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Sequence

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

    @property
    def ethernet_ipv4(self) -> str | None:
        return _first_ipv4(self.adapters, ("ethernet",))

    @property
    def wlan_ipv4(self) -> str | None:
        return _first_ipv4(self.adapters, ("wifi",))


def collect_network_report(
    runner: CommandRunner,
    *,
    sunshine_exe: Path | None = None,
) -> NetworkReport:
    """汇总网卡、IPv4 / IPv6 与防火墙状态。

    使用 PowerShell 结构化输出（JSON），避免解析中文系统上的本地化文本。
    """
    script = _build_network_query_script(FIREWALL_RULE_NAME)
    result = runner.query_powershell(script, timeout_s=25)
    if not result.ok:
        reason = _tail(result.stderr or result.stdout or "未知错误")
        runner.log(f"[网络] 检测失败: {reason}")
        return NetworkReport(
            adapters=(),
            scenario="offline",
            preferred_ipv4=None,
            preferred_ipv6=None,
            firewall_rule_present=False,
            warnings=(f"网络检测失败：{reason}",),
            generated_at=_now_iso(),
        )

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        runner.log(f"[网络] PowerShell 返回了无法解析的 JSON: {exc}")
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    report = _build_report(payload, sunshine_exe=sunshine_exe)
    runner.log(
        f"[网络] 检测完成：{len(report.adapters)} 个网卡，"
        f"推荐地址 {report.recommended_client_ip or '无'}"
    )
    for warning in report.warnings:
        runner.log(f"[网络] {warning}")
    return report


def enable_ipv6_binding(runner: CommandRunner, adapter_name: str) -> OperationResult:
    """为指定网卡重新启用 ``ms_tcpip6`` 绑定。

    对应 PowerShell：
    ``Enable-NetAdapterBinding -Name <adapter_name> -ComponentID ms_tcpip6``

    Dry-Run 下只输出将要执行的命令，不修改系统。
    """
    name = adapter_name.strip()
    if not name:
        return OperationResult.failure("网卡名称不能为空")
    safe_name = _ps_quote(name)
    script = (
        f"$binding = Get-NetAdapterBinding -Name '{safe_name}' "
        f"-ComponentID ms_tcpip6 -ErrorAction Stop\n"
        "if (-not $binding) { throw '未找到 ms_tcpip6 绑定' }\n"
        f"Enable-NetAdapterBinding -Name '{safe_name}' "
        f"-ComponentID ms_tcpip6 -ErrorAction Stop\n"
        f"(Get-NetAdapterBinding -Name '{safe_name}' "
        f"-ComponentID ms_tcpip6).Enabled\n"
    )
    result = runner.mutate_powershell(script)
    if result.simulated:
        return OperationResult.success(
            f"[DRY-RUN] 将为「{name}」启用 IPv6（ms_tcpip6）",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            f"为「{name}」启用 IPv6 失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(
        f"已为「{name}」启用 IPv6 协议",
        details=(f"绑定状态: {_tail(result.stdout)}",),
    )


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
    executable = Path(sunshine_exe)
    if not executable.exists():
        return OperationResult.failure(f"找不到 sunshine.exe：{executable}")

    if _firewall_rule_exists(runner, rule_name):
        return OperationResult.success(
            f"防火墙规则「{rule_name}」已存在，无需重复添加",
            simulated=runner.dry_run,
        )

    argv = [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={rule_name}",
        "dir=in",
        "action=allow",
        f"program={executable.resolve()}",
        "enable=yes",
        "profile=private,public",
        "protocol=any",
    ]
    result = runner.mutate(argv)
    if result.simulated:
        return OperationResult.success(
            f"[DRY-RUN] 将放行 {executable.name}（专用 + 公用网络）",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            "添加防火墙规则失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(
        f"已为 {executable.name} 添加入站放行规则（专用 + 公用网络）"
    )


def remove_firewall_rule(
    runner: CommandRunner,
    *,
    rule_name: str = FIREWALL_RULE_NAME,
) -> OperationResult:
    """删除本工具创建的防火墙规则，用于一键回滚。"""
    if not _firewall_rule_exists(runner, rule_name):
        return OperationResult.success(
            f"防火墙规则「{rule_name}」不存在，无需删除",
            simulated=runner.dry_run,
        )

    argv = [
        "netsh",
        "advfirewall",
        "firewall",
        "delete",
        "rule",
        f"name={rule_name}",
    ]
    result = runner.mutate(argv)
    if result.simulated:
        return OperationResult.success(
            f"[DRY-RUN] 将删除防火墙规则「{rule_name}」",
            simulated=True,
        )
    if not result.ok:
        return OperationResult.failure(
            "删除防火墙规则失败",
            details=(_tail(result.stderr or result.stdout),),
        )
    return OperationResult.success(f"已删除防火墙规则「{rule_name}」")


# ----------------------------------------------------------------------
# 内部解析与判定
# ----------------------------------------------------------------------


def _build_network_query_script(rule_name: str) -> str:
    safe_rule = _ps_quote(rule_name)
    return f"""
$ErrorActionPreference = 'SilentlyContinue'
$adapters = @(Get-NetAdapter | Select-Object Name, InterfaceDescription, Status, MediaType, NdisPhysicalMedium)
$ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 | Select-Object InterfaceAlias, IPAddress, AddressState, PrefixOrigin, SuffixOrigin)
$ipv6 = @(Get-NetIPAddress -AddressFamily IPv6 | Select-Object InterfaceAlias, IPAddress, AddressState, PrefixOrigin, SuffixOrigin, ValidLifetime)
$bindings = @(Get-NetAdapterBinding -ComponentID ms_tcpip6 | Select-Object Name, Enabled)
$firewall = @(Get-NetFirewallRule -DisplayName '{safe_rule}' | Select-Object DisplayName, Enabled, Direction, Action, Profile)
[pscustomobject]@{{
  adapters = $adapters
  ipv4 = $ipv4
  ipv6 = $ipv6
  bindings = $bindings
  firewall = $firewall
}} | ConvertTo-Json -Depth 5 -Compress
""".strip()


def _build_report(data: dict[str, Any], *, sunshine_exe: Path | None) -> NetworkReport:
    adapter_records = _as_list(data.get("adapters"))
    ipv4_by_alias = _group_ipv4(_as_list(data.get("ipv4")))
    ipv6_by_alias = _group_ipv6(_as_list(data.get("ipv6")))
    bindings = _binding_map(_as_list(data.get("bindings")))

    alias_names: dict[str, str] = {}
    for record in adapter_records:
        name = str(record.get("Name") or record.get("name") or "").strip()
        if name:
            alias_names[name.lower()] = name
    for alias in (*ipv4_by_alias, *ipv6_by_alias):
        alias_names.setdefault(alias.lower(), alias)

    adapters: list[AdapterInfo] = []
    for record in adapter_records:
        name = str(record.get("Name") or record.get("name") or "").strip()
        if not name:
            continue
        description = str(record.get("InterfaceDescription") or name)
        status = str(record.get("Status") or "")
        media_type = str(record.get("MediaType") or "")
        physical = record.get("NdisPhysicalMedium")
        ipv4 = _pick_ipv4(ipv4_by_alias.get(name.lower(), []))
        ipv6 = tuple(_ipv6_values(ipv6_by_alias.get(name.lower(), [])))
        is_up = _is_up_status(status) or bool(ipv4 or ipv6)
        adapters.append(
            AdapterInfo(
                name=name,
                description=description,
                kind=_classify_adapter(name, description, media_type, physical),
                is_up=is_up,
                ipv4=ipv4,
                global_ipv6=ipv6,
                ipv6_binding_enabled=bindings.get(name.lower(), True),
            )
        )

    known = {adapter.name.lower() for adapter in adapters}
    for alias_lower, alias in alias_names.items():
        if alias_lower in known:
            continue
        ipv4 = _pick_ipv4(ipv4_by_alias.get(alias_lower, []))
        ipv6 = tuple(_ipv6_values(ipv6_by_alias.get(alias_lower, [])))
        if not ipv4 and not ipv6:
            continue
        adapters.append(
            AdapterInfo(
                name=alias,
                description=alias,
                kind=_classify_adapter(alias, ""),
                is_up=True,
                ipv4=ipv4,
                global_ipv6=ipv6,
                ipv6_binding_enabled=bindings.get(alias_lower, True),
            )
        )

    adapters.sort(key=lambda item: (_kind_priority(item.kind), item.name.lower()))

    ethernet_ready = any(
        item.kind == "ethernet" and item.is_up and (item.ipv4 or item.global_ipv6)
        for item in adapters
    )
    wifi_ready = any(
        item.kind == "wifi" and item.is_up and (item.ipv4 or item.global_ipv6)
        for item in adapters
    )
    if ethernet_ready:
        scenario: NetworkScenario = "ethernet_ready"
    elif wifi_ready:
        scenario = "wifi_only"
    else:
        scenario = "offline"

    preferred_ipv4 = _first_ipv4(adapters, ("ethernet", "wifi", "other", "virtual"))
    preferred_ipv6 = _first_ipv6(adapters, ("ethernet", "wifi", "other", "virtual"))
    firewall_present = _firewall_present(_as_list(data.get("firewall")))

    warnings: list[str] = []
    if scenario == "wifi_only":
        warnings.append(CAMPUS_WIFI_WARNING)
    elif scenario == "offline":
        warnings.append("未检测到可用的局域网地址，请检查网线 / Wi-Fi 连接。")

    eth_ipv4 = _first_ipv4(adapters, ("ethernet",))
    wifi_ipv4 = _first_ipv4(adapters, ("wifi",))
    if eth_ipv4 and wifi_ipv4:
        warnings.append(
            "检测到有线与无线同时在线。建议在 Moonlight 中手动填写有线 IP，"
            "避免串流流量走 Wi-Fi 造成高延迟。"
        )
    elif scenario == "ethernet_ready" and wifi_ipv4 and not eth_ipv4:
        warnings.append(
            "有线网卡没有可用 IPv4，当前将使用无线 IP；"
            "校园网可能隔离无线设备，建议检查宿舍有线连接。"
        )

    disabled_bindings = [
        item.name
        for item in adapters
        if item.is_up and not item.ipv6_binding_enabled and item.kind in {"ethernet", "wifi"}
    ]
    if disabled_bindings:
        warnings.append(
            "以下网卡的 IPv6 协议（ms_tcpip6）未启用："
            + "、".join(disabled_bindings)
            + "。若校园网依赖 IPv6 直连，请先一键修复。"
        )

    if adapters and not preferred_ipv4 and not preferred_ipv6:
        warnings.append("检测到网卡，但没有可用的 IPv4 / 全局 IPv6 地址。")

    if sunshine_exe is not None and not Path(sunshine_exe).exists():
        warnings.append(f"未找到 sunshine.exe：{sunshine_exe}")

    return NetworkReport(
        adapters=tuple(adapters),
        scenario=scenario,
        preferred_ipv4=preferred_ipv4,
        preferred_ipv6=preferred_ipv6,
        firewall_rule_present=firewall_present,
        warnings=tuple(warnings),
        generated_at=_now_iso(),
    )


def _classify_adapter(
    name: str,
    description: str = "",
    media_type: str = "",
    physical_medium: Any = None,
) -> AdapterKind:
    text = f"{name} {description} {media_type}".lower()
    virtual_markers = (
        "vmware",
        "virtualbox",
        "hyper-v",
        "vethernet",
        "loopback",
        "tap-",
        "wintun",
        "tailscale",
        "zerotier",
        "wireguard",
        "vpn",
        "bluetooth",
        "npcap",
    )
    if any(marker in text for marker in virtual_markers):
        return "virtual"
    if any(marker in text for marker in ("wi-fi", "wifi", "wlan", "wireless", "802.11")):
        return "wifi"
    if any(marker in text for marker in ("ethernet", "802.3", "以太网", "本地连接")):
        return "ethernet"
    if str(physical_medium) == "9":
        return "wifi"
    return "other"


def _is_global_ipv6(address: str, suffix_origin: str = "") -> bool:
    if suffix_origin.strip().lower() in {"random", "temporary"}:
        return False
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if parsed.version != 6:
        return False
    if parsed.is_link_local or parsed.is_loopback or parsed.is_multicast or parsed.is_unspecified:
        return False
    return parsed.is_global


def _is_usable_ipv4(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if parsed.version != 4:
        return False
    return not (
        parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def _group_ipv4(records: list[Any]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        alias = str(record.get("InterfaceAlias") or "").strip()
        address = str(record.get("IPAddress") or "").strip()
        state = str(record.get("AddressState") or "").strip()
        if alias and _is_usable_ipv4(address):
            grouped.setdefault(alias.lower(), []).append((address, state))
    return grouped


def _group_ipv6(records: list[Any]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        alias = str(record.get("InterfaceAlias") or "").strip()
        address = str(record.get("IPAddress") or "").strip()
        state = str(record.get("AddressState") or "").strip()
        suffix = str(record.get("SuffixOrigin") or "").strip()
        if alias and _is_global_ipv6(address, suffix):
            grouped.setdefault(alias.lower(), []).append((address, state))
    return grouped


def _pick_ipv4(entries: Sequence[tuple[str, str]]) -> str | None:
    if not entries:
        return None
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for address, state in entries:
        if address not in seen:
            seen.add(address)
            unique.append((address, state))
    preferred = [
        address
        for address, state in unique
        if state.strip().lower() in {"preferred", "首选"}
    ]
    return (preferred or [address for address, _ in unique])[0]


def _ipv6_values(entries: Sequence[tuple[str, str]]) -> list[str]:
    if not entries:
        return []
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for address, state in entries:
        if address not in seen:
            seen.add(address)
            unique.append((address, state))
    ordered = sorted(
        unique,
        key=lambda item: 0 if item[1].strip().lower() in {"preferred", "首选"} else 1,
    )
    return [address for address, _ in ordered]


def _binding_map(records: list[Any]) -> dict[str, bool]:
    result: dict[str, bool] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("Name") or "").strip()
        enabled = _as_bool(record.get("Enabled"))
        if name and enabled is not None:
            result[name.lower()] = enabled
    return result


def _firewall_present(records: list[Any]) -> bool:
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get("DisplayName") or "").strip()
        if name.lower() != FIREWALL_RULE_NAME.lower():
            continue
        enabled = _as_bool(record.get("Enabled"))
        if enabled is None or enabled:
            return True
    return False


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "enabled", "enable", "on"}:
            return True
        if normalized in {"false", "0", "no", "disabled", "disable", "off"}:
            return False
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_up_status(value: str) -> bool:
    return value.strip().lower() in {"up", "connected", "已连接", "已启用"}


def _kind_priority(kind: AdapterKind) -> int:
    return {"ethernet": 0, "wifi": 1, "other": 2, "virtual": 3}[kind]


def _first_ipv4(
    adapters: Sequence[AdapterInfo],
    kinds: Sequence[AdapterKind],
) -> str | None:
    for kind in kinds:
        for adapter in adapters:
            if adapter.kind == kind and adapter.is_up and adapter.ipv4:
                return adapter.ipv4
    return None


def _first_ipv6(
    adapters: Sequence[AdapterInfo],
    kinds: Sequence[AdapterKind],
) -> str | None:
    for kind in kinds:
        for adapter in adapters:
            if adapter.kind == kind and adapter.is_up and adapter.global_ipv6:
                return adapter.global_ipv6[0]
    return None


def _firewall_rule_exists(runner: CommandRunner, rule_name: str) -> bool:
    result = runner.query(
        ["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"],
        timeout_s=15,
    )
    if not result.ok:
        return False
    return rule_name.lower() in result.stdout.lower()


def _ps_quote(value: str) -> str:
    return value.replace("'", "''")


def _tail(text: str, limit: int = 300) -> str:
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "无输出"
    return lines[-1][:limit]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
