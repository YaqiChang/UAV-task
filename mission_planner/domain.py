from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import floor, hypot
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


Position2D = Tuple[float, float]


@dataclass(frozen=True)
class AtomicTask:
    """Structured atomic task produced by the upstream task decomposition module."""

    id: str
    action: str
    region_id: str
    position: Position2D
    duration_s: int
    required_capabilities: Set[str] = field(default_factory=set)
    predecessors: Set[str] = field(default_factory=set)
    target_id: Optional[str] = None
    earliest_start_s: int = 0
    latest_finish_s: int = 3600
    priority: int = 1
    merge_policy: str = "bundle_only"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AtomicTask":
        required = {
            "id",
            "action",
            "region_id",
            "position",
            "duration_s",
            "required_capabilities",
            "predecessors",
            "target_id",
            "earliest_start_s",
            "latest_finish_s",
            "priority",
            "merge_policy",
        }
        missing = required - data.keys()
        if missing:
            raise ValueError(f"Atomic task is missing fields: {sorted(missing)}")
        position = data.get("position", [0.0, 0.0])
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise ValueError(f"Task {data.get('id')} position must have two values")
        merge_policy = str(data.get("merge_policy", "bundle_only"))
        if merge_policy not in {"deduplicate", "bundle_only", "never"}:
            raise ValueError(
                f"Task {data.get('id')} has unsupported merge_policy={merge_policy}"
            )
        task = cls(
            id=str(data["id"]),
            action=str(data["action"]).lower(),
            region_id=str(data["region_id"]),
            position=(float(position[0]), float(position[1])),
            duration_s=int(data["duration_s"]),
            required_capabilities=set(map(str, data.get("required_capabilities", []))),
            predecessors=set(map(str, data.get("predecessors", []))),
            target_id=(
                str(data["target_id"]) if data.get("target_id") is not None else None
            ),
            earliest_start_s=int(data.get("earliest_start_s", 0)),
            latest_finish_s=int(data.get("latest_finish_s", 3600)),
            priority=int(data.get("priority", 1)),
            merge_policy=merge_policy,
        )
        if not task.id or not task.action or not task.region_id:
            raise ValueError("Task id, action and region_id must be non-empty")
        if task.duration_s <= 0:
            raise ValueError(f"Task {task.id} duration_s must be positive")
        if task.latest_finish_s <= task.earliest_start_s:
            raise ValueError(f"Task {task.id} has an invalid time window")
        if task.duration_s > task.latest_finish_s - task.earliest_start_s:
            raise ValueError(f"Task {task.id} does not fit its time window")
        if task.priority < 0:
            raise ValueError(f"Task {task.id} priority must be non-negative")
        return task

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["position"] = list(self.position)
        data["required_capabilities"] = sorted(self.required_capabilities)
        data["predecessors"] = sorted(self.predecessors)
        return data


@dataclass
class MissionPackage:
    """A group of atomic tasks intended to be executed by one UAV."""

    id: str
    tasks: List[AtomicTask]

    @property
    def task_ids(self) -> Set[str]:
        return {task.id for task in self.tasks}

    @property
    def capabilities(self) -> Set[str]:
        result: Set[str] = set()
        for task in self.tasks:
            result.update(task.required_capabilities)
        return result

    @property
    def duration_s(self) -> int:
        return sum(task.duration_s for task in self.tasks)

    @property
    def priority(self) -> int:
        return max(task.priority for task in self.tasks)

    @property
    def earliest_start_s(self) -> int:
        return max(task.earliest_start_s for task in self.tasks)

    @property
    def latest_finish_s(self) -> int:
        return min(task.latest_finish_s for task in self.tasks)

    @property
    def centroid(self) -> Position2D:
        if not self.tasks:
            return (0.0, 0.0)
        return (
            sum(task.position[0] for task in self.tasks) / len(self.tasks),
            sum(task.position[1] for task in self.tasks) / len(self.tasks),
        )

    @property
    def region_ids(self) -> Set[str]:
        return {task.region_id for task in self.tasks}

    @property
    def target_ids(self) -> Set[str]:
        return {task.target_id for task in self.tasks if task.target_id is not None}

    @property
    def actions(self) -> Set[str]:
        return {task.action for task in self.tasks}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_ids": sorted(self.task_ids),
            "actions": sorted(self.actions),
            "region_ids": sorted(self.region_ids),
            "target_ids": sorted(self.target_ids),
            "required_capabilities": sorted(self.capabilities),
            "duration_s": self.duration_s,
            "priority": self.priority,
            "earliest_start_s": self.earliest_start_s,
            "latest_finish_s": self.latest_finish_s,
            "centroid": list(self.centroid),
            "tasks": [task.to_dict() for task in self.tasks],
        }


