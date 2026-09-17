#!/usr/bin/env python3
"""Run reproducible reconnaissance allocation scenarios and build a report.

The script keeps the allocator unchanged and evaluates the front-end contract
through ``allocate_mission``.  It writes raw results together with compact CSV,
Markdown, SVG, and self-contained HTML summaries.
"""

from __future__ import annotations

import argparse
import copy
import csv
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence

# Allow ``python scripts/recon_validation.py`` to import project packages when
# the interpreter sets sys.path[0] to the scripts directory.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mission_planner.mission_allocator import allocate_mission
from mission_planner.multiuav_adapter import allocation_to_multiuav_plan


Scenario = Dict[str, Any]


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def load_request(
    request_path: Path | None = None,
    planner_path: Path | None = None,
    aggregation_path: Path | None = None,
    fleet_path: Path | None = None,
) -> Dict[str, Any]:
    """Load either the combined request or three front-end snapshots."""
    if request_path is not None:
        return _read_json(request_path)
    if not all((planner_path, aggregation_path, fleet_path)):
        raise ValueError(
            "provide --request or all of --planner-output, "
            "--aggregation-output, and --fleet-snapshot"
        )
    return {
        "planner_output": _read_json(planner_path),
        "aggregation_output": _read_json(aggregation_path),
        "fleet_snapshot": _read_json(fleet_path),
        "completed_task_ids": [],
    }


def _group_member_ids(request: Mapping[str, Any]) -> set[str]:
    groups = request["aggregation_output"].get("task_groups", [])
    return {
        str(task_id)
        for group in groups
        for task_id in group.get("member_task_ids", [])
    }


def _set_recon_payload(
    request: MutableMapping[str, Any],
    payload_token: str,
    required_capabilities: Sequence[str],
) -> None:
    member_ids = _group_member_ids(request)
    for task in request["planner_output"].get("tasks", []):
        if str(task.get("task_id")) in member_ids:
            task["required_payloads"] = [payload_token]
            task["required_capabilities"] = list(required_capabilities)
    for group in request["aggregation_output"].get("task_groups", []):
        group["required_payloads"] = [payload_token]
        group["required_capabilities"] = list(required_capabilities)


def build_scenarios(request: Mapping[str, Any]) -> List[Scenario]:
    """Create the fixed scenario matrix used for regression and reporting."""
    scenarios: List[Scenario] = []

    baseline = copy.deepcopy(dict(request))
    scenarios.append(
        {
            "scenario_id": "baseline_visible",
            "title": "VISIBLE + LOITER",
            "description": "基线：可见光侦察，要求区域盘旋",
            "request": baseline,
        }
    )

    infrared = copy.deepcopy(dict(request))
    _set_recon_payload(infrared, "INFRARED", ["LOITER"])
    scenarios.append(
        {
            "scenario_id": "infrared_loiter",
            "title": "INFRARED + LOITER",
            "description": "替换载荷：红外侦察，要求区域盘旋",
            "request": infrared,
        }
    )

    radar = copy.deepcopy(dict(request))
    _set_recon_payload(radar, "RADAR", [])
    scenarios.append(
        {
            "scenario_id": "radar",
            "title": "RADAR",
            "description": "雷达侦察：由具备 Lynx 雷达的 SF50 执行",
            "request": radar,
        }
    )

    blocked = copy.deepcopy(dict(request))
    blocked["completed_task_ids"] = []
    scenarios.append(
        {
            "scenario_id": "dependency_blocked",
            "title": "DEPENDENCY BLOCKED",
            "description": "移除 T00 完成标记，验证依赖校验",
            "request": blocked,
        }
    )

    no_radar = copy.deepcopy(radar)
    for aircraft in no_radar["fleet_snapshot"].get("aircraft", []):
        aircraft["installed_payloads"] = [
            payload
            for payload in aircraft.get("installed_payloads", [])
            if "lynx" not in str(payload).lower()
        ]
    scenarios.append(
        {
            "scenario_id": "radar_without_payload",
            "title": "RADAR WITHOUT PAYLOAD",
            "description": "移除所有 Lynx 雷达，验证不可行诊断",
            "request": no_radar,
        }
    )
    return scenarios


