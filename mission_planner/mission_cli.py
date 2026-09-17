from __future__ import annotations

import argparse
import json
from pathlib import Path

from .mission_allocator import allocate_mission


def main() -> None:
    parser = argparse.ArgumentParser(description="Allocate normalized mission task groups to aircraft")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("outputs/allocation.json"))
    args = parser.parse_args()
    with args.request.open("r", encoding="utf-8") as handle:
        request = json.load(handle)
    result = allocate_mission(
        request["planner_output"],
        request["aggregation_output"],
        request["fleet_snapshot"],
        completed_task_ids=request.get("completed_task_ids", []),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
