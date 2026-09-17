"""Schemas used by the front-end mission allocator.

The legacy planner uses :class:`AtomicTask` and :class:`UAV`.  The models in
this file represent the richer planner-to-allocator contract and deliberately
keep source versions and unresolved dependencies visible in the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


def _as_str_set(value: Any) -> Set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    return {str(item) for item in value}


def _as_int(value: Any, default: int) -> int:
    if value is None:
        return default
    return int(value)


def _canonical_token(value: Any) -> str:
    return str(value).strip().upper().replace("-", "_")


@dataclass(frozen=True)
class TimeWindow:
    start_s: Optional[int] = None
    end_s: Optional[int] = None

    @classmethod
    def from_dict(cls, value: Optional[Mapping[str, Any]]) -> "TimeWindow":
        value = value or {}
        return cls(
            start_s=None if value.get("start") is None else int(value["start"]),
            end_s=None if value.get("end") is None else int(value["end"]),
        )

    def validate(self, task_id: str) -> None:
        if self.start_s is not None and self.end_s is not None:
            if self.end_s <= self.start_s:
                raise ValueError(f"Task {task_id} has an invalid time window")

    def to_dict(self) -> Dict[str, Optional[int]]:
        return {"start": self.start_s, "end": self.end_s}


@dataclass(frozen=True)
class NormalizedTask:
    task_id: str
    task_type: str
    target_area_id: str
    duration_sec: int
    priority: int
    time_window: TimeWindow
    required_payloads: Tuple[str, ...] = ()
    required_capabilities: Tuple[str, ...] = ()
    depends_on: Tuple[str, ...] = ()
    min_altitude_m: Optional[float] = None
    max_altitude_m: Optional[float] = None
    target_position: Optional[Tuple[float, float]] = None
    source: Dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "NormalizedTask":
        task_id = str(raw.get("task_id", raw.get("id", ""))).strip()
        if not task_id:
            raise ValueError("Each task must contain task_id or id")
        duration = _as_int(raw.get("duration_sec", raw.get("duration_s")), 0)
        if duration <= 0:
            raise ValueError(f"Task {task_id} duration must be positive")
        constraints = raw.get("constraints") or {}
        window = TimeWindow.from_dict(raw.get("time_window"))
        window.validate(task_id)
        position = raw.get("position")
        if isinstance(position, Mapping):
            position = [position.get("x", 0.0), position.get("y", 0.0)]
        target_position = None
        if isinstance(position, Sequence) and not isinstance(position, (str, bytes)) and len(position) == 2:
            target_position = (float(position[0]), float(position[1]))
        return cls(
            task_id=task_id,
            task_type=str(raw.get("task_type", raw.get("action", "UNKNOWN"))).upper(),
            target_area_id=str(raw.get("target_area_id", raw.get("region_id", ""))),
            duration_sec=duration,
            priority=int(raw.get("priority", 1)),
            time_window=window,
            required_payloads=tuple(
                sorted(_canonical_token(item) for item in _as_str_set(raw.get("required_payloads")))
            ),
            required_capabilities=tuple(
                sorted(_as_str_set(raw.get("required_capabilities")))
            ),
            depends_on=tuple(
                sorted(_as_str_set(raw.get("depends_on", raw.get("predecessors"))))
            ),
            min_altitude_m=(
                float(constraints["min_altitude_m"])
                if constraints.get("min_altitude_m") is not None
                else None
            ),
            max_altitude_m=(
                float(constraints["max_altitude_m"])
                if constraints.get("max_altitude_m") is not None
                else None
            ),
            target_position=target_position,
            source=dict(raw),
        )

    def to_dict(self) -> Dict[str, Any]:
        constraints: Dict[str, Any] = {}
        if self.min_altitude_m is not None:
            constraints["min_altitude_m"] = self.min_altitude_m
        if self.max_altitude_m is not None:
            constraints["max_altitude_m"] = self.max_altitude_m
        result = {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "target_area_id": self.target_area_id,
            "duration_sec": self.duration_sec,
            "priority": self.priority,
            "time_window": self.time_window.to_dict(),
            "required_payloads": list(self.required_payloads),
            "required_capabilities": list(self.required_capabilities),
            "depends_on": list(self.depends_on),
            "constraints": constraints,
        }
        if self.target_position is not None:
            result["position"] = list(self.target_position)
        return result


@dataclass(frozen=True)
class NormalizedGroup:
    group_id: str
    member_task_ids: Tuple[str, ...]
    group_type: str
    target_area_ids: Tuple[str, ...]
    priority: int
    duration_sec: int
    required_payloads: Tuple[str, ...]
    required_capabilities: Tuple[str, ...]
    time_window: TimeWindow
    min_altitude_m: Optional[float]
    max_altitude_m: Optional[float]
    depends_on_groups: Tuple[str, ...] = ()
    external_dependencies: Tuple[str, ...] = ()
    estimated_transit_time_sec: int = 0
    estimated_workload_sec: int = 0
    source: Dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def total_workload_sec(self) -> int:
        return self.estimated_workload_sec or self.duration_sec + self.estimated_transit_time_sec

    def to_dict(self) -> Dict[str, Any]:
        constraints: Dict[str, Any] = {}
        if self.min_altitude_m is not None:
            constraints["min_altitude_m"] = self.min_altitude_m
        if self.max_altitude_m is not None:
            constraints["max_altitude_m"] = self.max_altitude_m
        return {
            "group_id": self.group_id,
            "member_task_ids": list(self.member_task_ids),
            "group_type": self.group_type,
            "target_area_ids": list(self.target_area_ids),
            "combined_priority": self.priority,
            "estimated_workload_sec": self.estimated_workload_sec,
            "required_payloads": list(self.required_payloads),
            "required_capabilities": list(self.required_capabilities),
            "time_window": self.time_window.to_dict(),
            "constraints": constraints,
            "depends_on_groups": list(self.depends_on_groups),
            "external_dependencies": list(self.external_dependencies),
            "estimated_transit_time_sec": self.estimated_transit_time_sec,
        }


@dataclass(frozen=True)
class AircraftResource:
    aircraft_id: str
    model: str
    available: bool
    flight_capabilities: Set[str]
    installed_payloads: Set[str]
    position: Optional[Tuple[float, float]] = None
    remaining_endurance_sec: int = 3600
    max_work_sec: int = 3600
    current_altitude_m: Optional[float] = None
    service_ceiling_m: Optional[float] = None
    cruise_speed_mps: Optional[float] = None
    fuel_burn_reference: Optional[float] = None
    battery_percent: Optional[float] = None
    source: Dict[str, Any] = field(default_factory=dict, compare=False)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AircraftResource":
        aircraft_id = str(raw.get("aircraft_id", raw.get("id", ""))).strip()
        if not aircraft_id:
            raise ValueError("Each aircraft must contain aircraft_id or id")
        position = raw.get("position")
        if isinstance(position, Mapping):
            position = [position.get("x", 0.0), position.get("y", 0.0)]
        point = None
        if isinstance(position, Sequence) and not isinstance(position, (str, bytes)) and len(position) == 2:
            point = (float(position[0]), float(position[1]))
        flight_capabilities = _as_str_set(
            raw.get("flight_capabilities", raw.get("capabilities"))
        )
        installed_payloads = _as_str_set(
            raw.get("installed_payloads", raw.get("payloads"))
        )
        return cls(
            aircraft_id=aircraft_id,
            model=str(raw.get("model", "UNKNOWN")).upper(),
            available=bool(raw.get("available", True)),
            flight_capabilities={item.upper() for item in flight_capabilities},
            installed_payloads={item.upper() for item in installed_payloads},
            position=point,
            remaining_endurance_sec=int(raw.get("remaining_endurance_sec", raw.get("max_work_sec", raw.get("max_work_s", 3600)))),
            max_work_sec=int(raw.get("max_work_sec", raw.get("max_work_s", 3600))),
            current_altitude_m=(
                float(raw["current_altitude_m"])
                if raw.get("current_altitude_m") is not None
                else None
            ),
            service_ceiling_m=(
                float(raw["service_ceiling_m"])
                if raw.get("service_ceiling_m") is not None
                else None
            ),
            cruise_speed_mps=(
                float(raw["cruise_speed_mps"])
                if raw.get("cruise_speed_mps") is not None
                else None
            ),
            fuel_burn_reference=(
                float(raw["fuel_burn_reference"])
                if raw.get("fuel_burn_reference") is not None
                else None
            ),
            battery_percent=(
                float(raw["battery_percent"])
                if raw.get("battery_percent") is not None
                else None
            ),
            source=dict(raw),
        )

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "aircraft_id": self.aircraft_id,
            "model": self.model,
            "available": self.available,
            "flight_capabilities": sorted(self.flight_capabilities),
            "installed_payloads": sorted(self.installed_payloads),
            "remaining_endurance_sec": self.remaining_endurance_sec,
            "max_work_sec": self.max_work_sec,
        }
        if self.position is not None:
            result["position"] = list(self.position)
        for key, value in (
            ("current_altitude_m", self.current_altitude_m),
            ("service_ceiling_m", self.service_ceiling_m),
            ("cruise_speed_mps", self.cruise_speed_mps),
            ("fuel_burn_reference", self.fuel_burn_reference),
            ("battery_percent", self.battery_percent),
        ):
            if value is not None:
                result[key] = value
        return result
