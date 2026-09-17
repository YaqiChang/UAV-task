from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from math import hypot
from itertools import permutations
from typing import Dict, List, Sequence, Set, Tuple

from .domain import AtomicTask, MissionPackage, UAV


COMPATIBLE_ACTION_PAIRS: Set[frozenset[str]] = {
    frozenset({"search", "detect"}),
    frozenset({"search", "track"}),
    frozenset({"detect", "track"}),
    frozenset({"track", "relay"}),
    frozenset({"search", "relay"}),
    frozenset({"inspect", "relay"}),
    frozenset({"photo", "relay"}),
    frozenset({"transit", "search"}),
    frozenset({"transit", "track"}),
}


def _deduplicate(
    tasks: Sequence[AtomicTask],
    uavs: Sequence[UAV],
    max_distance_m: float,
    energy_per_meter: float,
) -> Tuple[List[AtomicTask], List[Dict]]:
    groups: Dict[Tuple, List[AtomicTask]] = defaultdict(list)
    passthrough: List[AtomicTask] = []

    for task in tasks:
        if task.merge_policy != "deduplicate":
            passthrough.append(task)
            continue
        key = (
            task.action,
            task.region_id,
            task.target_id,
            tuple(sorted(task.predecessors)),
            task.earliest_start_s,
            task.latest_finish_s,
        )
        groups[key].append(task)

    deduplicated: List[AtomicTask] = []
    trace: List[Dict] = []
    replacement_ids: Dict[str, str] = {}
    for raw_group in groups.values():
        pending = sorted(raw_group, key=lambda task: task.id)
        while pending:
            cluster = [pending.pop(0)]
            index = 0
            while index < len(pending):
                candidate_group = cluster + [pending[index]]
                xs = [task.position[0] for task in candidate_group]
                ys = [task.position[1] for task in candidate_group]
                candidate = replace(
                    cluster[0],
                    position=(sum(xs) / len(xs), sum(ys) / len(ys)),
                    duration_s=max(task.duration_s for task in candidate_group),
                    required_capabilities=set().union(
                        *(task.required_capabilities for task in candidate_group)
                    ),
                    priority=max(task.priority for task in candidate_group),
                )
                spatially_valid = all(
                    hypot(a.position[0] - b.position[0], a.position[1] - b.position[1])
                    <= max_distance_m
                    for a in candidate_group
                    for b in candidate_group
                )
                if spatially_valid and _single_uav_feasible(
                    MissionPackage(id="dedup-candidate", tasks=[candidate]),
                    uavs,
                    energy_per_meter,
                ):
                    cluster.append(pending.pop(index))
                else:
                    index += 1

            representative = cluster[0]
            xs = [task.position[0] for task in cluster]
            ys = [task.position[1] for task in cluster]
            merged = replace(
                representative,
                position=(sum(xs) / len(xs), sum(ys) / len(ys)),
                duration_s=max(task.duration_s for task in cluster),
                required_capabilities=set().union(
                    *(task.required_capabilities for task in cluster)
                ),
                priority=max(task.priority for task in cluster),
            )
            deduplicated.append(merged)
            for task in cluster:
                replacement_ids[task.id] = representative.id
            if len(cluster) > 1:
                source_ids = sorted(task.id for task in cluster)
                eliminated_ids = [item for item in source_ids if item != representative.id]
                trace.append(
                    {
                        "operation": "deduplicate",
                        "source_task_ids": source_ids,
                        "eliminated_task_ids": eliminated_ids,
                        "result_task_id": representative.id,
                        "constraints_rechecked": [
                            "spatial_distance",
                            "time_window",
                            "single_uav_capability",
                            "energy_and_reserve",
                            "package_size",
                        ],
                    }
                )

    result = passthrough + deduplicated
    remapped: List[AtomicTask] = []
    for task in result:
        predecessors = {
            replacement_ids.get(predecessor, predecessor)
            for predecessor in task.predecessors
        } - {task.id}
        remapped.append(replace(task, predecessors=predecessors))

    return remapped, trace


def _time_overlap_ratio(a: MissionPackage, b: MissionPackage) -> float:
    start = max(a.earliest_start_s, b.earliest_start_s)
    finish = min(a.latest_finish_s, b.latest_finish_s)
    if finish <= start:
        return 0.0
    union_start = min(a.earliest_start_s, b.earliest_start_s)
    union_finish = max(a.latest_finish_s, b.latest_finish_s)
    return (finish - start) / max(1, union_finish - union_start)


def _distance(a: MissionPackage, b: MissionPackage) -> float:
    return hypot(a.centroid[0] - b.centroid[0], a.centroid[1] - b.centroid[1])


def _action_score(a: MissionPackage, b: MissionPackage) -> float:
    if a.actions == b.actions:
        return 1.0
    for action_a in a.actions:
        for action_b in b.actions:
            if frozenset({action_a, action_b}) in COMPATIBLE_ACTION_PAIRS:
                return 1.0
    return 0.0


def _has_dependency_between(a: MissionPackage, b: MissionPackage) -> bool:
    a_ids = a.task_ids
    b_ids = b.task_ids
    return any(task.predecessors & b_ids for task in a.tasks) or any(
        task.predecessors & a_ids for task in b.tasks
    )


