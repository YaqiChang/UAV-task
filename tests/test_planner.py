from mission_planner.aggregation import aggregate_tasks
from mission_planner.allocation import allocate_packages
import pytest

from mission_planner.domain import AtomicTask, MissionPackage, UAV, load_tasks


def test_aggregation_and_allocation():
    tasks = [
        AtomicTask(
            id="T1",
            action="search",
            region_id="A",
            position=(100.0, 100.0),
            duration_s=100,
            required_capabilities={"eo"},
            merge_policy="bundle_only",
        ),
        AtomicTask(
            id="T2",
            action="track",
            region_id="A",
            position=(110.0, 100.0),
            duration_s=120,
            required_capabilities={"eo", "tracking"},
            predecessors={"T1"},
            target_id="X",
            merge_policy="bundle_only",
        ),
        AtomicTask(
            id="T3",
            action="relay",
            region_id="B",
            position=(400.0, 400.0),
            duration_s=120,
            required_capabilities={"relay"},
            merge_policy="never",
        ),
    ]
    uavs = [
        UAV(
            id="U1",
            position=(0.0, 0.0),
            max_speed_mps=20,
            battery_percent=90,
            capabilities={"eo", "tracking"},
            energy_budget=1000,
            reserve_energy=100,
            max_work_s=1000,
        ),
        UAV(
            id="U2",
            position=(300.0, 300.0),
            max_speed_mps=20,
            battery_percent=90,
            capabilities={"relay"},
            energy_budget=1000,
            reserve_energy=100,
            max_work_s=1000,
        ),
    ]

    packages, _trace = aggregate_tasks(tasks, uavs, threshold=0.5)
    assert len(packages) == 2

    assignments, metadata = allocate_packages(packages, uavs)
    assert metadata["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assigned = {
        package["package_id"]
        for item in assignments
        for package in item["packages"]
    }
    assert assigned == {package.id for package in packages}


def _uav(identifier="U1", capabilities=None, energy=2000, battery=100):
    return UAV(
        id=identifier,
        position=(0.0, 0.0),
        max_speed_mps=20,
        battery_percent=battery,
        capabilities=set(capabilities or {"eo"}),
        energy_budget=energy,
        reserve_energy=100,
        max_work_s=2000,
    )


def _task(identifier, policy, position=(0.0, 0.0), capabilities=None):
    return AtomicTask(
        id=identifier,
        action="search",
        region_id="A",
        position=position,
        duration_s=100,
        required_capabilities=set(capabilities or {"eo"}),
        earliest_start_s=0,
        latest_finish_s=1000,
        merge_policy=policy,
    )


def test_only_deduplicate_policy_can_remove_atomic_tasks():
    tasks = [
        _task("D1", "deduplicate"),
        _task("D2", "deduplicate", position=(2.0, 0.0)),
        _task("B1", "bundle_only"),
        _task("B2", "bundle_only", position=(2.0, 0.0)),
        _task("N1", "never"),
    ]
    packages, trace = aggregate_tasks(tasks, [_uav()], threshold=0.0)
    retained = [task.id for package in packages for task in package.tasks]

    assert "D1" in retained
    assert "D2" not in retained
    assert {"B1", "B2", "N1"}.issubset(retained)
    dedup = next(item for item in trace if item["operation"] == "deduplicate")
    assert dedup["source_task_ids"] == ["D1", "D2"]
    assert dedup["eliminated_task_ids"] == ["D2"]


def test_bundle_rechecks_full_spatial_diameter():
    tasks = [
        _task("T1", "bundle_only", position=(0.0, 0.0)),
        _task("T2", "bundle_only", position=(90.0, 0.0)),
        _task("T3", "bundle_only", position=(180.0, 0.0)),
    ]
    packages, _ = aggregate_tasks(
        tasks,
        [_uav()],
        threshold=0.0,
        max_bundle_distance_m=100.0,
    )
    assert len(packages) == 2
    assert max(len(package.tasks) for package in packages) == 2


def test_allocation_enforces_cumulative_energy_and_work():
    packages = [
        MissionPackage(id="P1", tasks=[_task("T1", "never")]),
        MissionPackage(id="P2", tasks=[_task("T2", "never")]),
    ]
    # Each package costs 100 energy. U1 has only 150 usable energy, so CP-SAT
    # must place the second package on U2 rather than checking packages alone.
    uavs = [
        _uav("U1", energy=250),
        _uav("U2", energy=1000),
    ]
    assignments, metadata = allocate_packages(packages, uavs)
    assert metadata["solver_status"] in {"OPTIMAL", "FEASIBLE"}
    assert all(item["total_energy"] <= item["usable_energy"] for item in assignments)
    assigned_ids = [
        package["package_id"]
        for item in assignments
        for package in item["packages"]
    ]
    assert sorted(assigned_ids) == ["P1", "P2"]


def test_atomic_task_requires_complete_schema():
    with pytest.raises(ValueError, match="missing fields"):
        load_tasks([{"id": "incomplete"}])
