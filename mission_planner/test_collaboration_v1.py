"""Contract checks run with CP-SAT and with the documented fallback."""

import copy
import json
from pathlib import Path
from unittest.mock import patch

from mission_planner import aggregate_and_allocate


def request():
    return json.loads((Path(__file__).parent / "examples" / "collaboration_three_aircraft.json").read_text(encoding="utf-8"))


def test_three_aircraft_cp_sat_and_immutable_input():
    source = request()
    original = copy.deepcopy(source)
    result = aggregate_and_allocate(source)
    assert source == original
    assert result["status"] == "ALLOCATED", result
    assert result["solver"]["solver_name"] == "cp_sat"
    assert result["solver"]["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert {assignment["aircraft_id"] for assignment in result["assignments"]} == {"C172-01", "C172-02", "SF50-01"}
    assert all(group["estimated_transit_time_sec"] > 0 for group in result["task_groups"])
    assert all(result["validation"].values())
    assert all(assignment["estimated_remaining_endurance_sec"] < 10800 for assignment in result["assignments"])
    json.dumps(result, allow_nan=False)


def test_missing_radar_payload_rejected_with_aircraft_reasons():
    source = request()
    for plane in source["fleet_snapshot"]["aircraft"]:
        plane["installed_payload_ids"] = ["payload.stormcaster_e"]
        plane["available_payload_ids"] = ["payload.stormcaster_e"]
    result = aggregate_and_allocate(source)
    assert result["status"] == "INFEASIBLE"
    assert result["reason"] == "NO_FEASIBLE_AIRCRAFT"
    radar_group = next(group for group in result["task_groups"] if "search-d-radar" in group["member_task_ids"])
    assert all(any(reason.startswith("MISSING_PAYLOAD") for reason in reasons)
               for reasons in result["candidate_summary"][radar_group["group_id"]]["rejected_aircraft"].values())


def test_sf50_loiter_allowed_from_snapshot():
    source = request()
    task = source["tasks"][2]
    task["required_capabilities"] = ["LOITER", "RADAR"]
    result = aggregate_and_allocate(source)
    assert result["status"] == "ALLOCATED"
    assert any("search-d-radar" in assignment["task_sequence"] and assignment["aircraft_id"] == "SF50-01"
               for assignment in result["assignments"])


def test_unresolved_dependency_blocked():
    source = request()
    source["tasks"][0]["depends_on"] = ["unknown-task"]
    result = aggregate_and_allocate(source)
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "UNRESOLVED_DEPENDENCY"
    assert set(result["unassigned_task_ids"]) == {item["task_id"] for item in source["tasks"]}


def test_endurance_accounts_for_initial_transit_and_reserve():
    source = request()
    source["fleet_snapshot"]["aircraft"][0]["remaining_endurance_sec"] = 100
    result = aggregate_and_allocate(source)
    assert result["status"] == "INFEASIBLE"
    group_id = next(group["group_id"] for group in result["task_groups"] if "observe-b-visible" in group["member_task_ids"])
    assert "INSUFFICIENT_REMAINING_ENDURANCE" in result["candidate_summary"][group_id]["rejected_aircraft"]["C172-01"]


def test_explicit_deduplication_trace_and_dependency():
    source = request()
    duplicate = copy.deepcopy(source["tasks"][0])
    duplicate["task_id"] = "observe-b-visible-copy"
    source["tasks"][0]["merge_policy"] = "DEDUPLICATE"
    duplicate["merge_policy"] = "DEDUPLICATE"
    source["tasks"].append(duplicate)
    result = aggregate_and_allocate(source)
    assert result["status"] == "ALLOCATED", result
    assert any(item["operation"] == "deduplicate" and item["eliminated_task_ids"] for item in result["aggregation_trace"])
    assert result["validation"]["all_input_tasks_accounted_for"]


def test_greedy_fallback_is_explicit():
    source = request()
    with patch.dict("sys.modules", {"ortools": None}):
        result = aggregate_and_allocate(source)
    assert result["status"] == "ALLOCATED", result
    assert result["solver"]["solver_name"] == "deterministic_greedy_fallback"
    assert result["solver"]["optimal"] is False


def test_second_group_requires_inter_group_transit_and_cumulative_endurance():
    source = request()
    second = copy.deepcopy(source["tasks"][0])
    second["task_id"] = "observe-b-second"
    second["target_area_id"] = "AREA-F"
    second["target_position"]["longitude"] += 0.07
    second["depends_on"] = ["observe-b-visible"]
    source["tasks"].append(second)
    result = aggregate_and_allocate(source)
    assert result["status"] == "ALLOCATED", result
    rows = {group["member_task_ids"][0]: group for group in result["task_groups"]}
    assert rows["observe-b-second"]["estimated_transit_time_sec"] > 0
    first_plane = next(assignment for assignment in result["assignments"] if assignment["aircraft_id"] == "C172-01")
    assert first_plane["task_sequence"] == ["observe-b-visible", "observe-b-second"]
    assert first_plane["estimated_total_workload_sec"] == sum(rows[task]["estimated_total_workload_sec"] for task in first_plane["task_sequence"])


def test_visible_alias_selects_stormcaster_and_unavailable_payload_is_rejected():
    source = request()
    source["tasks"][0]["required_payload_ids"] = ["VISIBLE"]
    result = aggregate_and_allocate(source)
    assert result["status"] == "ALLOCATED", result
    first = next(item for item in result["assignments"] if item["aircraft_id"] == "C172-01")
    assert first["selected_payload_ids"] == ["payload.stormcaster_e"]

    source["fleet_snapshot"]["aircraft"][0]["available_payload_ids"] = []
    rejected = aggregate_and_allocate(source)
    assert rejected["status"] == "INFEASIBLE"
    group_id = next(group["group_id"] for group in rejected["task_groups"] if "observe-b-visible" in group["member_task_ids"])
    assert any(item.startswith("MISSING_PAYLOAD") for item in rejected["candidate_summary"][group_id]["rejected_aircraft"]["C172-01"])
