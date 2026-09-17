
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _task_label(package: Dict[str, Any]) -> str:
    actions = html.escape(" → ".join(map(str, package.get("actions", []))))
    task_ids = html.escape(", ".join(map(str, package.get("task_ids", []))))
    return f"{actions}<br><span class='muted'>{task_ids}</span>"


def build_html(plan: Dict[str, Any]) -> str:
    packages = plan.get("packages", [])
    assignments = plan.get("assignments", [])
    trace = plan.get("aggregation_trace", [])
    solver = plan.get("solver", {})

    assigned_by_package = {}
    for uav in assignments:
        for package in uav.get("packages", []):
            assigned_by_package[package["package_id"]] = uav["uav_id"]

    package_rows = []
    task_rows = []
    for package in packages:
        package_rows.append(
            f"""
            <tr>
              <td>{html.escape(str(package.get("id", "")))}</td>
              <td>{_task_label(package)}</td>
              <td>{html.escape(", ".join(package.get("required_capabilities", [])))}</td>
              <td>{package.get("duration_s", 0)} s</td>
              <td>{html.escape(str(assigned_by_package.get(package.get("id"), "未分配")))}</td>
            </tr>
            """
        )
        for task in package.get("tasks", []):
            position = task.get("position", [0, 0])
            window = (
                f"[{task.get('earliest_start_s', 0)}, "
                f"{task.get('latest_finish_s', 0)}]"
            )
            task_rows.append(
                f"""
                <tr>
                  <td>{html.escape(str(task.get("id", "")))}</td>
                  <td>{html.escape(str(package.get("id", "")))}</td>
                  <td>{html.escape(str(task.get("action", "")))}</td>
                  <td>{html.escape(str(task.get("region_id", "")))}</td>
                  <td>{html.escape(str(position))}</td>
                  <td>{task.get("duration_s", 0)} s</td>
                  <td>{html.escape(", ".join(task.get("required_capabilities", [])))}</td>
                  <td>{html.escape(", ".join(task.get("predecessors", [])) or "-")}</td>
                  <td>{html.escape(str(task.get("target_id") or "-"))}</td>
                  <td>{html.escape(window)}</td>
                  <td>{task.get("priority", 0)}</td>
                  <td>{html.escape(str(task.get("merge_policy", "")))}</td>
                </tr>
                """
            )

    uav_cards = []
    for uav in assignments:
        package_items = []
        for package in uav.get("packages", []):
            package_items.append(
                f"""
                <div class="package">
                  <div class="package-title">{html.escape(package.get("package_id", ""))}</div>
                  <div>{html.escape(" → ".join(package.get("actions", [])))}</div>
                  <div class="muted">{html.escape(", ".join(package.get("task_ids", [])))}</div>
                </div>
                """
            )
        if not package_items:
            package_items.append("<div class='muted'>无任务</div>")
        uav_cards.append(
            f"""
            <section class="uav-card">
              <div class="uav-title">{html.escape(uav.get("uav_id", ""))}</div>
              <div class="muted">任务序列：{html.escape(" → ".join(uav.get("task_sequence", [])) or "无")}</div>
              <div class="muted">服务 {uav.get("total_service_time_s", 0)} s；工作 {uav.get("total_work_time_s", 0)} s；能源 {uav.get("total_energy", 0)} / {uav.get("usable_energy", 0)}</div>
              {''.join(package_items)}
            </section>
            """
        )

    trace_rows = []
    for item in trace:
        if item.get("operation") == "deduplicate":
            detail = (
                f"{', '.join(item.get('source_task_ids', []))} → "
                f"{item.get('result_task_id', '')}"
            )
        else:
            detail = (
                f"{', '.join(item.get('source_task_ids', []))} → "
                f"{item.get('result_package_id', '')}, "
                f"score={item.get('score', '')}"
            )
        trace_rows.append(
            f"<li><strong>{html.escape(item.get('operation', ''))}</strong> "
            f"{html.escape(detail)}</li>"
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Multi-UAV Task Aggregation Report</title>
<style>
:root {{
  --bg: #f7f8fa;
  --card: #ffffff;
  --text: #172033;
  --muted: #667085;
  --line: #d8dee8;
  --accent: #356ae6;
  --soft: #edf3ff;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: Arial, "Microsoft YaHei", sans-serif;
  background: var(--bg);
  color: var(--text);
}}
main {{
  max-width: 1180px;
  margin: 0 auto;
  padding: 28px;
}}
h1 {{ margin: 0 0 8px; font-size: 28px; }}
h2 {{ margin: 0 0 16px; font-size: 20px; }}
.muted {{ color: var(--muted); font-size: 13px; }}
.metrics {{
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  margin: 22px 0;
}}
.metric {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
}}
.metric-value {{ font-size: 26px; font-weight: 700; margin-top: 8px; }}
.section {{
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 20px;
  margin-top: 16px;
}}
.flow {{
  display: grid;
  grid-template-columns: 1fr 80px 1fr 80px 1fr;
  align-items: center;
  gap: 10px;
}}
.node {{
  min-height: 110px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
  background: #fff;
}}
.node strong {{ display: block; margin-bottom: 8px; }}
.arrow {{
  text-align: center;
  color: var(--accent);
  font-size: 28px;
}}
.uav-grid {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}}
.uav-card {{
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 16px;
}}
.uav-title {{ font-size: 18px; font-weight: 700; margin-bottom: 4px; }}
.package {{
  margin-top: 12px;
  border-left: 4px solid var(--accent);
  background: var(--soft);
  padding: 10px 12px;
  border-radius: 8px;
}}
.package-title {{ font-weight: 700; margin-bottom: 4px; }}
table {{
  width: 100%;
  border-collapse: collapse;
}}
.table-scroll {{ overflow-x: auto; }}
th, td {{
  border-bottom: 1px solid var(--line);
  padding: 12px 10px;
  text-align: left;
  vertical-align: top;
}}
th {{ color: var(--muted); font-size: 13px; }}
ul {{ margin: 0; padding-left: 20px; }}
li {{ margin: 8px 0; }}
.footer {{
  margin-top: 18px;
  color: var(--muted);
  font-size: 12px;
}}
@media (max-width: 800px) {{
  .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .flow {{ grid-template-columns: 1fr; }}
  .arrow {{ transform: rotate(90deg); }}
  .uav-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main>
  <h1>多无人机任务聚合与资源分配结果</h1>
  <div class="muted">离线任务规划演示报告</div>

  <div class="metrics">
    <div class="metric">
      <div class="muted">原子任务数</div>
      <div class="metric-value">{plan.get("input_task_count", 0)}</div>
    </div>
    <div class="metric">
      <div class="muted">任务包数</div>
      <div class="metric-value">{plan.get("package_count", 0)}</div>
    </div>
    <div class="metric">
      <div class="muted">无人机数</div>
      <div class="metric-value">{plan.get("uav_count", len(assignments))}</div>
    </div>
    <div class="metric">
      <div class="muted">求解状态</div>
      <div class="metric-value">{html.escape(str(solver.get("solver_status", "N/A")))}</div>
    </div>
  </div>

  <section class="section">
    <h2>算法链路</h2>
    <div class="flow">
      <div class="node">
        <strong>原子任务输入</strong>
        <div class="muted">动作、区域、目标、时间窗、能力需求、前置依赖</div>
      </div>
      <div class="arrow">→</div>
      <div class="node">
        <strong>约束任务聚合</strong>
        <div class="muted">受控重复消除、全包空间距离、时间窗、单机能力、能源与包大小复检</div>
      </div>
      <div class="arrow">→</div>
      <div class="node">
        <strong>CP-SAT 资源分配</strong>
        <div class="muted">恰好一次分配、能力、电量、累计能源、保留能源、工作时长与优先级</div>
      </div>
    </div>
  </section>

  <section class="section">
    <h2>无人机执行方案</h2>
    <div class="uav-grid">
      {''.join(uav_cards)}
    </div>
  </section>

  <section class="section">
    <h2>任务包明细</h2>
    <div class="table-scroll"><table>
      <thead>
        <tr>
          <th>任务包</th>
          <th>任务序列</th>
          <th>能力需求</th>
          <th>持续时间</th>
          <th>分配无人机</th>
        </tr>
      </thead>
      <tbody>
        {''.join(package_rows)}
      </tbody>
    </table></div>
  </section>

  <section class="section">
    <h2>原子任务明细（去重后）</h2>
    <div class="table-scroll"><table>
      <thead>
        <tr>
          <th>ID</th><th>任务包</th><th>动作</th><th>区域</th><th>坐标</th>
          <th>持续时间</th><th>能力</th><th>前置任务</th><th>目标</th>
          <th>时间窗</th><th>优先级</th><th>合并策略</th>
        </tr>
      </thead>
      <tbody>{''.join(task_rows)}</tbody>
    </table></div>
  </section>

  <section class="section">
    <h2>聚合过程</h2>
    <ul>
      {''.join(trace_rows) if trace_rows else "<li>本次未发生任务合并</li>"}
    </ul>
  </section>

  <div class="footer">
    Objective value: {solver.get("objective_value", "N/A")}；
    Wall time: {solver.get("wall_time_s", "N/A")} s
  </div>
</main>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan = _load_json(args.plan)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(plan), encoding="utf-8")
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
