"""Front-end planner to aircraft-resource allocation service core."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional

from .candidate_filter import filter_candidates
from .cp_sat_allocator import allocate_groups
from .dependency_validator import validate_dependencies
from .normalize import normalize_groups


def allocate_mission(
    planner_output: Mapping[str, Any],
    aggregation_output: Mapping[str, Any],
    fleet_snapshot: Mapping[str, Any],
    completed_task_ids: Iterable[str] = (),
    max_solver_time_s: float = 10.0,
) -> Dict[str, Any]:
    """Run validation, candidate filtering, allocation and result validation."""
    tasks, groups, metadata = normalize_groups(planner_output, aggregation_output)
    dependency = validate_dependencies(tasks, groups, completed_task_ids)
    base = {
        "mission_id": metadata.get("mission_id"),
        "allocation_version": 1,
        "plan_version": metadata.get("plan_version"),
        "aggregation_version": metadata.get("aggregation_version"),
        "context_version": metadata.get("context_version"),
        "fleet_snapshot_version": fleet_snapshot.get("fleet_snapshot_version"),
        "groups": [group.to_dict() for group in groups],
        "dependency_validation": dependency,
        "warnings": metadata.get("warnings", []),
        "conflicts": metadata.get("conflicts", []),
    }
    aggregation_status = metadata.get("aggregation_status")
    if aggregation_status not in {None, "READY_FOR_ALLOCATION"}:
        return {
            **base,
            "status": "BLOCKED",
            "reason": "AGGREGATION_NOT_READY",
            "assignments": [],
            "reserve_aircraft": [],
            "candidate_summary": {},
        }
    if metadata.get("conflicts"):
        return {
            **base,
            "status": "BLOCKED",
            "reason": "AGGREGATION_CONFLICTS_PRESENT",
            "assignments": [],
            "reserve_aircraft": [],
            "candidate_summary": {},
        }
    if not dependency["valid"]:
        return {
            **base,
            "status": "BLOCKED",
            "reason": "UNRESOLVED_DEPENDENCY",
            "assignments": [],
            "reserve_aircraft": [],
            "candidate_summary": {},
        }
    raw_aircraft = fleet_snapshot.get("aircraft", fleet_snapshot.get("uavs", []))
    expected_aircraft = (planner_output.get("fleet_capability_summary") or {}).get(
        "available_aircraft"
    )
    fleet_warnings = []
    if expected_aircraft is not None and int(expected_aircraft) != len(raw_aircraft):
        fleet_warnings.append(
            {
                "code": "FLEET_COUNT_MISMATCH",
                "expected": int(expected_aircraft),
                "received": len(raw_aircraft),
            }
        )
    base["warnings"] = [*base["warnings"], *fleet_warnings]
    candidates, rejected, aircraft = filter_candidates(groups, raw_aircraft)
    base["candidate_summary"] = {
        "candidates": candidates,
        "rejected": rejected,
    }
    empty_groups = [group_id for group_id, values in candidates.items() if not values]
    if empty_groups:
        return {
            **base,
            "status": "INFEASIBLE",
            "reason": "NO_FEASIBLE_AIRCRAFT",
            "infeasible_groups": empty_groups,
            "assignments": [],
            "reserve_aircraft": [],
        }
    assignments, solver_metadata = allocate_groups(
        groups,
        aircraft,
        candidates,
        max_solver_time_s=max_solver_time_s,
    )
    if solver_metadata.get("unassigned_group_ids"):
        status = "INFEASIBLE"
        reason = "ALLOCATION_FAILED"
    else:
        status = "ALLOCATED"
        reason = None
    reserve = [
        {
            "aircraft_id": item["aircraft_id"],
            "aircraft_model": item["aircraft_model"],
            "role": "AVAILABLE_RESERVE",
        }
        for item in assignments
        if item["role"] == "AVAILABLE_RESERVE"
    ]
    result = {
        **base,
        "status": status,
        "reason": reason,
        "assignments": assignments,
        "reserve_aircraft": reserve,
        "solver": solver_metadata,
    }
    result["validation"] = {
        "all_groups_assigned_once": not bool(solver_metadata.get("unassigned_group_ids")),
        "dependency_constraints_satisfied": dependency["valid"],
        "candidate_constraints_applied": True,
    }
    return result