def _single_uav_feasible(
    package: MissionPackage,
    uavs: Sequence[UAV],
    energy_per_meter: float,
) -> bool:
    for uav in uavs:
        if not uav.available:
            continue
        if not package.capabilities.issubset(uav.capabilities):
            continue
        travel_energy = round(uav.distance_to(package.centroid) * energy_per_meter)
        service_energy = package.duration_s
        total_energy = travel_energy + service_energy
        if total_energy > uav.usable_energy:
            continue
        travel_time_s = round(uav.distance_to(package.centroid) / uav.max_speed_mps)
        if package.duration_s + travel_time_s > uav.max_work_s:
            continue
        return True
    return False


def _time_feasible(package: MissionPackage) -> bool:
    """Find a serial order satisfying task windows and in-package dependencies."""
    task_ids = package.task_ids
    for order in permutations(package.tasks):
        completed: Set[str] = set()
        current_s = 0
        valid = True
        for task in order:
            internal_predecessors = task.predecessors & task_ids
            if not internal_predecessors.issubset(completed):
                valid = False
                break
            start_s = max(current_s, task.earliest_start_s)
            current_s = start_s + task.duration_s
            if current_s > task.latest_finish_s:
                valid = False
                break
            completed.add(task.id)
        if valid:
            return True
    return False


def _max_task_distance(package: MissionPackage) -> float:
    return max(
        (
            hypot(
                a.position[0] - b.position[0],
                a.position[1] - b.position[1],
            )
            for a in package.tasks
            for b in package.tasks
        ),
        default=0.0,
    )


def _hard_compatible(
    a: MissionPackage,
    b: MissionPackage,
    uavs: Sequence[UAV],
    max_bundle_distance_m: float,
    max_package_tasks: int,
    energy_per_meter: float,
) -> bool:
    if len(a.tasks) + len(b.tasks) > max_package_tasks:
        return False
    if any(task.merge_policy == "never" for task in a.tasks + b.tasks):
        return False
    merged = MissionPackage(id="candidate", tasks=a.tasks + b.tasks)
    if _max_task_distance(merged) > max_bundle_distance_m:
        return False
    if not _time_feasible(merged):
        return False
    if not _single_uav_feasible(merged, uavs, energy_per_meter):
        return False
    return True


def _merge_score(
    a: MissionPackage,
    b: MissionPackage,
    max_bundle_distance_m: float,
) -> float:
    same_region = 1.0 if a.region_ids & b.region_ids else 0.0
    same_target = 1.0 if a.target_ids and a.target_ids & b.target_ids else 0.0
    dependency = 1.0 if _has_dependency_between(a, b) else 0.0
    temporal = _time_overlap_ratio(a, b)
    spatial = max(0.0, 1.0 - _distance(a, b) / max(1.0, max_bundle_distance_m))
    action = _action_score(a, b)

    return (
        0.25 * same_region
        + 0.15 * same_target
        + 0.20 * dependency
        + 0.15 * temporal
        + 0.15 * spatial
        + 0.10 * action
    )


def aggregate_tasks(
    tasks: Sequence[AtomicTask],
    uavs: Sequence[UAV],
    threshold: float = 0.55,
    max_bundle_distance_m: float = 300.0,
    max_package_tasks: int = 4,
    energy_per_meter: float = 0.05,
) -> Tuple[List[MissionPackage], List[Dict]]:
    """
    Greedy constrained agglomeration.

    Every merge must pass hard feasibility checks. This avoids the transitive
    chaining problem of applying DBSCAN directly to a similarity matrix.
    """
    deduplicated, trace = _deduplicate(
        tasks,
        uavs,
        max_bundle_distance_m,
        energy_per_meter,
    )
    packages = [
        MissionPackage(id=f"P{index + 1}", tasks=[task])
        for index, task in enumerate(sorted(deduplicated, key=lambda item: item.id))
    ]

    next_package_index = len(packages) + 1
    while True:
        best = None
        for i in range(len(packages)):
            for j in range(i + 1, len(packages)):
                a, b = packages[i], packages[j]
                if not _hard_compatible(
                    a,
                    b,
                    uavs,
                    max_bundle_distance_m,
                    max_package_tasks,
                    energy_per_meter,
                ):
                    continue
                score = _merge_score(a, b, max_bundle_distance_m)
                if score < threshold:
                    continue
                candidate = (score, i, j)
                if best is None or candidate > best:
                    best = candidate

        if best is None:
            break

        score, i, j = best
        a, b = packages[i], packages[j]
        merged = MissionPackage(
            id=f"P{next_package_index}",
            tasks=sorted(a.tasks + b.tasks, key=lambda task: task.id),
        )
        next_package_index += 1
        trace.append(
            {
                "operation": "bundle",
                "source_package_ids": [a.id, b.id],
                "source_task_ids": sorted(a.task_ids | b.task_ids),
                "result_package_id": merged.id,
                "score": round(score, 4),
                "constraints_rechecked": [
                    "spatial_distance",
                    "time_window",
                    "single_uav_capability",
                    "energy_and_reserve",
                    "package_size",
                ],
            }
        )
        packages = [
            package
            for index, package in enumerate(packages)
            if index not in {i, j}
        ]
        packages.append(merged)

    packages = sorted(packages, key=lambda package: min(package.task_ids))
    for index, package in enumerate(packages, start=1):
        package.id = f"P{index}"
    final_package_by_tasks = {
        frozenset(package.task_ids): package.id for package in packages
    }
    for item in trace:
        if item.get("operation") == "bundle":
            final_id = final_package_by_tasks.get(
                frozenset(item.get("source_task_ids", []))
            )
            if final_id is not None:
                item["result_package_id"] = final_id
    return packages, trace
