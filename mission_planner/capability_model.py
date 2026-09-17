"""Payload aliases and simulation capability profiles.

The payload records follow the supplied virtual-payload design.  Platform
compatibility is intentionally an explicit simulation profile because the
provided C172 and SF50 pilot manuals do not establish real payload mounting,
electrical integration, or airworthiness compatibility.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Set, Tuple


PAYLOAD_ALIAS_CAPABILITIES: Dict[str, Set[str]] = {
    "VISIBLE": {"EO_DAYLIGHT"},
    "EO": {"EO_DAYLIGHT"},
    "EO_DAYLIGHT": {"EO_DAYLIGHT"},
    "INFRARED": {"IR_THERMAL"},
    "IR": {"IR_THERMAL"},
    "IR_THERMAL": {"IR_THERMAL"},
    "SWIR": {"SWIR"},
    "RADAR": {"RADAR", "SAR", "RADAR_GMTI", "DMTI"},
    "SAR": {"SAR"},
    "RADAR_GMTI": {"RADAR_GMTI"},
    "GMTI": {"RADAR_GMTI"},
    "DMTI": {"DMTI"},
}


PAYLOAD_CATALOG: Dict[str, Dict[str, Any]] = {
    "PAYLOAD.WESCAM_MX15": {
        "payload_id": "payload.wescam_mx15",
        "payload_family": "EO_IR",
        "capabilities": [
            "EO_DAYLIGHT",
            "IR_THERMAL",
            "SWIR",
            "VIDEO_TRACKING",
            "VIDEO_MTI",
            "TARGET_GEOLOCATION",
        ],
        "mass_kg_upper_bound": 43.2,
        "average_power_w": None,
        "value_origin": "PUBLIC_OR_UNKNOWN",
    },
    "PAYLOAD.LYNX_MMR": {
        "payload_id": "payload.lynx_mmr",
        "payload_family": "RADAR",
        "capabilities": [
            "RADAR",
            "SAR",
            "RADAR_GMTI",
            "DMTI",
            "MARITIME_SEARCH",
            "ISAR",
            "TARGET_CUEING",
        ],
        "mass_kg_upper_bound": 62.0,
        "average_power_w": 1400.0,
        "peak_power_w": 1900.0,
        "value_origin": "PUBLIC_OR_PROJECT_MODEL",
    },
}


PLATFORM_PROFILES: Dict[str, Dict[str, Any]] = {
    "C172": {
        "model": "C172",
        "service_ceiling_m": 4267.0,
        "cruise_speed_mps": 63.79,
        "fuel_burn_reference": 8.0,
        "performance_source": "C172 Pilot Operating Manual",
        "simulation_flight_capabilities": [
            "LOITER",
            "LOW_ALTITUDE_OPERATION",
            "SHORT_RANGE_RECON",
        ],
    },
    "SF50": {
        "model": "SF50",
        "service_ceiling_m": 8534.0,
        "cruise_speed_mps": 154.33,
        "fuel_burn_reference": 315.0,
        "performance_source": "SF50 Pilot Operating Manual",
        "simulation_flight_capabilities": [
            "FAST_TRANSIT",
            "HIGH_ALTITUDE_OPERATION",
            "LONG_RANGE_RECON",
        ],
    },
}


def canonical_payload_token(token: str) -> str:
    return str(token).strip().upper().replace("-", "_")


def capabilities_for_payload(token: str) -> Set[str]:
    canonical = canonical_payload_token(token)
    if canonical in PAYLOAD_CATALOG:
        return {str(item).upper() for item in PAYLOAD_CATALOG[canonical]["capabilities"]}
    if canonical not in PAYLOAD_ALIAS_CAPABILITIES:
        raise ValueError(f"Unsupported payload token: {token}")
    return set(PAYLOAD_ALIAS_CAPABILITIES[canonical])


def normalize_payload_requirements(tokens: Iterable[str]) -> Tuple[Set[str], Dict[str, Set[str]]]:
    labels: Set[str] = set()
    capability_groups: Dict[str, Set[str]] = {}
    for token in tokens:
        canonical = canonical_payload_token(token)
        capabilities = capabilities_for_payload(canonical)
        labels.add(canonical)
        capability_groups[canonical] = capabilities
    return labels, capability_groups


def payload_ids_for_requirements(
    tokens: Iterable[str], installed_payloads: Iterable[str]
) -> Set[str]:
    """Return installed payload entities that can satisfy the requested tokens."""
    selected: Set[str] = set()
    installed = list(installed_payloads)
    for token in tokens:
        required = capabilities_for_payload(token)
        matches = []
        for payload in installed:
            try:
                provided = capabilities_for_payload(payload)
            except ValueError:
                continue
            if required.intersection(provided):
                canonical = canonical_payload_token(payload)
                if canonical in PAYLOAD_CATALOG:
                    matches.append(str(PAYLOAD_CATALOG[canonical]["payload_id"]))
                else:
                    matches.append(str(payload))
        selected.update(matches)
    return selected


def enrich_platform(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Add documented performance and explicit simulation capabilities."""
    result = dict(raw)
    model = str(raw.get("model", "UNKNOWN")).upper()
    profile = PLATFORM_PROFILES.get(model, {})
    result.setdefault("service_ceiling_m", profile.get("service_ceiling_m"))
    result.setdefault("cruise_speed_mps", profile.get("cruise_speed_mps"))
    result.setdefault("fuel_burn_reference", profile.get("fuel_burn_reference"))
    result.setdefault("performance_source", profile.get("performance_source"))
    if "flight_capabilities" not in result and profile:
        result["flight_capabilities"] = list(profile["simulation_flight_capabilities"])
    return result
