"""Dependency checks between planner tasks and aggregated task groups."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from .schemas import NormalizedGroup, NormalizedTask


def validate_dependencies(
    tasks: Mapping[str, NormalizedTask],
    groups: Sequence[NormalizedGroup],
    completed_task_ids: Iterable[str] = (),
) -> Dict[str, Any]:
    completed = {str(item) for item in completed_task_ids}
    group_ids = {group.group_id for group in groups}
    task_to_group = {
        task_id: group.group_id
        for group in groups
        for task_id in group.member_task_ids
    }
    blockers: List[Dict[str, Any]] = []
    for group in groups:
        for dependency in group.external_dependencies:
            if dependency in completed:
                continue
            if dependency in task_to_group:
                continue
            if dependency not in tasks:
                reason = "UNKNOWN_TASK_DEPENDENCY"
            else:
                reason = "UNRESOLVED_TASK_DEPENDENCY"
            blockers.append(
                {
                    "group_id": group.group_id,
                    "dependency": dependency,
                    "reason": reason,
                }
            )
        for dependency_group in group.depends_on_groups:
            if dependency_group not in group_ids:
                blockers.append(
                    {
                        "group_id": group.group_id,
                        "dependency": dependency_group,
                        "reason": "UNKNOWN_GROUP_DEPENDENCY",
                    }
                )
    return {
        "valid": not blockers,
        "completed_task_ids": sorted(completed),
        "blockers": blockers,
    }