@dataclass(frozen=True)
class UAV:
    id: str
    position: Position2D
    max_speed_mps: float
    battery_percent: float
    capabilities: Set[str] = field(default_factory=set)
    energy_budget: int = 1000
    reserve_energy: int = 100
    max_work_s: int = 3600
    available: bool = True

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UAV":
        position_data = data.get("position", [0.0, 0.0])
        if isinstance(position_data, dict):
            position = (
                float(position_data.get("x", 0.0)),
                float(position_data.get("y", 0.0)),
            )
        else:
            position = (float(position_data[0]), float(position_data[1]))
        return cls(
            id=str(data["id"]),
            position=position,
            max_speed_mps=float(data.get("max_speed_mps", data.get("max_speed", 10.0))),
            battery_percent=float(data.get("battery_percent", data.get("battery_level", 100.0))),
            capabilities=set(map(str, data.get("capabilities", []))),
            energy_budget=int(data.get("energy_budget", 1000)),
            reserve_energy=int(data.get("reserve_energy", 100)),
            max_work_s=int(data.get("max_work_s", 3600)),
            available=bool(data.get("available", True)),
        )

    def distance_to(self, position: Position2D) -> float:
        return hypot(self.position[0] - position[0], self.position[1] - position[1])

    @property
    def usable_energy(self) -> int:
        """Energy currently available after preserving the mandatory reserve."""
        charged_energy = floor(self.energy_budget * self.battery_percent / 100.0)
        return max(0, charged_energy - self.reserve_energy)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "position": list(self.position),
            "max_speed_mps": self.max_speed_mps,
            "battery_percent": self.battery_percent,
            "capabilities": sorted(self.capabilities),
            "energy_budget": self.energy_budget,
            "reserve_energy": self.reserve_energy,
            "max_work_s": self.max_work_s,
            "available": self.available,
        }


def load_tasks(raw: Iterable[Dict[str, Any]]) -> List[AtomicTask]:
    tasks = [AtomicTask.from_dict(item) for item in raw]
    ids = [task.id for task in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Atomic task IDs must be unique")
    known = set(ids)
    for task in tasks:
        if task.id in task.predecessors:
            raise ValueError(f"Task {task.id} cannot depend on itself")
        missing = task.predecessors - known
        if missing:
            raise ValueError(f"Task {task.id} references missing predecessors: {sorted(missing)}")
    return tasks


def load_uavs(raw: Iterable[Dict[str, Any]]) -> List[UAV]:
    uavs = [UAV.from_dict(item) for item in raw]
    ids = [uav.id for uav in uavs]
    if len(ids) != len(set(ids)):
        raise ValueError("UAV IDs must be unique")
    for uav in uavs:
        if not uav.id:
            raise ValueError("UAV id must be non-empty")
        if uav.max_speed_mps <= 0:
            raise ValueError(f"UAV {uav.id} max_speed_mps must be positive")
        if not 0 <= uav.battery_percent <= 100:
            raise ValueError(f"UAV {uav.id} battery_percent must be within [0, 100]")
        if uav.energy_budget < 0 or uav.reserve_energy < 0:
            raise ValueError(f"UAV {uav.id} energy values must be non-negative")
        if uav.max_work_s <= 0:
            raise ValueError(f"UAV {uav.id} max_work_s must be positive")
    return uavs