def run_scenario(scenario: Mapping[str, Any]) -> Dict[str, Any]:
    request = scenario["request"]
    result = allocate_mission(
        request["planner_output"],
        request["aggregation_output"],
        request["fleet_snapshot"],
        request.get("completed_task_ids", []),
    )
    route_plan = None
    if result.get("status") == "ALLOCATED":
        route_plan = allocation_to_multiuav_plan(result)
    return {
        "scenario_id": scenario["scenario_id"],
        "title": scenario["title"],
        "description": scenario["description"],
        "result": result,
        "route_plan": route_plan,
    }


def _primary_assignments(result: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [
        item
        for item in result.get("assignments", [])
        if item.get("role") == "PRIMARY" and item.get("groups")
    ]


def summarize_case(case: Mapping[str, Any]) -> Dict[str, Any]:
    result = case["result"]
    primary = _primary_assignments(result)
    primary_aircraft = ";".join(str(item.get("aircraft_id")) for item in primary)
    primary_models = ";".join(str(item.get("aircraft_model")) for item in primary)
    payloads = sorted(
        {
            str(payload_id)
            for item in primary
            for group in item.get("groups", [])
            for payload_id in group.get("payload_ids", [])
        }
    )
    candidate_map = result.get("candidate_summary", {}).get("candidates", {})
    rejected_map = result.get("candidate_summary", {}).get("rejected", {})
    candidate_count = sum(len(values) for values in candidate_map.values())
    rejection_reasons = sorted(
        {
            str(reason)
            for reasons in rejected_map.values()
            for values in reasons.values()
            for reason in values
        }
    )
    solver = result.get("solver", {})
    return {
        "scenario_id": case["scenario_id"],
        "title": case["title"],
        "status": result.get("status"),
        "reason": result.get("reason") or "",
        "primary_aircraft": primary_aircraft,
        "primary_models": primary_models,
        "payloads": ";".join(payloads),
        "assigned_groups": ";".join(solver.get("assigned_group_ids", [])),
        "unassigned_groups": ";".join(solver.get("unassigned_group_ids", [])),
        "candidate_count": candidate_count,
        "rejection_count": sum(
            len(values) for reasons in rejected_map.values() for values in reasons.values()
        ),
        "rejection_reasons": ";".join(rejection_reasons),
        "reserve_count": len(result.get("reserve_aircraft", [])),
        "solver_name": solver.get("solver_name", ""),
        "solver_status": solver.get("solver_status", ""),
        "objective_value": solver.get("objective_value", ""),
        "validation": json.dumps(result.get("validation", {}), ensure_ascii=False),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_summary_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "scenario_id",
        "title",
        "status",
        "reason",
        "primary_aircraft",
        "primary_models",
        "payloads",
        "assigned_groups",
        "unassigned_groups",
        "candidate_count",
        "rejection_count",
        "rejection_reasons",
        "reserve_count",
        "solver_name",
        "solver_status",
        "objective_value",
        "validation",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def _status_color(status: str) -> str:
    return {
        "ALLOCATED": "#2f855a",
        "BLOCKED": "#c05621",
        "INFEASIBLE": "#c53030",
    }.get(status, "#718096")


def render_svg(rows: Sequence[Mapping[str, Any]]) -> str:
    width = 1100
    row_height = 58
    top = 72
    height = top + max(1, len(rows)) * row_height + 54
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Reconnaissance resource allocation validation</title>',
        '<desc id="desc">Scenario status, primary aircraft, payload, and candidate counts.</desc>',
        '<rect width="100%" height="100%" fill="#f7fafc"/>',
        '<text x="32" y="38" font-family="Arial, sans-serif" font-size="22" '
        'font-weight="600" fill="#1a202c">侦察资源分配验证矩阵</text>',
        '<text x="32" y="60" font-family="Arial, sans-serif" font-size="13" fill="#4a5568">'
        '状态、主分配飞机、载荷和候选平台数量</text>',
    ]
    columns = [(32, "场景"), (320, "状态"), (475, "主任务飞机"), (660, "载荷"), (830, "候选数")]
    for x, label in columns:
        parts.append(
            f'<text x="{x}" y="{top - 18}" font-family="Arial, sans-serif" '
            f'font-size="13" font-weight="600" fill="#4a5568">{html.escape(label)}</text>'
        )
    for index, row in enumerate(rows):
        y = top + index * row_height
        if index % 2 == 0:
            parts.append(f'<rect x="20" y="{y - 25}" width="1060" height="48" fill="#edf2f7"/>')
        status = str(row.get("status", ""))
        parts.extend(
            [
                f'<text x="32" y="{y}" font-family="Arial, sans-serif" font-size="14" fill="#1a202c">'
                f'{html.escape(str(row.get("title", "")))}</text>',
                f'<rect x="320" y="{y - 16}" width="120" height="24" rx="4" fill="{_status_color(status)}"/>',
                f'<text x="380" y="{y + 1}" text-anchor="middle" font-family="Arial, sans-serif" '
                f'font-size="12" font-weight="600" fill="#ffffff">{html.escape(status)}</text>',
                f'<text x="475" y="{y}" font-family="Arial, sans-serif" font-size="14" fill="#1a202c">'
                f'{html.escape(str(row.get("primary_aircraft", "—")) or "—")}</text>',
                f'<text x="660" y="{y}" font-family="Arial, sans-serif" font-size="14" fill="#1a202c">'
                f'{html.escape(str(row.get("payloads", "—")) or "—")}</text>',
                f'<text x="830" y="{y}" font-family="Arial, sans-serif" font-size="14" fill="#1a202c">'
                f'{html.escape(str(row.get("candidate_count", "0")))}</text>',
            ]
        )
    parts.append(
        '<text x="32" y="{0}" font-family="Arial, sans-serif" font-size="12" fill="#718096">'
        '结果由前端任务、聚合任务组和舰队快照共同计算</text>'.format(height - 18)
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def render_markdown(rows: Sequence[Mapping[str, Any]], source: str) -> str:
    lines = [
        "# 侦察资源分配验证报告",
        "",
        f"输入：`{source}`",
        "",
        "## 场景结果",
        "",
        "| 场景 | 状态 | 主任务飞机 | 载荷 | 候选数 | 备用飞机 | 求解器 |",
        "|---|---|---|---|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {title} | {status} | {aircraft} | {payload} | {candidates} | {reserve} | {solver} |".format(
                title=row["title"],
                status=row["status"],
                aircraft=row["primary_aircraft"] or "—",
                payload=row["payloads"] or "—",
                candidates=row["candidate_count"],
                reserve=row["reserve_count"],
                solver=row["solver_name"] or "—",
            )
        )
    lines.extend(["", "## 可核验结论", ""])
    for row in rows:
        if row["status"] == "ALLOCATED":
            lines.append(
                f"- {row['title']}：任务组 `{row['assigned_groups'] or '—'}` 分配给 "
                f"`{row['primary_aircraft'] or '—'}`，载荷为 `{row['payloads'] or '—'}`。"
            )
        else:
            reason = row["reason"] or row["rejection_reasons"] or "—"
            lines.append(f"- {row['title']}：结果为 `{row['status']}`，原因是 `{reason}`。")
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- C172 和 SF50 的飞行能力来自飞行手册参数整理后的仿真配置。",
            "- MX-15 和 Lynx 的平台兼容关系属于虚拟载荷配置，用于验证资源匹配逻辑。",
            "- 本报告验证任务组分配和候选筛选，不验证实际航迹、燃油消耗、通信带宽或飞控执行。",
        ]
    )
    return "\n".join(lines) + "\n"


