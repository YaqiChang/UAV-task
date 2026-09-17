from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

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
