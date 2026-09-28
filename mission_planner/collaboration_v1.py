"""Version 1 in-process task aggregation and aircraft allocation contract.

The public function accepts and returns JSON-compatible dictionaries.  Local
WGS84 tangent-plane coordinates are used only inside this module.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil, cos, hypot, radians, sin, sqrt
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5

from .capability_model import PAYLOAD_CATALOG, PLATFORM_PROFILES
from .collaboration_solver import solve_allocation, validate_allocation


PAYLOAD_IDS = {record["payload_id"]: set(record["capabilities"]) for record in PAYLOAD_CATALOG.values()}
PAYLOAD_ALIASES = {
    "VISIBLE": "payload.stormcaster_e",
    "INFRARED": "payload.wescam_mx15",
    "RADAR": "payload.lynx_mmr",
}
SENSOR_CAPABILITIES = set().union(*PAYLOAD_IDS.values())
TASK_TYPES = {"OBSERVE", "SEARCH", "TRACK", "RELAY", "SURVEY"}
GROUP_TYPES = {"OBSERVE": "AREA_OBSERVATION", "SEARCH": "AREA_SEARCH", "TRACK": "TARGET_TRACKING",
               "RELAY": "COMMUNICATION_RELAY", "SURVEY": "AREA_SURVEY"}
POLICIES = {"NEVER", "BUNDLE_IF_COMPATIBLE", "DEDUPLICATE"}


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return value


def _sequence(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return value


def _number(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not (-float("inf") < result < float("inf")) or (positive and result <= 0):
        raise ValueError(f"{field} has an invalid value")
    return result


def _integer(value: Any, field: str, *, positive: bool = False) -> int:
    result = _number(value, field, positive=positive)
    if int(result) != result:
        raise ValueError(f"{field} must be an integer")
    return int(result)


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value.strip()


def _position(raw: Any, field: str) -> dict[str, Any]:
    position = _mapping(raw, field)
    lat = _number(position.get("latitude"), f"{field}.latitude")
    lon = _number(position.get("longitude"), f"{field}.longitude")
    altitude = _number(position.get("altitude_m"), f"{field}.altitude_m")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"{field} has invalid WGS84 coordinates")
    if position.get("altitude_reference") != "MSL":
        raise ValueError(f"{field}.altitude_reference must be MSL")
    return {"latitude": lat, "longitude": lon, "altitude_m": altitude, "altitude_reference": "MSL"}


def _enu(position: Mapping[str, Any], reference: Mapping[str, Any]) -> tuple[float, float]:
    """ECEF to local ENU for horizontal distance; MSL height is handled separately."""
    a = 6378137.0
    eccentricity_sq = 6.69437999014e-3

    def ecef(lat: float, lon: float) -> tuple[float, float, float]:
        latitude, longitude = radians(lat), radians(lon)
        normal = a / sqrt(1 - eccentricity_sq * sin(latitude) ** 2)
        return (normal * cos(latitude) * cos(longitude), normal * cos(latitude) * sin(longitude), normal * (1 - eccentricity_sq) * sin(latitude))

    x, y, z = ecef(position["latitude"], position["longitude"])
    x0, y0, z0 = ecef(reference["latitude"], reference["longitude"])
    dx, dy, dz = x - x0, y - y0, z - z0
    latitude, longitude = radians(reference["latitude"]), radians(reference["longitude"])
    return (-sin(longitude) * dx + cos(longitude) * dy,
            -sin(latitude) * cos(longitude) * dx - sin(latitude) * sin(longitude) * dy + cos(latitude) * dz)


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return hypot(first[0] - second[0], first[1] - second[1])


def _payload_id(value: Any) -> str:
    identifier = _identifier(value, "payload_id")
    resolved = PAYLOAD_ALIASES.get(identifier.upper(), identifier.lower())
    if resolved not in PAYLOAD_IDS:
        raise ValueError(f"Unsupported payload_id: {identifier}")
    return resolved


def _identifiers(raw: Any, field: str) -> list[str]:
    return [_identifier(item, field) for item in _sequence(raw, field)]


def _time_window(raw: Any, field: str) -> tuple[int, int | None]:
    window = _mapping(raw, field)
    start = window.get("start_sec")
    end = window.get("end_sec")
    lower = 0 if start is None else _integer(start, f"{field}.start_sec")
    upper = None if end is None else _integer(end, f"{field}.end_sec")
    if lower < 0 or (upper is not None and upper <= lower):
        raise ValueError(f"{field} has an invalid interval")
    return lower, upper


def _parse_request(request: Mapping[str, Any]) -> tuple[list[dict], list[dict], dict, dict, list[dict]]:
    if request.get("schema_version") != "1.0":
        raise ValueError("schema_version must be 1.0")
    for field in ("request_id", "mission_id"):
        _identifier(request.get(field), field)
    _integer(request.get("plan_version"), "plan_version", positive=True)
    _integer(request.get("context_version"), "context_version", positive=True)
    timestamp = _identifier(request.get("requested_at"), "requested_at")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("requested_at must be UTC ISO 8601") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("requested_at must use UTC")

    context = _mapping(request.get("scenario_context"), "scenario_context")
    reference = _position(context.get("map_reference_position"), "scenario_context.map_reference_position")
    parameters = _mapping(request.get("parameters", {}), "parameters")
    config = {
        "threshold": _number(parameters.get("aggregation_threshold", 0.55), "aggregation_threshold"),
        "max_distance": _number(parameters.get("max_bundle_distance_m", 10000), "max_bundle_distance_m", positive=True),
        "max_tasks": _integer(parameters.get("max_tasks_per_group", 4), "max_tasks_per_group", positive=True),
        "time_limit": _number(parameters.get("solver_time_limit_sec", 10), "solver_time_limit_sec", positive=True),
        "reserve": _number(parameters.get("endurance_reserve_ratio", 0.2), "endurance_reserve_ratio"),
    }
    if not 0 <= config["threshold"] <= 1 or not 0 <= config["reserve"] < 1:
        raise ValueError("aggregation_threshold or endurance_reserve_ratio outside valid range")
    completed = set(_identifiers(request.get("completed_task_ids", []), "completed_task_ids"))
    raw_tasks = _sequence(request.get("tasks"), "tasks")
    if not raw_tasks:
        raise ValueError("tasks must not be empty")
    tasks = []
    for raw in raw_tasks:
        item = _mapping(raw, "task")
        task_id = _identifier(item.get("task_id"), "task_id")
        task_type = _identifier(item.get("task_type"), "task_type").upper()
        if task_type not in TASK_TYPES:
            raise ValueError(f"Unsupported business task type: {task_type}")
        position = _position(item.get("target_position"), f"task {task_id}.target_position")
        constraints = _mapping(item.get("constraints", {}), f"task {task_id}.constraints")
        min_alt = _number(constraints.get("min_altitude_m", position["altitude_m"]), "min_altitude_m")
        max_alt = _number(constraints.get("max_altitude_m", position["altitude_m"]), "max_altitude_m")
        if min_alt > max_alt or not min_alt <= position["altitude_m"] <= max_alt:
            raise ValueError(f"Task {task_id} altitude outside constraints")
        policy = str(item.get("merge_policy", "BUNDLE_IF_COMPATIBLE")).upper()
        if policy not in POLICIES:
            raise ValueError(f"Task {task_id} has unsupported merge_policy")
        capabilities = {token.upper() for token in _identifiers(item.get("required_capabilities", []), "required_capabilities")}
        tasks.append({
            "id": task_id, "type": task_type, "area": _identifier(item.get("target_area_id"), "target_area_id"),
            "position": position, "enu": _enu(position, reference), "duration": _integer(item.get("duration_sec"), "duration_sec", positive=True),
            "priority": _integer(item.get("priority"), "priority", positive=True),
            "window": _time_window(item.get("time_window", {"start_sec": None, "end_sec": None}), "time_window"),
            "payloads": set(_payload_id(x) for x in _sequence(item.get("required_payload_ids", item.get("required_payloads", [])), "required_payload_ids")),
            "flight": capabilities - SENSOR_CAPABILITIES, "sensor": capabilities & SENSOR_CAPABILITIES,
            "depends": set(_identifiers(item.get("depends_on", []), "depends_on")),
            "policy": policy, "min_alt": min_alt, "max_alt": max_alt,
        })
    task_ids = [task["id"] for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("Duplicate task_id")
    missing = {dep for task in tasks for dep in task["depends"] if dep not in task_ids and dep not in completed}
    if missing:
        config["unresolved"] = sorted(missing)
    config["completed"] = completed

    snapshot = _mapping(request.get("fleet_snapshot"), "fleet_snapshot")
    _integer(snapshot.get("fleet_snapshot_version"), "fleet_snapshot_version", positive=True)
    warnings: list[dict] = []
    fleet = []
    for raw in _sequence(snapshot.get("aircraft"), "fleet_snapshot.aircraft"):
        item = _mapping(raw, "aircraft")
        aircraft_id = _identifier(item.get("aircraft_id"), "aircraft_id")
        model = _identifier(item.get("model_id"), "model_id").upper()
        profile = _mapping(item.get("aircraft_profile", {}), "aircraft_profile")
        defaults = PLATFORM_PROFILES.get(model, {})

        def profile_value(key: str, legacy_key: str | None = None) -> Any:
            value = profile.get(key, item.get(key))
            if value is None and legacy_key:
                value = defaults.get(legacy_key)
                if value is not None:
                    warnings.append({"code": "AIRCRAFT_PROFILE_DEFAULT_USED", "aircraft_id": aircraft_id, "field": key})
            if value is None:
                raise ValueError(f"Aircraft {aircraft_id} missing aircraft_profile.{key}")
            return value

        flight_capabilities = item.get("flight_capabilities")
        if flight_capabilities is None:
            flight_capabilities = defaults.get("simulation_flight_capabilities")
            if flight_capabilities is None:
                raise ValueError(f"Aircraft {aircraft_id} missing flight_capabilities")
            warnings.append({"code": "FLIGHT_CAPABILITIES_DEFAULT_USED", "aircraft_id": aircraft_id})
        installed = set(_payload_id(x) for x in _sequence(item.get("installed_payload_ids"), "installed_payload_ids"))
        available = item.get("available_payload_ids")
        if available is None:
            available = sorted(installed)
            warnings.append({"code": "PAYLOAD_AVAILABILITY_DEFAULT_USED", "aircraft_id": aircraft_id})
        available = set(_payload_id(x) for x in _sequence(available, "available_payload_ids"))
        if not available <= installed:
            raise ValueError(f"Aircraft {aircraft_id} available payload is not installed")
        position = _position(item.get("position"), f"aircraft {aircraft_id}.position")
        remaining = _integer(item.get("remaining_endurance_sec"), "remaining_endurance_sec")
        if remaining < 0:
            raise ValueError("remaining_endurance_sec must be nonnegative")
        available_flag = item.get("available")
        if not isinstance(available_flag, bool):
            raise ValueError(f"Aircraft {aircraft_id}.available must be boolean")
        fleet.append({
            "id": aircraft_id, "model": model, "xplane_instance_id": item.get("xplane_instance_id"),
            "available": available_flag, "position": position, "enu": _enu(position, reference),
            "installed": installed, "payloads": available,
            "flight": {x.upper() for x in _identifiers(flight_capabilities, "flight_capabilities")},
            "endurance": remaining, "max_work": _integer(profile_value("max_work_sec"), "max_work_sec", positive=True),
            "speed": _number(profile_value("cruise_speed_mps", "cruise_speed_mps"), "cruise_speed_mps", positive=True),
            "ceiling": _number(profile_value("service_ceiling_m", "service_ceiling_m"), "service_ceiling_m"),
        })
    if len({item["id"] for item in fleet}) != len(fleet):
        raise ValueError("Duplicate aircraft_id")
    if not fleet:
        raise ValueError("fleet_snapshot.aircraft must not be empty")
    return tasks, fleet, config, dict(snapshot), warnings


def _ordered_tasks(tasks: list[dict]) -> list[dict]:
    remaining = {task["id"]: task for task in tasks}
    result = []
    while remaining:
        ready = [item for item in remaining.values() if not item["depends"] & remaining.keys()]
        if not ready:
            raise ValueError("CYCLIC_DEPENDENCY")
        selected = min(ready, key=lambda item: (item["window"][0], item["id"]))
        result.append(selected)
        del remaining[selected["id"]]
    return result


def _group(tasks: list[dict], index: int) -> dict:
    ordered = _ordered_tasks(tasks)
    start = max(task["window"][0] for task in tasks)
    ends = [task["window"][1] for task in tasks if task["window"][1] is not None]
    end = min(ends) if ends else None
    return {
        "id": f"group-{index:03d}", "tasks": ordered, "ids": [task["id"] for task in ordered],
        "start": start, "end": end, "service": sum(task["duration"] for task in tasks),
        "first": ordered[0]["enu"], "last": ordered[-1]["enu"],
        "intra_distance": sum(_distance(a["enu"], b["enu"]) for a, b in zip(ordered, ordered[1:])),
        "payloads": set().union(*(task["payloads"] for task in tasks)),
        "flight": set().union(*(task["flight"] for task in tasks)),
        "sensor": set().union(*(task["sensor"] for task in tasks)),
        "priority": max(task["priority"] for task in tasks),
        "area": sorted({task["area"] for task in tasks}),
        "min_alt": max(task["min_alt"] for task in tasks),
        "max_alt": min(task["max_alt"] for task in tasks),
    }


def _candidate(group: dict, aircraft: dict, reserve: float) -> tuple[list[str], dict]:
    reasons = []
    if not aircraft["available"]:
        reasons.append("AIRCRAFT_UNAVAILABLE")
    missing = group["payloads"] - aircraft["payloads"]
    if missing:
        reasons.extend(f"MISSING_PAYLOAD:{token}" for token in sorted(missing))
    chosen = set(group["payloads"] & aircraft["payloads"])
    for token in sorted(group["sensor"]):
        if not any(token in PAYLOAD_IDS[payload] for payload in chosen):
            provider = next((payload for payload in sorted(aircraft["payloads"]) if token in PAYLOAD_IDS[payload]), None)
            if provider is None:
                reasons.append(f"MISSING_PAYLOAD_CAPABILITY:{token}")
            else:
                chosen.add(provider)
    for token in sorted(group["flight"] - aircraft["flight"]):
        reasons.append(f"MISSING_FLIGHT_CAPABILITY:{token}")
    if any(task["position"]["altitude_m"] > aircraft["ceiling"] for task in group["tasks"]):
        reasons.append("SERVICE_CEILING_TOO_LOW")
    entry = ceil(_distance(aircraft["enu"], group["first"]) / aircraft["speed"])
    internal = ceil(group["intra_distance"] / aircraft["speed"])
    duration = group["service"] + internal
    work = entry + duration
    if work > int(aircraft["endurance"] * (1 - reserve)):
        reasons.append("INSUFFICIENT_REMAINING_ENDURANCE")
    if work > aircraft["max_work"]:
        reasons.append("EXCEEDS_MAX_WORK_TIME")
    if group["end"] is not None and max(entry, group["start"]) + duration > group["end"]:
        reasons.append("TIME_WINDOW_CONFLICT")
    return reasons, {"entry": entry, "internal": internal, "duration": duration, "selected_payloads": chosen}


def _aggregate(tasks: list[dict], fleet: list[dict], config: dict) -> tuple[list[dict], list[dict], dict[str, str]]:
    trace = []
    replacement: dict[str, str] = {}
    retained = []
    for task in sorted(tasks, key=lambda item: item["id"]):
        duplicate = next((kept for kept in retained if task["policy"] == kept["policy"] == "DEDUPLICATE"
                          and task["type"] == kept["type"] and task["area"] == kept["area"]
                          and task["window"] == kept["window"] and task["duration"] == kept["duration"]
                          and task["payloads"] == kept["payloads"] and task["flight"] == kept["flight"]
                          and task["sensor"] == kept["sensor"] and task["depends"] == kept["depends"]
                          and task["min_alt"] == kept["min_alt"] and task["max_alt"] == kept["max_alt"]
                          and _distance(task["enu"], kept["enu"]) <= 1.0), None)
        if duplicate is None:
            retained.append(task)
        else:
            replacement[task["id"]] = duplicate["id"]
            trace.append({"operation": "deduplicate", "source_task_ids": [duplicate["id"], task["id"]],
                          "retained_task_id": duplicate["id"], "eliminated_task_ids": [task["id"]],
                          "reason": "Same task, position, window, payload and dependency requirements",
                          "constraints_rechecked": ["position", "time_window", "payload", "dependencies", "altitude"]})
    for task in retained:
        task["depends"] = {replacement.get(dep, dep) for dep in task["depends"]} - {task["id"]}

    groups = [[task] for task in retained]
    while True:
        choice = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                combined = groups[i] + groups[j]
                if len(combined) > config["max_tasks"] or any(task["policy"] != "BUNDLE_IF_COMPATIBLE" for task in combined):
                    continue
                if len({task["area"] for task in combined}) != 1:
                    continue
                if len({task["type"] for task in combined}) != 1:
                    continue
                if any(_distance(a["enu"], b["enu"]) > config["max_distance"] for a in combined for b in combined):
                    continue
                if max(task["min_alt"] for task in combined) > min(task["max_alt"] for task in combined):
                    continue
                try:
                    candidate = _group(combined, 0)
                except ValueError:
                    continue
                if not any(not _candidate(candidate, plane, config["reserve"])[0] for plane in fleet):
                    continue
                max_distance = max(_distance(a["enu"], b["enu"]) for a in combined for b in combined)
                same_payload = len({tuple(sorted(task["payloads"])) for task in combined}) == 1
                score = 0.45 + 0.2 + 0.15 * same_payload + 0.2 * max(0, 1 - max_distance / config["max_distance"])
                if score < config["threshold"]:
                    continue
                key = (round(score, 6), -i, -j)
                if choice is None or key > choice[0]:
                    choice = (key, i, j)
        if choice is None:
            break
        score, i, j = choice
        trace.append({"operation": "bundle", "source_task_ids": sorted(task["id"] for task in groups[i] + groups[j]),
                      "reason": "Area, task type, distance, time window and aircraft requirements compatible",
                      "score": score[0], "constraints_rechecked": ["distance", "time_window", "payload", "altitude", "endurance", "max_work_time"]})
        groups = [group for k, group in enumerate(groups) if k not in (i, j)] + [groups[i] + groups[j]]
    groups.sort(key=lambda group: min(task["id"] for task in group))
    result = [_group(group, index) for index, group in enumerate(groups, start=1)]
    for item in trace:
        if item["operation"] == "bundle":
            item["result_group_id"] = next((group["id"] for group in result if set(item["source_task_ids"]) <= set(group["ids"])), None)
    return result, trace, replacement


def _base(request: Mapping[str, Any]) -> dict:
    snapshot = request.get("fleet_snapshot", {})
    snapshot = snapshot if isinstance(snapshot, Mapping) else {}
    def safe_value(field: str) -> Any:
        value = request.get(field)
        return value if isinstance(value, (str, int)) and not isinstance(value, bool) else None

    return {
        "schema_version": "1.0", "request_id": safe_value("request_id"), "mission_id": safe_value("mission_id"),
        "allocation_id": str(uuid5(NAMESPACE_URL, ":".join(str(request.get(key)) for key in ("request_id", "mission_id", "plan_version", "context_version")) + ":" + str(snapshot.get("fleet_snapshot_version")))),
        "allocation_version": 1, "plan_version": safe_value("plan_version"), "context_version": safe_value("context_version"),
        "fleet_snapshot_version": snapshot.get("fleet_snapshot_version") if isinstance(snapshot.get("fleet_snapshot_version"), int) else None,
        "status": "ERROR", "reason": None, "task_groups": [], "assignments": [], "reserve_aircraft": [],
        "unassigned_task_ids": [], "candidate_summary": {}, "aggregation_trace": [], "warnings": [], "conflicts": [],
        "solver": {"solver_name": "not_run", "solver_status": "NOT_RUN", "optimal": False, "unassigned_group_ids": []},
        "validation": {"all_input_tasks_accounted_for": False, "every_group_assigned_at_most_once": False,
                       "dependencies_satisfied": False, "payload_constraints_satisfied": False,
                       "flight_constraints_satisfied": False, "endurance_constraints_satisfied": False},
    }


def aggregate_and_allocate(request: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate business tasks and allocate aircraft in one non-mutating call."""
    if not isinstance(request, Mapping):
        raise TypeError("request must be a Mapping")
    result = _base(request)
    raw_ids = [item.get("task_id") for item in request.get("tasks", []) if isinstance(item, Mapping)] if isinstance(request.get("tasks"), list) else []
    result["unassigned_task_ids"] = raw_ids
    try:
        # Copy input because aggregation remaps dependency IDs in internal task objects.
        tasks, fleet, config, snapshot, warnings = _parse_request(deepcopy(dict(request)))
        result["warnings"] = warnings
        result["unassigned_task_ids"] = [task["id"] for task in tasks]
        _ordered_tasks(tasks)
        if config.get("unresolved"):
            result["status"] = "BLOCKED"
            result["reason"] = "UNRESOLVED_DEPENDENCY"
            result["conflicts"] = [{"code": "UNRESOLVED_DEPENDENCY", "task_id": item} for item in config["unresolved"]]
            return result
        groups, trace, replaced = _aggregate(tasks, fleet, config)
        result["aggregation_trace"] = trace
        candidates: dict[str, list[str]] = {}
        rejected: dict[str, dict[str, list[str]]] = {}
        resources: dict[tuple[str, str], dict] = {}
        for group in groups:
            candidates[group["id"]] = []
            rejected[group["id"]] = {}
            for aircraft in fleet:
                reasons, resource = _candidate(group, aircraft, config["reserve"])
                if reasons:
                    rejected[group["id"]][aircraft["id"]] = reasons
                else:
                    candidates[group["id"]].append(aircraft["id"])
                    resources[group["id"], aircraft["id"]] = resource
            result["candidate_summary"][group["id"]] = {
                "accepted_aircraft_ids": candidates[group["id"]], "rejected_aircraft": rejected[group["id"]]
            }
        if any(not candidates[group["id"]] for group in groups):
            selection, solver = [], {"solver_name": "not_run", "solver_status": "INFEASIBLE", "optimal": False,
                                      "unassigned_group_ids": [group["id"] for group in groups], "wall_time_sec": 0.0}
            result["reason"] = "NO_FEASIBLE_AIRCRAFT"
        else:
            selection, solver = solve_allocation(groups, fleet, candidates, resources, config)
        result["solver"] = solver
        result["status"] = "ALLOCATED" if len(selection) == len(groups) else "INFEASIBLE"
        validation = validate_allocation(selection, groups, fleet, candidates, resources, config) if selection else {}
        if selection and not all(validation.values()):
            raise RuntimeError("Solver result failed independent validation")
        if result["status"] == "INFEASIBLE":
            result["reason"] = result["reason"] or "GLOBAL_CAPACITY_OR_TIME_CONFLICT"
            result["conflicts"].append({"code": result["reason"]})
            selection = []

        by_group = {item["group_id"]: item for item in selection}
        by_aircraft = {aircraft["id"]: [] for aircraft in fleet}
        for item in selection:
            by_aircraft[item["aircraft_id"]].append(item)
        for group in groups:
            item = by_group.get(group["id"])
            result["task_groups"].append({
                "group_id": group["id"], "member_task_ids": group["ids"], "group_type": GROUP_TYPES[group["tasks"][0]["type"]],
                "target_area_ids": group["area"], "target_positions": [task["position"] for task in group["tasks"]],
                "required_payload_ids": sorted(group["payloads"]),
                "required_capabilities": sorted(group["flight"] | group["sensor"]),
                "estimated_transit_time_sec": item["transit"] if item else None,
                "estimated_service_time_sec": group["service"],
                "estimated_total_workload_sec": item["transit"] + group["service"] if item else None,
                "scheduled_start_sec": item["start"] if item else None,
                "scheduled_end_sec": item["start"] + resources[group["id"], item["aircraft_id"]]["duration"] if item else None,
                "aggregation_reason": "Compatible area and constraints" if len(group["tasks"]) > 1 else "Single business task",
            })
        aircraft_by_id = {item["id"]: item for item in fleet}
        for aircraft_id, rows in by_aircraft.items():
            aircraft = aircraft_by_id[aircraft_id]
            if not rows:
                if aircraft["available"]:
                    result["reserve_aircraft"].append({"aircraft_id": aircraft_id, "model_id": aircraft["model"],
                                                       "xplane_instance_id": aircraft["xplane_instance_id"]})
                continue
            rows.sort(key=lambda row: (row["start"], row["group_id"]))
            duration = sum(row["transit"] + row["service"] for row in rows)
            result["assignments"].append({
                "aircraft_id": aircraft_id, "model_id": aircraft["model"], "xplane_instance_id": aircraft["xplane_instance_id"],
                "role": "PRIMARY", "group_ids": [row["group_id"] for row in rows],
                "task_sequence": [task_id for row in rows for task_id in row["task_ids"]],
                "selected_payload_ids": sorted(set().union(*(set(row["payloads"]) for row in rows))),
                "estimated_transit_time_sec": sum(row["transit"] for row in rows),
                "estimated_service_time_sec": sum(row["service"] for row in rows),
                "estimated_total_workload_sec": duration,
                "estimated_remaining_endurance_sec": aircraft["endurance"] - duration,
                "assignment_reason": "Payload, capability, altitude, time window and endurance satisfied",
            })
        if result["status"] == "ALLOCATED":
            result["unassigned_task_ids"] = []
        assigned_groups = [row["group_id"] for row in selection]
        covered = {task_id for group in groups for task_id in group["ids"]}
        eliminated = set(replaced)
        unassigned = set(result["unassigned_task_ids"])
        result["validation"] = {
            "all_input_tasks_accounted_for": set(raw_ids) <= covered | eliminated | unassigned,
            "every_group_assigned_at_most_once": len(assigned_groups) == len(set(assigned_groups)),
            "dependencies_satisfied": validation.get("dependencies_satisfied", False),
            "payload_constraints_satisfied": validation.get("payload_constraints_satisfied", False),
            "flight_constraints_satisfied": validation.get("flight_constraints_satisfied", False),
            "endurance_constraints_satisfied": validation.get("endurance_constraints_satisfied", False),
        }
        return result
    except (ValueError, TypeError, KeyError) as exc:
        result["status"] = "BLOCKED" if str(exc) == "CYCLIC_DEPENDENCY" else "ERROR"
        result["reason"] = str(exc)
        result["conflicts"] = [{"code": "INVALID_REQUEST" if result["status"] == "ERROR" else "CYCLIC_DEPENDENCY", "detail": str(exc)}]
        return result
    except Exception as exc:
        result["status"] = "ERROR"
        result["reason"] = "INTERNAL_ERROR"
        result["conflicts"] = [{"code": "INTERNAL_ERROR", "detail": type(exc).__name__}]
        return result
