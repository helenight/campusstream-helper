"""Step 1 五项检测的状态模型与纯函数推导（不依赖 Qt）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from core.gpu import GpuInfo
from core.network import NetworkReport
from core.power import PowerState
from core.sunshine import SunshineStatus
from core.vdd import VddStatus

DetectKey = Literal["admin", "gpu", "sunshine", "vdd", "network"]
ActionKind = Literal[
    "download_sunshine",
    "download_vdd",
    "relaunch_admin",
    "restart_sunshine",
    "fix_ipv6",
    "add_firewall",
    "open_device_manager",
]


class DetectionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    READY = "ready"
    WARNING = "warning"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DetectionAction:
    kind: ActionKind
    label: str
    payload: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DetectionItem:
    key: DetectKey
    label: str
    state: DetectionState
    summary: str
    detail: tuple[tuple[str, str], ...] = ()
    actions: tuple[DetectionAction, ...] = ()


ITEM_LABELS: dict[DetectKey, str] = {
    "admin": "管理员权限",
    "gpu": "显卡",
    "sunshine": "Sunshine 服务端",
    "vdd": "虚拟显示驱动",
    "network": "网络与防火墙",
}

ITEM_ORDER: tuple[DetectKey, ...] = ("admin", "gpu", "sunshine", "vdd", "network")


def pending_items() -> tuple[DetectionItem, ...]:
    return tuple(
        DetectionItem(key, ITEM_LABELS[key], DetectionState.PENDING, "等待检测")
        for key in ITEM_ORDER
    )


def running_item(key: DetectKey) -> DetectionItem:
    return DetectionItem(
        key,
        ITEM_LABELS[key],
        DetectionState.RUNNING,
        "正在检测…",
    )


def failed_item(key: DetectKey, reason: str) -> DetectionItem:
    return DetectionItem(
        key,
        ITEM_LABELS[key],
        DetectionState.FAILED,
        "检测失败",
        detail=(("失败原因", reason or "未知错误"),),
    )


def admin_item(is_admin: bool, power: PowerState | None) -> DetectionItem:
    detail: list[tuple[str, str]] = [
        ("当前权限", "管理员" if is_admin else "普通用户"),
    ]
    if power is None:
        detail.append(("电源方案", "读取失败"))
        detail.append(("交流电源", "未知"))
    else:
        detail.append(("电源方案", power.scheme_name))
        detail.append(
            (
                "交流电源",
                f"睡眠 {_duration(power.ac_sleep_seconds)} · "
                f"关屏 {_duration(power.ac_display_off_seconds)}",
            )
        )
    actions: tuple[DetectionAction, ...] = ()
    if not is_admin:
        actions = (DetectionAction("relaunch_admin", "以管理员身份重启"),)
    return DetectionItem(
        key="admin",
        label=ITEM_LABELS["admin"],
        state=DetectionState.READY if is_admin else DetectionState.WARNING,
        summary="管理员权限已就绪" if is_admin else "普通用户，仅可 Dry-Run 或查看检测结果",
        detail=tuple(detail),
        actions=actions,
    )


def gpu_item(info: GpuInfo) -> DetectionItem:
    if info.error is not None or not info.name:
        return failed_item("gpu", info.error or "未检测到显卡适配器")
    detail = (
        ("显卡型号", info.name),
        ("驱动版本", info.driver_version or "未知"),
        ("设备状态", info.status or "未知"),
    )
    if info.is_basic_adapter:
        return DetectionItem(
            key="gpu",
            label=ITEM_LABELS["gpu"],
            state=DetectionState.WARNING,
            summary=info.detail,
            detail=detail,
            actions=(
                DetectionAction("open_device_manager", "打开设备管理器"),
            ),
        )
    return DetectionItem(
        key="gpu",
        label=ITEM_LABELS["gpu"],
        state=DetectionState.READY,
        summary=info.detail,
        detail=detail,
    )


def sunshine_item(status: SunshineStatus | None) -> DetectionItem:
    if status is None:
        return failed_item("sunshine", "未能读取 Sunshine 状态")
    detail = (
        ("安装目录", str(status.install_dir or "未检测到")),
        ("服务状态", f"{status.service_name} · {status.service_state}"),
        ("版本", status.version or "未知"),
        ("配置文件", str(status.config_path or "未找到 sunshine.conf")),
    )
    if not status.installed or status.executable is None:
        return DetectionItem(
            key="sunshine",
            label=ITEM_LABELS["sunshine"],
            state=DetectionState.WARNING,
            summary="未安装 Sunshine",
            detail=detail,
            actions=(
                DetectionAction("download_sunshine", "一键自动下载安装"),
            ),
        )
    if status.service_state != "running":
        return DetectionItem(
            key="sunshine",
            label=ITEM_LABELS["sunshine"],
            state=DetectionState.WARNING,
            summary="Sunshine 已安装，但服务未运行",
            detail=detail,
            actions=(
                DetectionAction("restart_sunshine", "启动 Sunshine 服务"),
            ),
        )
    return DetectionItem(
        key="sunshine",
        label=ITEM_LABELS["sunshine"],
        state=DetectionState.READY,
        summary=f"已安装 · {status.version or '版本未知'} · 服务运行中",
        detail=detail,
    )


def vdd_item(status: VddStatus | None) -> DetectionItem:
    if status is None:
        return failed_item("vdd", "未能读取虚拟显示驱动状态")
    detail = (
        ("配置文件", str(status.settings_path or "未找到 vdd_settings.xml")),
        ("XML 结构", status.schema),
        ("虚拟屏数量", str(status.configured_monitor_count)),
        ("分辨率条目", str(status.configured_resolution_count)),
        ("驱动版本", status.driver_version or "未知"),
    )
    if not status.installed:
        return DetectionItem(
            key="vdd",
            label=ITEM_LABELS["vdd"],
            state=DetectionState.WARNING,
            summary="未安装 Virtual Display Driver",
            detail=detail,
            actions=(DetectionAction("download_vdd", "一键自动下载安装"),),
        )
    if status.settings_path is None:
        return DetectionItem(
            key="vdd",
            label=ITEM_LABELS["vdd"],
            state=DetectionState.WARNING,
            summary="已安装 VDD，但缺少 vdd_settings.xml",
            detail=detail,
            actions=(DetectionAction("download_vdd", "重新部署 VDD"),),
        )
    return DetectionItem(
        key="vdd",
        label=ITEM_LABELS["vdd"],
        state=DetectionState.READY,
        summary=(
            f"已安装 · {status.configured_monitor_count} 个虚拟屏 · "
            f"{status.configured_resolution_count} 条分辨率"
        ),
        detail=detail,
    )


def network_item(
    report: NetworkReport | None,
    *,
    can_add_firewall: bool = False,
) -> DetectionItem:
    if report is None:
        return failed_item("network", "未能读取网络状态")

    detail: list[tuple[str, str]] = [
        ("网络场景", _scenario_text(report.scenario)),
        ("推荐地址", report.recommended_client_ip or "未获取到可用地址"),
        (
            "防火墙",
            "已放行 Sunshine"
            if report.firewall_rule_present
            else "尚未添加入站规则",
        ),
    ]
    disabled = [
        adapter.name
        for adapter in report.adapters
        if adapter.kind in {"ethernet", "wifi"}
        and adapter.is_up
        and not adapter.ipv6_binding_enabled
    ]
    if disabled:
        detail.append(("IPv6 绑定", "未启用：" + "、".join(disabled)))

    actions: list[DetectionAction] = []
    if disabled:
        actions.append(DetectionAction("fix_ipv6", "修复 IPv6 绑定", tuple(disabled)))
    if not report.firewall_rule_present and can_add_firewall:
        actions.append(DetectionAction("add_firewall", "添加防火墙规则"))

    issues: list[str] = []
    if report.scenario == "wifi_only":
        issues.append("仅无线网络")
    elif report.scenario == "offline":
        issues.append("未联网")
    if disabled:
        issues.append("IPv6 未启用")
    if not report.firewall_rule_present:
        issues.append("防火墙未放行")

    if report.scenario == "ethernet_ready":
        base = (
            f"有线网络就绪 · {report.recommended_client_ip}"
            if report.recommended_client_ip
            else "有线网络就绪"
        )
    else:
        base = _scenario_text(report.scenario)

    return DetectionItem(
        key="network",
        label=ITEM_LABELS["network"],
        state=DetectionState.READY if not issues else DetectionState.WARNING,
        summary=base + ("；" + "、".join(issues) if issues else ""),
        detail=tuple(detail),
        actions=tuple(actions),
    )


def summarize(items: tuple[DetectionItem, ...]) -> tuple[str, str]:
    """返回 ``(汇总文案, success|warning|failed|info)``。"""
    states = [item.state for item in items]
    if any(state in {DetectionState.PENDING, DetectionState.RUNNING} for state in states):
        return "正在检测系统环境…", "info"
    failed = states.count(DetectionState.FAILED)
    warning = states.count(DetectionState.WARNING)
    ready = states.count(DetectionState.READY)
    if failed:
        suffix = f"，{warning} 项待处理" if warning else ""
        return f"❌ 检测完成：{failed} 项失败{suffix}", "failed"
    if warning:
        return f"⚠️ 检测完成：发现 {warning} 项待处理", "warning"
    if ready == len(items):
        return "✅ 检测完成：环境全部就绪", "success"
    return "检测完成", "info"


def _duration(seconds: int) -> str:
    if seconds == 0:
        return "永不"
    if seconds < 0:
        return "未知"
    return f"{max(1, (seconds + 30) // 60)} 分钟"


def _scenario_text(scenario: str) -> str:
    return {
        "ethernet_ready": "有线网络就绪",
        "wifi_only": "仅无线网络",
        "offline": "未联网",
    }.get(scenario, "未知")
