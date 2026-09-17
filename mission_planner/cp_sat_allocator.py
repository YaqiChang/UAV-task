"""Resource allocation for normalized front-end task groups.

OR-Tools CP-SAT is used when installed.  A deterministic greedy solver is
kept as a development fallback so schema and integration tests can run in a
minimal environment.  The fallback is reported explicitly in the result.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .capability_model import payload_ids_for_requirements
from .schemas import AircraftResource, NormalizedGroup


def _group_duration(group: NormalizedGroup) -> int:
    return max(1, group.total_workload_sec)


def _window_bounds(group: NormalizedGroup) -> Tuple[int, int]:
    start = group.time_window.start_s or 0
    end = group.time_window.end_s
    if end is None:
        end = start + 86400
    latest_start = end - _group_duration(group)
    if latest_start < start:
        raise ValueError(f"Group {group.group_id} does not fit its time window")
    return start, latest_start


def _candidate_cost(group: NormalizedGroup, aircraft: AircraftResource) -> int:
    duration = _group_duration(group)
    priority_term = max(0, 10 - group.priority) * 100
    model_term = 0
    if "FAST_TRANSIT" in group.required_capabilities:
        model_term = 0 if aircraft.model == "SF50" else 5000
    elif "LOITER" in group.required_capabilities:
        model_term = 0 if aircraft.model == "C172" else 5000
    match = re.search(r"(\d+)$", aircraft.aircraft_id)
    stable_tie_break = int(match.group(1)) if match else sum(
        (index + 1) * ord(char)
        for index, char in enumerate(aircraft.aircraft_id)
    ) % 1000
    return (duration * 10 + priority_term + model_term) * 1000 + stable_tie_break


def _ordered_groups(groups: Sequence[NormalizedGroup]) -> List[NormalizedGroup]:
    return sorted(groups, key=lambda item: (-item.priority, item.group_id))


def _build_assignment(
    group: NormalizedGroup,
    aircraft: AircraftResource,
    start_s: int,
) -> Dict[str, Any]:
    duration = _group_duration(group)
    return {
        "group_id": group.group_id,
        "task_ids": list(group.member_task_ids),
        "aircraft_id": aircraft.aircraft_id,
        "aircraft_model": aircraft.model,
        "payload_tokens": list(group.required_payloads),
        "payload_ids": sorted(
            payload_ids_for_requirements(
                group.required_payloads, aircraft.installed_payloads
            )
        ),
        "required_capabilities": list(group.required_capabilities),
        "target_area_ids": list(group.target_area_ids),
        "start_time_sec": start_s,
        "end_time_sec": start_s + duration,
        "service_duration_sec": group.duration_sec,
        "estimated_workload_sec": group.estimated_workload_sec,
        "estimated_cost": _candidate_cost(group, aircraft),
        "role": "PRIMARY",
    }


def _format_result(
    groups: Sequence[NormalizedGroup],
    aircraft: Sequence[AircraftResource],
    assignment_rows: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    by_aircraft: Dict[str, List[Dict[str, Any]]] = {
        item.aircraft_id: [] for item in aircraft
    }
    for row in assignment_rows:
        by_aircraft[str(row["aircraft_id"])].append(dict(row))
    result: List[Dict[str, Any]] = []
    for item in aircraft:
        rows = sorted(by_aircraft[item.aircraft_id], key=lambda row: (row["start_time_sec"], row["group_id"]))
        result.append(
            {
                "aircraft_id": item.aircraft_id,
                "aircraft_model": item.model,
                "role": "PRIMARY" if rows else "AVAILABLE_RESERVE",
                "groups": rows,
                "task_sequence": [task_id for row in rows for task_id in row["task_ids"]],
                "total_workload_sec": sum(int(row["estimated_workload_sec"]) for row in rows),
                "remaining_endurance_sec": item.remaining_endurance_sec,
                "installed_payloads": sorted(item.installed_payloads),
                "performance": {
                    "service_ceiling_m": item.service_ceiling_m,
                    "cruise_speed_mps": item.cruise_speed_mps,
                    "fuel_burn_reference": item.fuel_burn_reference,
                },
            }
        )
    assigned_ids = {str(row["group_id"]) for row in assignment_rows}
    metadata = {
        **dict(metadata),
        "assigned_group_ids": sorted(assigned_ids),
        "unassigned_group_ids": sorted(group.group_id for group in groups if group.group_id not in assigned_ids),
    }
    return result, metadata


def _greedy_allocate(
    groups: Sequence[NormalizedGroup],
    aircraft: Sequence[AircraftResource],
    candidates: Mapping[str, Sequence[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    occupied: Dict[str, List[Tuple[int, int]]] = {item.aircraft_id: [] for item in aircraft}
    used_work: Dict[str, int] = {item.aircraft_id: 0 for item in aircraft}
    by_id = {item.aircraft_id: item for item in aircraft}
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for group in _ordered_groups(groups):
        candidate_ids = list(candidates.get(group.group_id, []))
        if not candidate_ids:
            failures.append({"group_id": group.group_id, "reason": "NO_CANDIDATE_AIRCRAFT"})
            continue
        start_bound, latest_start = _window_bounds(group)
        duration = _group_duration(group)
        selected: Optional[Tuple[int, str, int]] = None
        for aircraft_id in candidate_ids:
            item = by_id[aircraft_id]
            if used_work[aircraft_id] + duration > item.remaining_endurance_sec:
                continue
            start = start_bound
            for old_start, old_end in sorted(occupied[aircraft_id]):
                if start + duration <= old_start:
                    break
                if start >= old_end:
                    continue
                start = old_end
            if start > latest_start:
                continue
            key = (_candidate_cost(group, item), aircraft_id, start)
            if selected is None or key < selected:
                selected = key
        if selected is None:
            failures.append({"group_id": group.group_id, "reason": "CAPACITY_OR_TIME_CONFLICT"})
            continue
        _, aircraft_id, start = selected
        used_work[aircraft_id] += duration
        occupied[aircraft_id].append((start, start + duration))
        rows.append(_build_assignment(group, by_id[aircraft_id], start))
    return rows, {
        "solver_name": "deterministic_greedy_fallback",
        "solver_status": "FEASIBLE" if not failures else "INFEASIBLE",
        "failures": failures,
    }


def _cp_sat_allocate(
    groups: Sequence[NormalizedGroup],
    aircraft: Sequence[AircraftResource],
    candidates: Mapping[str, Sequence[str]],
    max_solver_time_s: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    aircraft_by_id = {item.aircraft_id: item for item in aircraft}
    group_by_id = {item.group_id: item for item in groups}
    starts: Dict[str, Any] = {}
    ends: Dict[str, Any] = {}
    durations: Dict[str, int] = {}
    variables: Dict[Tuple[str, str], Any] = {}
    for group in groups:
        start_bound, latest_start = _window_bounds(group)
        duration = _group_duration(group)
        durations[group.group_id] = duration
        starts[group.group_id] = model.new_int_var(start_bound, latest_start, f"start_{group.group_id}")
        ends[group.group_id] = model.new_int_var(start_bound + duration, latest_start + duration, f"end_{group.group_id}")
        model.add(ends[group.group_id] == starts[group.group_id] + duration)

    for group in groups:
        for aircraft_id in candidates.get(group.group_id, []):
            variables[group.group_id, aircraft_id] = model.new_bool_var(
                f"assign_{group.group_id}_{aircraft_id}"
            )
        eligible = [variables[group.group_id, item] for item in candidates.get(group.group_id, [])]
        if not eligible:
            raise RuntimeError(f"No eligible aircraft for group {group.group_id}")
        model.add(sum(eligible) == 1)

    for item in aircraft:
        intervals = []
        for group in groups:
            variable = variables.get((group.group_id, item.aircraft_id))
            if variable is None:
                continue
            intervals.append(
                model.new_optional_interval_var(
                    starts[group.group_id],
                    durations[group.group_id],
                    ends[group.group_id],
                    variable,
                    f"interval_{group.group_id}_{item.aircraft_id}",
                )
            )
        if intervals:
            model.add_no_overlap(intervals)
        assigned_groups = [
            group
            for group in groups
            if (group.group_id, item.aircraft_id) in variables
        ]
        model.add(
            sum(
                durations[group.group_id] * variables[group.group_id, item.aircraft_id]
                for group in assigned_groups
            )
            <= item.remaining_endurance_sec
        )

    for group in groups:
        for dependency in group.depends_on_groups:
            if dependency in group_by_id:
                model.add(starts[group.group_id] >= ends[dependency])

    model.minimize(
        sum(
            _candidate_cost(group_by_id[group_id], aircraft_by_id[aircraft_id]) * variable
            for (group_id, aircraft_id), variable in variables.items()
        )
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_solver_time_s
    status = solver.solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        return [], {
            "solver_name": "ortools_cp_sat",
            "solver_status": solver.status_name(status),
            "failures": [{"reason": "CP_SAT_NO_FEASIBLE_SOLUTION"}],
        }
    rows: List[Dict[str, Any]] = []
    for group in groups:
        for aircraft_id in candidates.get(group.group_id, []):
            variable = variables[group.group_id, aircraft_id]
            if solver.value(variable):
                rows.append(
                    _build_assignment(
                        group,
                        aircraft_by_id[aircraft_id],
                        solver.value(starts[group.group_id]),
                    )
                )
    return rows, {
        "solver_name": "ortools_cp_sat",
        "solver_status": solver.status_name(status),
        "objective_value": solver.objective_value,
        "wall_time_sec": solver.wall_time,
        "failures": [],
    }


def allocate_groups(
    groups: Sequence[NormalizedGroup],
    aircraft: Sequence[AircraftResource],
    candidates: Mapping[str, Sequence[str]],
    max_solver_time_s: float = 10.0,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Allocate every normalized group exactly once."""
    try:
        import ortools  # noqa: F401
    except ImportError:
        rows, metadata = _greedy_allocate(groups, aircraft, candidates)
    else:
        rows, metadata = _cp_sat_allocate(groups, aircraft, candidates, max_solver_time_s)
    return _format_result(groups, aircraft, rows, metadata)
