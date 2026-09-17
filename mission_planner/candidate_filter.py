"""Hard candidate filtering before allocation."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from .capability_model import capabilities_for_payload, enrich_platform
from .schemas import AircraftResource, NormalizedGroup


def _payload_match(group: NormalizedGroup, aircraft: AircraftResource) -> Tuple[bool, List[str]]:
    installed = {item.upper() for item in aircraft.installed_payloads}
    missing: List[str] = []
    for token in group.required_payloads:
        aliases = capabilities_for_payload(token)
        matches = set()
        for installed_payload in installed:
            try:
                matches.update(capabilities_for_payload(installed_payload))
            except ValueError:
                matches.add(installed_payload)
        if not aliases.intersection(matches):
            missing.append(token)
    return not missing, missing


def _flight_capability_match(group: NormalizedGroup, aircraft: AircraftResource) -> Tuple[bool, List[str]]:
    available = {item.upper() for item in aircraft.flight_capabilities}
    missing = [item for item in group.required_capabilities if item.upper() not in available]
    return not missing, missing


def _altitude_match(group: NormalizedGroup, aircraft: AircraftResource) -> Tuple[bool, str]:
    if group.max_altitude_m is not None and aircraft.service_ceiling_m is not None:
        if group.max_altitude_m > aircraft.service_ceiling_m:
            return False, "SERVICE_CEILING_TOO_LOW"
    return True, ""


def filter_candidates(
    groups: Sequence[NormalizedGroup],
    raw_aircraft: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, List[str]]], List[AircraftResource]]:
    aircraft: List[AircraftResource] = []
    for raw in raw_aircraft:
        aircraft.append(AircraftResource.from_dict(enrich_platform(raw)))
    candidates: Dict[str, List[str]] = {}
    rejected: Dict[str, Dict[str, List[str]]] = {}
    for group in groups:
        candidates[group.group_id] = []
        rejected[group.group_id] = {}
        for item in aircraft:
            reasons: List[str] = []
            if not item.available:
                reasons.append("AIRCRAFT_UNAVAILABLE")
            payload_ok, missing_payloads = _payload_match(group, item)
            if not payload_ok:
                reasons.append("MISSING_PAYLOAD:" + ",".join(sorted(missing_payloads)))
            capability_ok, missing_capabilities = _flight_capability_match(group, item)
            if not capability_ok:
                reasons.append("MISSING_FLIGHT_CAPABILITY:" + ",".join(sorted(missing_capabilities)))
            altitude_ok, altitude_reason = _altitude_match(group, item)
            if not altitude_ok:
                reasons.append(altitude_reason)
            if group.total_workload_sec > item.remaining_endurance_sec:
                reasons.append("INSUFFICIENT_REMAINING_ENDURANCE")
            if group.total_workload_sec > item.max_work_sec:
                reasons.append("EXCEEDS_MAX_WORK_TIME")
            if reasons:
                rejected[group.group_id][item.aircraft_id] = reasons
            else:
                candidates[group.group_id].append(item.aircraft_id)
    return candidates, rejected, aircraft
