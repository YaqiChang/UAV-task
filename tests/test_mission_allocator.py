import json
from pathlib import Path

from mission_planner.mission_allocator import allocate_mission
from mission_planner.multiuav_adapter import allocation_to_multiuav_plan


ROOT = Path(__file__).resolve().parents[1]


def _request():
    return json.loads(
        (ROOT / "examples" / "recon_allocation_request.json").read_text(
            encoding="utf-8"
        )
    )


def test_frontend_contract_allocates_visible_recon_task():
    request = _request()
    result = allocate_mission(
        request["planner_output"],
        request["aggregation_output"],
        request["fleet_snapshot"],
        request["completed_task_ids"],
    )
    assert result["status"] == "ALLOCATED"
    assert result["solver"]["unassigned_group_ids"] == []
    primary = next(item for item in result["assignments"] if item["groups"])
    assert primary["aircraft_id"] == "C172-01"
    assert primary["groups"][0]["payload_ids"] == ["payload.wescam_mx15"]
    assert len(result["reserve_aircraft"]) == 9


def test_unresolved_external_dependency_blocks_allocation():
    request = _request()
    result = allocate_mission(
        request["planner_output"],
        request["aggregation_output"],
        request["fleet_snapshot"],
        completed_task_ids=[],
    )
    assert result["status"] == "BLOCKED"
    assert result["reason"] == "UNRESOLVED_DEPENDENCY"
    assert result["dependency_validation"]["blockers"][0]["dependency"] == "T00"


def test_route_adapter_keeps_allocator_boundary():
    request = _request()
    allocation = allocate_mission(
        request["planner_output"],
        request["aggregation_output"],
        request["fleet_snapshot"],
        request["completed_task_ids"],
    )
    route_input = allocation_to_multiuav_plan(allocation)
    assert route_input["status"] == "READY_FOR_ROUTE_PLANNING"
    assert route_input["aircraft_task_bundles"][0]["aircraft_id"] == "C172-01"
