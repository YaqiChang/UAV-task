import json
from pathlib import Path

from scripts.recon_validation import build_scenarios, run_validation


ROOT = Path(__file__).resolve().parents[1]


def _request():
    return json.loads(
        (ROOT / "examples" / "recon_allocation_request.json").read_text(
            encoding="utf-8"
        )
    )


def test_validation_matrix_contains_all_report_scenarios():
    scenarios = build_scenarios(_request())
    assert [item["scenario_id"] for item in scenarios] == [
        "baseline_visible",
        "infrared_loiter",
        "radar",
        "dependency_blocked",
        "radar_without_payload",
    ]


def test_validation_matrix_writes_machine_and_human_readable_outputs(tmp_path):
    rows = run_validation(_request(), tmp_path, "test-request.json")
    by_id = {row["scenario_id"]: row for row in rows}

    assert by_id["baseline_visible"]["status"] == "ALLOCATED"
    assert by_id["infrared_loiter"]["status"] == "ALLOCATED"
    assert by_id["radar"]["status"] == "ALLOCATED"
    assert by_id["dependency_blocked"]["status"] == "BLOCKED"
    assert by_id["radar_without_payload"]["status"] == "INFEASIBLE"
    assert (tmp_path / "summary.csv").exists()
    assert (tmp_path / "report.md").exists()
    assert (tmp_path / "report.html").exists()
    assert (tmp_path / "allocation_summary.svg").exists()
    assert (tmp_path / "scenarios" / "radar_route_plan.json").exists()
