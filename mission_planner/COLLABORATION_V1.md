# 任务聚合与逐机资源分配协作接口 v1

## 调用和文件范围

Python 进程内调用：

```python
from mission_planner import aggregate_and_allocate

result = aggregate_and_allocate(request)
```

仅需将整个 `mission_planner` 目录置于调用方 Python 搜索路径的父目录。安装依赖：

```bash
python -m pip install -r mission_planner/requirements-collaboration.txt
```

输入模型见 `request_v1.schema.json`，输出模型见 `response_v1.schema.json`。完整三机输入见 `examples/collaboration_three_aircraft.json`。使用下列命令检查：

```bash
python -m pytest mission_planner/test_collaboration_v1.py -q
```

接口不会修改传入对象，不会连接 HTTP、X-Plane 或修改执行状态。它只接收 `OBSERVE`、`SEARCH`、`TRACK`、`RELAY`、`SURVEY` 业务任务。输出的 `assignments` 使用飞机实例 ID 和任务序列，由调用方生成人工确认和逐机航迹规划请求。

## 约束语义

- `requested_at` 必须为 UTC。`time_window.start_sec/end_sec` 和输出的 `scheduled_start_sec/scheduled_end_sec` 均以该时刻为零点。
- 输入经纬度为 WGS84 度，高度为 MSL 米。模块用 `map_reference_position` 建立局部水平 ENU；MSL 高度仅用于任务约束和升限检查。模块不生成航点、不绕障、不计返航航迹。
- 任务载荷使用 `payload.stormcaster_e`、`payload.wescam_mx15` 和 `payload.lynx_mmr`。输入允许 `VISIBLE`、`INFRARED`、`RADAR`；输出统一为正式载荷 ID。`IR_THERMAL` 等传感器能力由运行可用载荷提供，`LOITER` 等飞行能力由飞机快照提供。
- `merge_policy=DEDUPLICATE` 只消除等价重复任务，`BUNDLE_IF_COMPATIBLE` 将任务合为一组并保留全部原任务 ID，`NEVER` 禁止两种操作。去重映射保留在 `aggregation_trace`。
- 总工作时间为首任务区转场时间、组间转场时间、组内转场时间和任务执行时间之和。必须同时满足 `max_work_sec` 与 `remaining_endurance_sec` 扣除 `endurance_reserve_ratio` 后的上限；`estimated_remaining_endurance_sec` 从原始剩余续航减去估计耗时。
- 同一区域且任务类型兼容时才考虑聚合。聚合后重新检查跨度、任务上限、时间窗、高度和至少一架飞机的基本可行性。任务组中的任务以依赖拓扑顺序串行执行。
- CP-SAT 对每架飞机建立从当前位置出发的任务组序列，同时约束转场、时间窗、依赖、最大工作时间和剩余续航。缺少 OR-Tools 时使用确定性贪心算法，并报告 `optimal=false`。超时得到 `FEASIBLE` 时也报告 `optimal=false`。
- 所有任务均视为必须任务。当前 v1 没有可选任务输入字段，因此不生成 `PARTIALLY_ALLOCATED`。任一任务无法分配时返回 `INFEASIBLE` 和完整未分配任务列表；依赖缺失或成环时返回 `BLOCKED`。

## 改动记录

- 增加单次 Python 调用的输入、聚合、候选筛选、调度及结果校验。
- 使用正式载荷 ID，并从飞机快照读取 SF50 盘旋能力。
- 增加 WGS84 局部距离、逐机转场、可用载荷和安全余量检查。
- 增加 CP-SAT 与贪心模式的三机和异常验收测试。

## 许可证与集成

本目录随原仓库的 GPL-3.0 许可证提供，文本见 `LICENSE`。将代码复制到另一个项目之前，集成方应核对原仓库各部分的来源与授权；此文件不授予额外许可证。核心算法不依赖 `server`、`agent4drone`、前端或 X-Plane。
