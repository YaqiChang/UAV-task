"""Aircraft route scheduling for the version 1 collaboration contract."""

from __future__ import annotations

from math import ceil, hypot
from time import perf_counter
from typing import Any


def _travel(first: tuple[float, float], second: tuple[float, float], speed: float) -> int:
    return ceil(hypot(first[0] - second[0], first[1] - second[1]) / speed)


def _dependencies(groups: list[dict], completed: set[str]) -> dict[str, set[str]]:
    by_task = {task_id: group["id"] for group in groups for task_id in group["ids"]}
    return {
        group["id"]: {
            by_task[dependency]
            for task in group["tasks"]
            for dependency in task["depends"]
            if dependency not in completed and dependency in by_task and by_task[dependency] != group["id"]
        }
        for group in groups
    }


def _rows(routes: dict[str, list[str]], starts: dict[str, int], groups: list[dict],
          aircraft: list[dict], resources: dict[tuple[str, str], dict]) -> list[dict]:
    group_by_id = {group["id"]: group for group in groups}
    aircraft_by_id = {item["id"]: item for item in aircraft}
    output = []
    for aircraft_id, group_ids in sorted(routes.items()):
        plane = aircraft_by_id[aircraft_id]
        previous = plane["enu"]
        for group_id in group_ids:
            group = group_by_id[group_id]
            resource = resources[group_id, aircraft_id]
            transit = _travel(previous, group["first"], plane["speed"]) + resource["internal"]
            output.append({"group_id": group_id, "aircraft_id": aircraft_id, "start": starts[group_id],
                           "transit": transit, "service": group["service"], "task_ids": group["ids"],
                           "payloads": sorted(resource["selected_payloads"])})
            previous = group["last"]
    return output


def _greedy(groups: list[dict], aircraft: list[dict], candidates: dict[str, list[str]],
            resources: dict[tuple[str, str], dict], config: dict) -> tuple[list[dict], dict]:
    by_id = {plane["id"]: plane for plane in aircraft}
    groups_by_id = {group["id"]: group for group in groups}
    dependencies = _dependencies(groups, config["completed"])
    routes: dict[str, list[str]] = {plane["id"]: [] for plane in aircraft}
    starts: dict[str, int] = {}
    finishes: dict[str, int] = {}
    work: dict[str, int] = {plane["id"]: 0 for plane in aircraft}
    remaining = {group["id"] for group in groups}
    while remaining:
        ready = [groups_by_id[group_id] for group_id in remaining if dependencies[group_id] <= starts.keys()]
        if not ready:
            break
        group = min(ready, key=lambda item: (-item["priority"], item["id"]))
        possible = []
        for aircraft_id in candidates[group["id"]]:
            plane = by_id[aircraft_id]
            route = routes[aircraft_id]
            last = groups_by_id[route[-1]]["last"] if route else plane["enu"]
            travel = _travel(last, group["first"], plane["speed"])
            duration = resources[group["id"], aircraft_id]["duration"]
            start = max(group["start"],
                        finishes[route[-1]] + travel if route else travel,
                        *(finishes[predecessor] for predecessor in dependencies[group["id"]]))
            incremental = travel + duration
            if group["end"] is not None and start + duration > group["end"]:
                continue
            if work[aircraft_id] + incremental > plane["max_work"]:
                continue
            if work[aircraft_id] + incremental > int(plane["endurance"] * (1 - config["reserve"])):
                continue
            possible.append((start + duration, travel, aircraft_id, start, incremental))
        if not possible:
            break
        _, _, aircraft_id, start, incremental = min(possible)
        routes[aircraft_id].append(group["id"])
        starts[group["id"]] = start
        finishes[group["id"]] = start + resources[group["id"], aircraft_id]["duration"]
        work[aircraft_id] += incremental
        remaining.remove(group["id"])
    rows = _rows(routes, starts, groups, aircraft, resources) if not remaining else []
    return rows, {"solver_name": "deterministic_greedy_fallback",
                  "solver_status": "FEASIBLE" if not remaining else "INFEASIBLE",
                  "optimal": False, "unassigned_group_ids": sorted(remaining) if remaining else [],
                  "wall_time_sec": 0.0}


