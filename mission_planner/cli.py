from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List

from .aggregation import aggregate_tasks
from .allocation import allocate_packages
from .domain import load_tasks, load_uavs
from .multiuav_adapter import fetch_multiuav_drones


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_plan(
    tasks: List[Any],
    uavs: List[Any],
    packages: List[Any],
    assignments: List[Dict[str, Any]],
    trace: List[Dict[str, Any]],
) -> Dict[str, Any]:
    retained_ids = {task.id for package in packages for task in package.tasks}
    non_deduplicated_ids = {
        task.id for task in tasks if task.merge_policy != "deduplicate"
    }
    if not non_deduplicated_ids.issubset(retained_ids):
        missing = sorted(non_deduplicated_ids - retained_ids)
        raise RuntimeError(f"Non-deduplicated tasks were lost: {missing}")

    eliminated_ids = {
        task_id
        for item in trace
        if item.get("operation") == "deduplicate"
        for task_id in item.get("eliminated_task_ids", [])
    }
    original_ids = {task.id for task in tasks}
    unexplained = original_ids - retained_ids - eliminated_ids
    if unexplained:
        raise RuntimeError(f"Tasks missing without deduplication trace: {sorted(unexplained)}")

    assigned_ids = [
        package["package_id"]
        for assignment in assignments
        for package in assignment["packages"]
    ]
    expected_ids = {package.id for package in packages}
    if len(assigned_ids) != len(set(assigned_ids)) or set(assigned_ids) != expected_ids:
        raise RuntimeError("Every package must be assigned exactly once")

    uav_by_id = {uav.id: uav for uav in uavs}
    package_by_id = {package.id: package for package in packages}
    for assignment in assignments:
        uav = uav_by_id[assignment["uav_id"]]
        for assigned_package in assignment["packages"]:
            package = package_by_id[assigned_package["package_id"]]
            if not package.capabilities.issubset(uav.capabilities):
                raise RuntimeError(
                    f"UAV {uav.id} lacks capabilities for {package.id}"
                )

    return {
        "all_non_deduplicated_tasks_retained": True,
        "deduplications_traced": True,
        "every_package_assigned_once": True,
        "capabilities_satisfied": True,
    }


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Constrained task aggregation and UAV allocation"
    )
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--uavs", type=Path)
    parser.add_argument("--server")
    parser.add_argument("--api-key", help="X-API-Key for the optional live adapter")
    parser.add_argument("--capability-profiles", type=Path)
    parser.add_argument("--output", type=Path, default=Path("outputs/plan.json"))
    parser.add_argument("--threshold", type=float, default=0.55)
    parser.add_argument("--max-bundle-distance", type=float, default=300.0)
    args = parser.parse_args()

    tasks = load_tasks(_load_json(args.tasks))

    if args.server:
        if args.capability_profiles is None:
            parser.error("--capability-profiles is required with --server")
        profiles = _load_json(args.capability_profiles)
        uavs = fetch_multiuav_drones(args.server, profiles, api_key=args.api_key)
    else:
        if args.uavs is None:
            parser.error("--uavs is required when --server is not used")
        uavs = load_uavs(_load_json(args.uavs))

    packages, trace = aggregate_tasks(
        tasks,
        uavs,
        threshold=args.threshold,
        max_bundle_distance_m=args.max_bundle_distance,
    )
    assignments, solver_metadata = allocate_packages(packages, uavs)
    validation = _validate_plan(tasks, uavs, packages, assignments, trace)

    result: Dict[str, Any] = {
        "input_task_count": len(tasks),
        "post_dedup_task_count": sum(len(package.tasks) for package in packages),
        "package_count": len(packages),
        "uav_count": len(uavs),
        "uavs": [uav.to_dict() for uav in uavs],
        "packages": [package.to_dict() for package in packages],
        "assignments": assignments,
        "aggregation_trace": trace,
        "solver": solver_metadata,
        "validation": validation,
        "parameters": {
            "aggregation_threshold": args.threshold,
            "max_bundle_distance_m": args.max_bundle_distance,
            "energy_per_meter": 0.05,
            "max_package_tasks": 4,
        },
    }

    _write_json_atomic(args.output, result)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
