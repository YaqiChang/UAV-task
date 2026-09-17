from __future__ import annotations

from math import ceil
from typing import Dict, List, Sequence, Tuple

from ortools.sat.python import cp_model

from .domain import MissionPackage, UAV


def _resource_usage(
    uav: UAV,
    package: MissionPackage,
    energy_per_meter: float,
) -> Tuple[int, int, int]:
    distance = uav.distance_to(package.centroid)
    travel_time_s = ceil(distance / uav.max_speed_mps)
    required_energy = round(distance * energy_per_meter) + package.duration_s
    required_work_s = travel_time_s + package.duration_s
    return required_energy, required_work_s, travel_time_s


def _assignment_cost(
    uav: UAV,
    package: MissionPackage,
    energy_per_meter: float,
) -> int:
    required_energy, required_work_s, _ = _resource_usage(
        uav, package, energy_per_meter
    )
    low_battery_penalty = max(0.0, 30.0 - uav.battery_percent) * 10.0
    priority_reward = package.priority * 20.0
    return max(
        0,
        round(
            10.0 * required_work_s
            + 5.0 * required_energy
            + low_battery_penalty
            - priority_reward
        ),
    )


def _feasible(
    uav: UAV,
    package: MissionPackage,
    energy_per_meter: float,
) -> bool:
    if not uav.available:
        return False
    if not package.capabilities.issubset(uav.capabilities):
        return False
    required_energy, required_work_s, _ = _resource_usage(
        uav, package, energy_per_meter
    )
    if required_energy > uav.usable_energy:
        return False
    if required_work_s > uav.max_work_s:
        return False
    return True


def _ordered_task_ids(package: MissionPackage) -> List[str]:
    remaining = {task.id: task for task in package.tasks}
    completed = set()
    result: List[str] = []
    while remaining:
        ready = [
            task
            for task in remaining.values()
            if not (task.predecessors & package.task_ids) - completed
        ]
        if not ready:
            return sorted(package.task_ids)
        task = min(ready, key=lambda item: (item.earliest_start_s, item.id))
        result.append(task.id)
        completed.add(task.id)
        del remaining[task.id]
    return result


def allocate_packages(
    packages: Sequence[MissionPackage],
    uavs: Sequence[UAV],
    energy_per_meter: float = 0.05,
    max_solver_time_s: float = 10.0,
) -> Tuple[List[Dict], Dict]:
    """
    Assign every package to exactly one UAV.

    CP-SAT jointly enforces capability eligibility, battery-adjusted cumulative
    energy, cumulative work time, package time windows and package precedence.
    """
    model = cp_model.CpModel()
    variables: Dict[Tuple[int, int], cp_model.IntVar] = {}
    starts: Dict[int, cp_model.IntVar] = {}
    ends: Dict[int, cp_model.IntVar] = {}

    for p, package in enumerate(packages):
        earliest = package.earliest_start_s
        latest_start = package.latest_finish_s - package.duration_s
        if latest_start < earliest:
            raise RuntimeError(f"Package {package.id} does not fit its time window")
        starts[p] = model.new_int_var(earliest, latest_start, f"start_{p}")
        ends[p] = model.new_int_var(
            earliest + package.duration_s,
            package.latest_finish_s,
            f"end_{p}",
        )
        model.add(ends[p] == starts[p] + package.duration_s)

    for i, uav in enumerate(uavs):
        intervals = []
        for p, package in enumerate(packages):
            variable = model.new_bool_var(f"x_{i}_{p}")
            variables[i, p] = variable
            intervals.append(
                model.new_optional_interval_var(
                    starts[p],
                    package.duration_s,
                    ends[p],
                    variable,
                    f"interval_{i}_{p}",
                )
            )
            if not _feasible(uav, package, energy_per_meter):
                model.add(variable == 0)
        model.add_no_overlap(intervals)

    for p, _package in enumerate(packages):
        model.add(sum(variables[i, p] for i in range(len(uavs))) == 1)

    for i, uav in enumerate(uavs):
        model.add(
            sum(
                _resource_usage(uav, packages[p], energy_per_meter)[1]
                * variables[i, p]
                for p in range(len(packages))
            )
            <= uav.max_work_s
        )
        model.add(
            sum(
                _resource_usage(uav, packages[p], energy_per_meter)[0]
                * variables[i, p]
                for p in range(len(packages))
            )
            <= uav.usable_energy
        )

    package_by_task = {
        task.id: p
        for p, package in enumerate(packages)
        for task in package.tasks
    }
    for p, package in enumerate(packages):
        for task in package.tasks:
            for predecessor in task.predecessors:
                predecessor_package = package_by_task.get(predecessor)
                if predecessor_package is not None and predecessor_package != p:
                    model.add(starts[p] >= ends[predecessor_package])

    costs = []
    for i, uav in enumerate(uavs):
        for p, package in enumerate(packages):
            costs.append(
                _assignment_cost(uav, package, energy_per_meter) * variables[i, p]
            )
    model.minimize(sum(costs))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_solver_time_s
    status = solver.solve(model)

    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise RuntimeError(
            "No feasible allocation. Check UAV capabilities, battery and work limits."
        )

    assignments: List[Dict] = []
    for i, uav in enumerate(uavs):
        assigned_packages = []
        for p, package in enumerate(packages):
            if solver.value(variables[i, p]) == 1:
                required_energy, required_work_s, travel_time_s = _resource_usage(
                    uav, package, energy_per_meter
                )
                assigned_packages.append(
                    {
                        "package_id": package.id,
                        "task_ids": _ordered_task_ids(package),
                        "actions": sorted(package.actions),
                        "required_capabilities": sorted(package.capabilities),
                        "start_time_s": solver.value(starts[p]),
                        "end_time_s": solver.value(ends[p]),
                        "travel_time_s": travel_time_s,
                        "required_energy": required_energy,
                        "required_work_s": required_work_s,
                        "estimated_cost": _assignment_cost(
                            uav, package, energy_per_meter
                        ),
                    }
                )
        assigned_packages.sort(
            key=lambda item: (item["start_time_s"], item["package_id"])
        )
        assignments.append(
            {
                "uav_id": uav.id,
                "packages": assigned_packages,
                "task_sequence": [
                    task_id
                    for package in assigned_packages
                    for task_id in package["task_ids"]
                ],
                "total_service_time_s": sum(
                    package["end_time_s"] - package["start_time_s"]
                    for package in assigned_packages
                ),
                "total_work_time_s": sum(
                    package["required_work_s"] for package in assigned_packages
                ),
                "total_energy": sum(
                    package["required_energy"] for package in assigned_packages
                ),
                "usable_energy": uav.usable_energy,
            }
        )

    metadata = {
        "solver_status": solver.status_name(status),
        "objective_value": solver.objective_value,
        "wall_time_s": solver.wall_time,
    }
    return assignments, metadata