def _cp_sat(groups: list[dict], aircraft: list[dict], candidates: dict[str, list[str]],
            resources: dict[tuple[str, str], dict], config: dict) -> tuple[list[dict], dict]:
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    group_by_id = {group["id"]: group for group in groups}
    dependencies = _dependencies(groups, config["completed"])
    finite_ends = [group["end"] for group in groups if group["end"] is not None]
    horizon = max([86400, *finite_ends, *(group["start"] + group["service"] for group in groups)]) + 86400
    starts = {group["id"]: model.new_int_var(group["start"], horizon, "start_" + group["id"])
              for group in groups}
    selected = {}
    for group in groups:
        group_id = group["id"]
        options = []
        for aircraft_id in candidates[group_id]:
            variable = model.new_bool_var("assign_" + group_id + "_" + aircraft_id)
            selected[group_id, aircraft_id] = variable
            options.append(variable)
            if group["end"] is not None:
                model.add(starts[group_id] + resources[group_id, aircraft_id]["duration"] <= group["end"]).only_enforce_if(variable)
        model.add(sum(options) == 1)

    arcs_by_aircraft: dict[str, list[tuple[str | None, str | None, Any, int]]] = {}
    for plane in aircraft:
        aircraft_id = plane["id"]
        available = [group for group in groups if (group["id"], aircraft_id) in selected]
        if not available:
            continue
        # AddCircuit chooses a path from the depot through each selected group.
        # The final arc to the depot has zero cost because return flight is outside this contract.
        empty = model.new_bool_var("idle_" + aircraft_id)
        model.add(sum(selected[group["id"], aircraft_id] for group in available) == 0).only_enforce_if(empty)
        model.add(sum(selected[group["id"], aircraft_id] for group in available) >= 1).only_enforce_if(empty.Not())
        circuit = [(0, 0, empty)]
        arcs: list[tuple[str | None, str | None, Any, int]] = []
        for index, group in enumerate(available, start=1):
            group_id = group["id"]
            circuit.append((index, index, selected[group_id, aircraft_id].Not()))
            from_depot = model.new_bool_var(f"depot_{aircraft_id}_{group_id}")
            entry = _travel(plane["enu"], group["first"], plane["speed"])
            circuit.append((0, index, from_depot))
            arcs.append((None, group_id, from_depot, entry))
            model.add(starts[group_id] >= entry).only_enforce_if(from_depot)
            to_depot = model.new_bool_var(f"finish_{aircraft_id}_{group_id}")
            circuit.append((index, 0, to_depot))
            arcs.append((group_id, None, to_depot, 0))
            for other_index, other in enumerate(available, start=1):
                if index == other_index:
                    continue
                other_id = other["id"]
                transition = _travel(group["last"], other["first"], plane["speed"])
                edge = model.new_bool_var(f"edge_{aircraft_id}_{group_id}_{other_id}")
                circuit.append((index, other_index, edge))
                arcs.append((group_id, other_id, edge, transition))
                model.add(starts[other_id] >= starts[group_id] + resources[group_id, aircraft_id]["duration"] + transition).only_enforce_if(edge)
        model.add_circuit(circuit)
        effort = sum(resources[group["id"], aircraft_id]["duration"] * selected[group["id"], aircraft_id]
                     for group in available) + sum(travel * arc for _, _, arc, travel in arcs)
        model.add(effort <= plane["max_work"])
        model.add(effort <= int(plane["endurance"] * (1 - config["reserve"])))
        arcs_by_aircraft[aircraft_id] = arcs

    for group in groups:
        for predecessor in dependencies[group["id"]]:
            departure = starts[predecessor] + sum(
                resources[predecessor, aircraft_id]["duration"] * selected[predecessor, aircraft_id]
                for aircraft_id in candidates[predecessor]
            )
            model.add(starts[group["id"]] >= departure)
    model.minimize(
        sum(travel * edge * 100 for arcs in arcs_by_aircraft.values() for _, _, edge, travel in arcs)
        + sum(starts[group["id"]] * min(10, group["priority"]) for group in groups)
        + sum((index + 1) * selected[group["id"], plane["id"]]
              for index, plane in enumerate(aircraft) for group in groups
              if (group["id"], plane["id"]) in selected)
    )
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = config["time_limit"]
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    state = solver.solve(model)
    metadata = {"solver_name": "cp_sat", "solver_status": solver.status_name(state),
                "optimal": state == cp_model.OPTIMAL, "wall_time_sec": solver.wall_time,
                "unassigned_group_ids": [] if state in (cp_model.OPTIMAL, cp_model.FEASIBLE) else [group["id"] for group in groups]}
    if state not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], metadata
    routes = {}
    for plane in aircraft:
        aircraft_id = plane["id"]
        edges = arcs_by_aircraft.get(aircraft_id, [])
        successor = {origin: destination for origin, destination, arc, _ in edges
                     if solver.value(arc) and destination is not None}
        route = []
        current = successor.get(None)
        while current is not None:
            route.append(current)
            current = successor.get(current)
        routes[aircraft_id] = route
    actual_starts = {group["id"]: solver.value(starts[group["id"]]) for group in groups}
    return _rows(routes, actual_starts, groups, aircraft, resources), metadata


