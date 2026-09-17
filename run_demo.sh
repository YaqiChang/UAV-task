#!/usr/bin/env bash
set -euo pipefail

python -m mission_planner.cli \
  --tasks examples/tasks.json \
  --uavs examples/uavs.json \
  --output outputs/plan.json

python -m mission_planner.visualize \
  --plan outputs/plan.json \
  --output outputs/report.html

echo
echo "Generated:"
echo "  outputs/plan.json"
echo "  outputs/report.html"
