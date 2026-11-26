"""Phase 3 UI 与 core 之间的配置计划与执行结果契约。"""

from __future__ import annotations

from dataclasses import dataclass

from core.network import NetworkReport
from core.sunshine import AddressFamily, ResolutionMode, SunshineStatus
from core.vdd import MonitorSpec, Platform, VddStatus


@dataclass(frozen=True, slots=True)
class SetupPlan:
    """用户在 Step 2 选择的完整配置计划。"""

    platforms: tuple[Platform, ...]
    monitors: tuple[MonitorSpec, ...]
    address_family: AddressFamily = "ipv4"
    resolution_mode: ResolutionMode = "manual"
    enable_firewall: bool = True
    apply_power_profile: bool = True
    reload_driver: bool = True
    dry_run: bool = False


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """单个执行步骤的结果，供 Step 3 日志与 Step 4 汇总展示。"""

    key: str
    label: str
    ok: bool
    message: str
    details: tuple[str, ...] = ()
    simulated: bool = False


@dataclass(frozen=True, slots=True)
class SetupSummary:
    """一次完整配置执行的汇总。"""

    plan: SetupPlan
    outcomes: tuple[StepOutcome, ...]
    network: NetworkReport | None = None
    sunshine: SunshineStatus | None = None
    vdd: VddStatus | None = None
    device_id: str | None = None
    recommended_ip: str | None = None
    pairing_url: str | None = None
    rollback_available: bool = False

    @property
    def success(self) -> bool:
        """关键步骤（VDD、首次枚举重启、device_id、配置、最终重启）全部成功。"""
        critical = {
            "vdd",
            "service_initial",
            "device_id",
            "sunshine_config",
            "service",
        }
        critical_outcomes = [item for item in self.outcomes if item.key in critical]
        return bool(critical_outcomes) and all(item.ok for item in critical_outcomes)

    @property
    def failed_outcomes(self) -> tuple[StepOutcome, ...]:
        return tuple(item for item in self.outcomes if not item.ok)

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_outcomes)