def render_html(rows: Sequence[Mapping[str, Any]], svg: str, source: str) -> str:
    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row['title']))}</td>"
            f"<td class='status status-{html.escape(str(row['status']).lower())}'>"
            f"{html.escape(str(row['status']))}</td>"
            f"<td>{html.escape(str(row['primary_aircraft']) or '—')}</td>"
            f"<td>{html.escape(str(row['payloads']) or '—')}</td>"
            f"<td>{html.escape(str(row['candidate_count']))}</td>"
            f"<td>{html.escape(str(row['reserve_count']))}</td>"
            f"<td>{html.escape(str(row['solver_name']) or '—')}</td>"
            f"<td>{html.escape(str(row['reason'] or row['rejection_reasons'] or '—'))}</td>"
            "</tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>侦察资源分配验证报告</title>
<style>
body {{ margin: 0; padding: 32px; color: #1a202c; background: #ffffff; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
main {{ max-width: 1180px; margin: 0 auto; }}
h1 {{ margin: 0 0 8px; font-size: 26px; }}
p {{ color: #4a5568; }}
.figure {{ overflow-x: auto; margin: 24px 0; }}
svg {{ min-width: 900px; max-width: 100%; height: auto; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 14px; }}
th, td {{ border-bottom: 1px solid #e2e8f0; padding: 10px 8px; text-align: left; vertical-align: top; }}
th {{ color: #4a5568; font-weight: 600; }}
.status {{ font-weight: 600; }}
.status-allocated {{ color: #2f855a; }}
.status-blocked {{ color: #c05621; }}
.status-infeasible {{ color: #c53030; }}
code {{ background: #edf2f7; padding: 2px 4px; border-radius: 3px; }}
.note {{ margin-top: 24px; padding-top: 12px; border-top: 1px solid #e2e8f0; font-size: 13px; }}
</style>
</head>
<body>
<main>
<h1>侦察资源分配验证报告</h1>
<p>输入：<code>{html.escape(source)}</code></p>
<div class="figure">{svg}</div>
<table>
<thead><tr><th>场景</th><th>状态</th><th>主任务飞机</th><th>载荷</th><th>候选数</th><th>备用飞机</th><th>求解器</th><th>原因或排除信息</th></tr></thead>
<tbody>{''.join(table_rows)}</tbody>
</table>
<div class="note">
<div>C172 与 SF50 的飞行能力来自飞行手册参数整理后的仿真配置。</div>
<div>MX-15 与 Lynx 的平台兼容关系属于虚拟载荷配置。</div>
<div>当前验证范围是任务组分配、候选筛选和依赖阻塞，不包含真实航迹、通信带宽和飞控执行。</div>
</div>
</main>
</body>
</html>
"""


def run_validation(request: Mapping[str, Any], output_dir: Path, source: str) -> List[Dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    scenarios_dir = output_dir / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    cases = [run_scenario(scenario) for scenario in build_scenarios(request)]
    rows = [summarize_case(case) for case in cases]
    for case in cases:
        scenario_id = case["scenario_id"]
        _write_json(scenarios_dir / f"{scenario_id}.json", case["result"])
        if case["route_plan"] is not None:
            _write_json(scenarios_dir / f"{scenario_id}_route_plan.json", case["route_plan"])
    _write_json(output_dir / "summary.json", rows)
    write_summary_csv(output_dir / "summary.csv", rows)
    svg = render_svg(rows)
    (output_dir / "allocation_summary.svg").write_text(svg, encoding="utf-8")
    (output_dir / "report.md").write_text(
        render_markdown(rows, source), encoding="utf-8"
    )
    (output_dir / "report.html").write_text(
        render_html(rows, svg, source), encoding="utf-8"
    )
    return rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--request",
        type=Path,
        default=Path("examples/recon_allocation_request.json"),
        help="combined allocation request JSON",
    )
    input_group.add_argument("--planner-output", type=Path)
    parser.add_argument("--aggregation-output", type=Path)
    parser.add_argument("--fleet-snapshot", type=Path)
    parser.add_argument(
        "--completed-task-ids",
        default="",
        help="comma-separated completed task IDs for three-snapshot input",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/recon_validation"),
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    request_path = args.request if args.planner_output is None else None
    request = load_request(
        request_path=request_path,
        planner_path=args.planner_output,
        aggregation_path=args.aggregation_output,
        fleet_path=args.fleet_snapshot,
    )
    if args.completed_task_ids:
        request["completed_task_ids"] = [
            item.strip()
            for item in args.completed_task_ids.split(",")
            if item.strip()
        ]
    rows = run_validation(request, args.output_dir, str(request_path or "three front-end snapshots"))
    print(f"validation_cases={len(rows)}")
    for row in rows:
        primary = row["primary_aircraft"] or "-"
        print(
            f"{row['scenario_id']}: status={row['status']} "
            f"primary={primary} payload={row['payloads'] or '-'}"
        )
    print(f"report={args.output_dir / 'report.html'}")
    print(f"summary={args.output_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
