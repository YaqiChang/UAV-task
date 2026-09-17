from __future__ import annotations

from typing import Any, Dict, List, Optional

from .domain import UAV


def fetch_multiuav_drones(
    server_url: str,
    capability_profiles: Dict[str, Dict[str, Any]],
    timeout_s: float = 5.0,
    api_key: Optional[str] = None,
) -> List[UAV]:
    """
    Read dynamic platform state from MultiUAV-Plat and add planner-only
    capability profiles keyed by UAV ID or model.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "requests is required only for the live MultiUAV-Plat adapter"
        ) from exc
    headers = {"X-API-Key": api_key} if api_key else None
    response = requests.get(
        f"{server_url.rstrip('/')}/drones",
        timeout=timeout_s,
        headers=headers,
    )
    response.raise_for_status()
    raw_drones = response.json()
    if not isinstance(raw_drones, list):
        raise ValueError("GET /drones did not return a list")

    result: List[UAV] = []
    for drone in raw_drones:
        if not isinstance(drone, dict) or "id" not in drone:
            raise ValueError("Each drone returned by GET /drones must contain an id")
        profile = capability_profiles.get(str(drone.get("id")))
        if profile is None:
            profile = capability_profiles.get(str(drone.get("model")), {})
        merged = {
            **drone,
            **profile,
            "id": drone["id"],
            "position": drone.get("position", {"x": 0.0, "y": 0.0}),
            "battery_percent": drone.get("battery_level", 100.0),
            "max_speed_mps": drone.get("max_speed", 10.0),
            "available": drone.get("status") not in {"offline", "emergency"},
        }
        result.append(UAV.from_dict(merged))
    return result


def allocation_to_multiuav_plan(allocation: Dict[str, Any]) -> Dict[str, Any]:
    """Convert allocation output into a route-planner-facing task bundle.

    This adapter does not generate waypoints or send execution commands. It
    returns task sequences, target areas and constraints for downstream
    path-planning and Agent4Drone layers.
    """
    if allocation.get("status") != "ALLOCATED":
        raise ValueError(
            f"Only ALLOCATED results can be adapted, got {allocation.get('status')}"
        )
    bundles: List[Dict[str, Any]] = []
    for aircraft in allocation.get("assignments", []):
        groups = aircraft.get("groups", [])
        if not groups:
            continue
        bundles.append(
            {
                "aircraft_id": aircraft["aircraft_id"],
                "aircraft_model": aircraft.get("aircraft_model"),
                "task_sequence": aircraft.get("task_sequence", []),
                "task_groups": [
                    {
                        "group_id": group["group_id"],
                        "task_ids": group.get("task_ids", []),
                        "target_area_ids": group.get("target_area_ids", []),
                        "payload_tokens": group.get("payload_tokens", []),
                        "payload_ids": group.get("payload_ids", []),
                        "required_capabilities": group.get(
                            "required_capabilities", []
                        ),
                        "start_time_sec": group.get("start_time_sec"),
                        "end_time_sec": group.get("end_time_sec"),
                    }
                    for group in groups
                ],
                "route_request": {
                    "target_area_ids": sorted(
                        {
                            area
                            for group in groups
                            for area in group.get("target_area_ids", [])
                        }
                    ),
                    "payload_tokens": sorted(
                        {
                            payload
                            for group in groups
                            for payload in group.get("payload_tokens", [])
                        }
                    ),
                    "payload_ids": sorted(
                        {
                            payload_id
                            for group in groups
                            for payload_id in group.get("payload_ids", [])
                        }
                    ),
                },
            }
        )
    return {
        "mission_id": allocation.get("mission_id"),
        "allocation_version": allocation.get("allocation_version", 1),
        "context_version": allocation.get("context_version"),
        "status": "READY_FOR_ROUTE_PLANNING",
        "aircraft_task_bundles": bundles,
        "reserve_aircraft": allocation.get("reserve_aircraft", []),
    }