def solve_allocation(groups: list[dict], aircraft: list[dict], candidates: dict[str, list[str]],
                     resources: dict[tuple[str, str], dict], config: dict) -> tuple[list[dict], dict]:
    started = perf_counter()
    try:
        import ortools  # noqa: F401
    except ImportError:
        rows, metadata = _greedy(groups, aircraft, candidates, resources, config)
    else:
        rows, metadata = _cp_sat(groups, aircraft, candidates, resources, config)
    if metadata["solver_name"] == "deterministic_greedy_fallback":
        metadata["wall_time_sec"] = round(perf_counter() - started, 6)
    if len(rows) != len(groups):
        metadata["unassigned_group_ids"] = [group["id"] for group in groups]
    return rows, metadata


def validate_allocation(rows: list[dict], groups: list[dict], aircraft: list[dict],
                        candidates: dict[str, list[str]], resources: dict[tuple[str, str], dict],
                        config: dict) -> dict[str, bool]:
    """Recompute all hard constraints independently from the chosen schedule."""
    group_by_id = {group["id"]: group for group in groups}
    aircraft_by_id = {plane["id"]: plane for plane in aircraft}
    chosen = {row["group_id"]: row for row in rows}
    uniqueness = len(chosen) == len(rows) == len(groups) and set(chosen) == set(group_by_id)
    payload = uniqueness
    flight = uniqueness
    endurance = uniqueness
    dependencies_ok = uniqueness
    by_aircraft: dict[str, list[dict]] = {plane["id"]: [] for plane in aircraft}
    for row in rows:
        group = group_by_id.get(row["group_id"])
        plane = aircraft_by_id.get(row["aircraft_id"])
        if group is None or plane is None:
            return {"every_group_assigned_at_most_once": False, "dependencies_satisfied": False,
                    "payload_constraints_satisfied": False, "flight_constraints_satisfied": False,
                    "endurance_constraints_satisfied": False}
        by_aircraft[plane["id"]].append(row)
        payload &= row["aircraft_id"] in candidates[group["id"]] and group["payloads"] <= plane["payloads"]
        payload &= set(row["payloads"]) == resources[group["id"], plane["id"]]["selected_payloads"]
        flight &= group["flight"] <= plane["flight"] and all(task["position"]["altitude_m"] <= plane["ceiling"] for task in group["tasks"])
        duration = resources[group["id"], plane["id"]]["duration"]
        dependencies_ok &= row["start"] >= group["start"]
        dependencies_ok &= group["end"] is None or row["start"] + duration <= group["end"]
    for plane in aircraft:
        route = sorted(by_aircraft[plane["id"]], key=lambda row: (row["start"], row["group_id"]))
        position = plane["enu"]
        last_finish = 0
        work = 0
        for row in route:
            group = group_by_id[row["group_id"]]
            transition = _travel(position, group["first"], plane["speed"])
            resource = resources[group["id"], plane["id"]]
            dependencies_ok &= row["start"] >= last_finish + transition
            endurance &= row["transit"] == transition + resource["internal"]
            endurance &= row["service"] == group["service"]
            work += transition + resource["duration"]
            last_finish = row["start"] + resource["duration"]
            position = group["last"]
        endurance &= work <= plane["max_work"]
        endurance &= work <= int(plane["endurance"] * (1 - config["reserve"]))
    for group_id, preceding in _dependencies(groups, config["completed"]).items():
        if group_id in chosen:
            for predecessor in preceding:
                if predecessor not in chosen:
                    dependencies_ok = False
                    continue
                previous = chosen[predecessor]
                finish = previous["start"] + resources[predecessor, previous["aircraft_id"]]["duration"]
                dependencies_ok &= chosen[group_id]["start"] >= finish
    return {"every_group_assigned_at_most_once": uniqueness,
            "dependencies_satisfied": bool(dependencies_ok),
            "payload_constraints_satisfied": bool(payload),
            "flight_constraints_satisfied": bool(flight),
            "endurance_constraints_satisfied": bool(endurance)}
