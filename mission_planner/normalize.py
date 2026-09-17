"""Normalize planner and aggregation JSON into the allocator contract."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .capability_model import canonical_payload_token, normalize_payload_requirements
from .schemas import (
    AircraftResource,
    NormalizedGroup,
    NormalizedTask,
    TimeWindow,
)


def _finite_start(window: TimeWindow) -> int:
    return 0 if window.start_s is None else window.start_s


def _compatible_end(windows: Iterable[TimeWindow]) -> Optional[int]:
    ends = [window.end_s for window in windows if window.end_s is not None]
    return min(ends) if ends else None


def _group_window(tasks: Sequence[NormalizedTask], raw: Mapping[str, Any]) -> TimeWindow:
    raw_window = TimeWindow.from_dict(raw.get("time_window"))
    starts = [_finite_start(task.time_window) for task in tasks]
    start = max([_finite_start(raw_window), *starts])
    ends = [raw_window.end_s, _compatible_end(task.time_window for task in tasks)]
    finite_ends = [value for value in ends if value is not None]
    end = min(finite_ends) if finite_ends else None
    return TimeWindow(start_s=start, end_s=end)


def normalize_tasks(plan: Mapping[str, Any]) -> Dict[str, NormalizedTask]:
    raw_tasks = plan.get("tasks", [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("Planner output must contain a non-empty tasks list")
    tasks = [NormalizedTask.from_dict(raw) for raw in raw_tasks]
    result: Dict[str, NormalizedTask] = {}
    for task in tasks:
        if task.task_id in result:
            raise ValueError(f"Duplicate task_id: {task.task_id}")
        result[task.task_id] = task
    return result


def _derived_dependencies(
    member_tasks: Sequence[NormalizedTask],
    task_to_group: Mapping[str, str],
    current_group_id: str,
) -> Tuple[Set[str], Set[str]]:
    group_dependencies: Set[str] = set()
    external_tasks: Set[str] = set()
    member_ids = {task.task_id for task in member_tasks}
    for task in member_tasks:
        for dependency in task.depends_on:
            if dependency in member_ids:
                continue
            dependency_group = task_to_group.get(dependency)
            if dependency_group and dependency_group != current_group_id:
                group_dependencies.add(dependency_group)
            else:
                external_tasks.add(dependency)
    return group_dependencies, external_tasks


def normalize_groups(
    plan: Mapping[str, Any],
    aggregation: Mapping[str, Any],
) -> Tuple[Dict[str, NormalizedTask], List[NormalizedGroup], Dict[str, Any]]:
    tasks = normalize_tasks(plan)
    raw_groups = aggregation.get("task_groups", [])
    if not isinstance(raw_groups, list):
        raise ValueError("Aggregation output task_groups must be a list")
    task_to_group: Dict[str, str] = {}
    for raw_group in raw_groups:
        group_id = str(raw_group.get("group_id", "")).strip()
        for task_id in raw_group.get("member_task_ids", []):
            task_id = str(task_id)
            if task_id not in tasks:
                raise ValueError(f"Group {group_id} references unknown task {task_id}")
            if task_id in task_to_group:
                raise ValueError(f"Task {task_id} appears in multiple task groups")
            task_to_group[task_id] = group_id

    groups: List[NormalizedGroup] = []
    for raw_group in raw_groups:
        group_id = str(raw_group.get("group_id", "")).strip()
        if not group_id:
            raise ValueError("Each task group must contain group_id")
        member_ids = tuple(str(item) for item in raw_group.get("member_task_ids", []))
        if not member_ids:
            raise ValueError(f"Group {group_id} has no member tasks")
        member_tasks = [tasks[item] for item in member_ids]
        payload_tokens = set(
            canonical_payload_token(token)
            for token in raw_group.get("required_payloads", [])
        )
        for task in member_tasks:
            payload_tokens.update(task.required_payloads)
        _, payload_capability_groups = normalize_payload_requirements(payload_tokens)
        capabilities = set(
            str(item).upper()
            for item in raw_group.get("required_capabilities", [])
        )
        for task in member_tasks:
            capabilities.update(item.upper() for item in task.required_capabilities)
        group_dependencies, external_dependencies = _derived_dependencies(
            member_tasks, task_to_group, group_id
        )
        group_dependencies.update(str(item) for item in raw_group.get("depends_on_groups", []))
        external_dependencies.update(
            str(item) for item in raw_group.get("external_dependencies", [])
        )
        window = _group_window(member_tasks, raw_group)
        constraints = raw_group.get("constraints") or {}
        min_altitudes = [
            task.min_altitude_m
            for task in member_tasks
            if task.min_altitude_m is not None
        ]
        max_altitudes = [
            task.max_altitude_m
            for task in member_tasks
            if task.max_altitude_m is not None
        ]
        min_altitude = max(min_altitudes) if min_altitudes else None
        max_altitude = min(max_altitudes) if max_altitudes else None
        if constraints.get("min_altitude_m") is not None:
            min_altitude = max(min_altitude or float("-inf"), float(constraints["min_altitude_m"]))
        if constraints.get("max_altitude_m") is not None:
            max_altitude = min(max_altitude or float("inf"), float(constraints["max_altitude_m"]))
        if min_altitude is not None and max_altitude is not None and min_altitude > max_altitude:
            raise ValueError(f"Group {group_id} has incompatible altitude constraints")
        duration = int(raw_group.get("duration_sec", sum(task.duration_sec for task in member_tasks)))
        workload = int(raw_group.get("estimated_workload_sec", duration))
        groups.append(
            NormalizedGroup(
                group_id=group_id,
                member_task_ids=member_ids,
                group_type=str(raw_group.get("group_type", "UNKNOWN")).upper(),
                target_area_ids=tuple(
                    sorted(
                        set(str(item) for item in raw_group.get("target_area_ids", []))
                        | {task.target_area_id for task in member_tasks if task.target_area_id}
                    )
                ),
                priority=int(raw_group.get("combined_priority", max(task.priority for task in member_tasks))),
                duration_sec=duration,
                required_payloads=tuple(sorted(payload_tokens)),
                required_capabilities=tuple(sorted(capabilities)),
                time_window=window,
                min_altitude_m=min_altitude,
                max_altitude_m=max_altitude,
                depends_on_groups=tuple(sorted(group_dependencies)),
                external_dependencies=tuple(sorted(external_dependencies)),
                estimated_transit_time_sec=int(raw_group.get("estimated_transit_time_sec", 0)),
                estimated_workload_sec=workload,
                source={
                    "raw_group": dict(raw_group),
                    "payload_capability_groups": {
                        key: sorted(value)
                        for key, value in payload_capability_groups.items()
                    },
                },
            )
        )
    metadata = {
        "mission_id": aggregation.get("mission_id", plan.get("mission_id")),
        "plan_version": plan.get("plan_version"),
        "context_version": plan.get("context_version"),
        "aggregation_version": aggregation.get("aggregation_version"),
        "aggregation_status": aggregation.get("status"),
        "unmerged_tasks": [str(item) for item in aggregation.get("unmerged_tasks", [])],
        "warnings": list(aggregation.get("warnings", [])),
        "conflicts": list(aggregation.get("conflicts", [])),
        "task_to_group": task_to_group,
    }
    return tasks, groups, metadata


def normalize_fleet(raw_fleet: Mapping[str, Any]) -> List[AircraftResource]:
    raw_aircraft = raw_fleet.get("aircraft", raw_fleet.get("uavs", []))
    if not isinstance(raw_aircraft, list) or not raw_aircraft:
        raise ValueError("Fleet snapshot must contain a non-empty aircraft list")
    aircraft = [AircraftResource.from_dict(item) for item in raw_aircraft]
    ids = [item.aircraft_id for item in aircraft]
    if len(ids) != len(set(ids)):
        raise ValueError("Aircraft IDs must be unique")
    return aircraft
