# UAV Task

该仓库是独立的无人机任务聚合与资源分配项目。它承接前端任务规划输出，将任务组分配给 C172 和 SF50 两类飞行平台，并输出供航迹规划模块使用的飞机任务包。

当前实现完成以下功能：

1. 承接前端任务规划输出和任务聚合输出。
2. 将 `VISIBLE`、`INFRARED`、`RADAR` 映射到虚拟载荷能力。
3. 检查任务依赖、高度范围、飞行能力、载荷和剩余任务时长。
4. 使用 OR-Tools CP-SAT 完成任务组与飞机分配。
5. 在没有 OR-Tools 的开发环境中使用确定性回退求解，并在结果中明确标记。
6. 输出主任务飞机和可用备份飞机，不强制占用整个舰队。
7. 通过适配器生成供现有航迹规划模块消费的任务包。

## 1. 代码结构

```text
mission_planner/
├── schemas.py
├── normalize.py
├── capability_model.py
├── dependency_validator.py
├── candidate_filter.py
├── cp_sat_allocator.py
├── mission_allocator.py
├── mission_cli.py
└── multiuav_adapter.py
```

旧版 `AtomicTask`、`MissionPackage`、`UAV` 和原有聚合接口继续保留，用于兼容仓库中的既有示例和测试。

## 2. 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell 使用：

```powershell
.venv\Scripts\Activate.ps1
```

## 3. 运行前端输出到资源分配

```bash
python -m mission_planner.mission_cli \
  --request examples/recon_allocation_request.json \
  --output outputs/recon_allocation.json
```

示例结果中，G01 被分配给一架 C172，其余飞机保留为 `AVAILABLE_RESERVE`。SF50 在该示例中因未配置 `LOITER` 仿真能力而被筛除。

`T00` 通过 `completed_task_ids` 标记为已完成。删除该字段后，G01 将返回 `BLOCKED`，用于验证前端任务依赖没有被聚合过程丢失。

## 4. 输入输出边界

资源分配接口的输入包含三份快照：

1. 前端任务规划输出。
2. 任务聚合输出。
3. 舰队状态和载荷配置。

资源分配接口输出 `assignments` 和 `reserve_aircraft`。每个任务组保存任务顺序、目标区域、载荷标识、飞行能力和时间范围。航迹点生成、飞行控制指令和 X-Plane 执行由下游模块处理。

## 5. 读取 MultiUAV-Plat 实时飞机状态

先启动原有服务端：

```bash
cd /path/to/MultiUAV-Plat/server
python -m pip install -r requirements.txt
python main.py
```

再使用旧版实时状态适配器：

```bash
python -m mission_planner.cli \
  --tasks examples/tasks.json \
  --server http://127.0.0.1:8000 \
  --capability-profiles examples/uav_capabilities.json \
  --output outputs/plan.json
```

`GET /drones` 提供动态平台状态。分配模块需要由能力配置补充 `model`、`flight_capabilities` 和 `installed_payloads`。当前 C172 和 SF50 性能参数来自项目提供的 X-Plane 飞行手册，平台与虚拟载荷的兼容关系属于 `SIMULATION_PROFILE`，没有标记为真实改装结论。

## 6. 测试

```bash
pytest -q
```

在没有安装 OR-Tools 的环境中，旧版分配接口和新接口都使用确定性回退求解。安装 `requirements.txt` 后，新接口使用 CP-SAT。

## 7. 侦察资源分配验证与汇报输出

运行固定场景矩阵：

```bash
bash scripts/run_recon_validation.sh
```

脚本默认读取 `examples/recon_allocation_request.json`，自动运行以下场景：

1. `VISIBLE + LOITER` 基线分配。
2. `INFRARED + LOITER` 红外载荷替换。
3. `RADAR` 雷达载荷匹配。
4. 删除 `T00` 完成标记后的依赖阻塞。
5. 移除所有 Lynx 雷达后的不可行诊断。

输出目录默认为 `outputs/recon_validation/`，包括：

- `summary.csv`：便于导入 Excel 的结果表。
- `summary.json`：机器可读的汇总结果。
- `report.md`：可直接整理到汇报材料中的文字报告。
- `report.html`：离线打开的结果图表和详细表格。
- `allocation_summary.svg`：可插入 PPT 的矢量图。
- `scenarios/*.json`：每个场景的完整分配结果。
- `scenarios/*_route_plan.json`：分配结果传给航迹规划模块的适配结果。

使用自己的三份前端快照：

```bash
bash scripts/run_recon_validation.sh \
  --planner-output path/to/planner_output.json \
  --aggregation-output path/to/aggregation_output.json \
  --fleet-snapshot path/to/fleet_snapshot.json \
  --completed-task-ids T00 \
  --output-dir outputs/my_recon_validation
```

如果已有合并请求文件：

```bash
bash scripts/run_recon_validation.sh \
  --request path/to/recon_allocation_request.json \
  --output-dir outputs/my_recon_validation
```

打开 `outputs/recon_validation/report.html`，查看任务组、主任务飞机、载荷、候选数、备用飞机、求解器和失败原因。

## 8. 当前模型边界

- 一个任务组由一架飞机执行。
- 同一飞机可以接收多个时间上不冲突的任务组。
- 当前资源分配使用任务组的估计工作量，精确转场时间和燃油代价由航迹规划模块回传后加入。
- 通信链路的静态设备知识保留扩展位置，当前示例验证载荷和飞行能力。
- 多机协同执行同一任务、链路带宽竞争和在线重分配属于后续扩展。

## 9. 旧版聚合与分配示例

```bash
python -m mission_planner.cli \
  --tasks examples/tasks.json \
  --uavs examples/uavs.json \
  --output outputs/plan.json
```

Linux 或 macOS 可以使用：

```bash
bash run_demo.sh
```

Windows 可以使用：

```bat
run_demo.bat
```
