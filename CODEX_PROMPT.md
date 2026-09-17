在当前仓库中完成一个可演示的多无人机任务聚合与资源分配最小系统。仓库为 MultiUAV-Plat，工作分支使用 feature/task-aggregation。不要重写 server 和 agent4drone 主链，新增独立 mission_planner 包，并复用 MultiUAV-Plat 的 GET /drones 动态状态接口。

目标输出必须包含：
1. outputs/plan.json
2. outputs/report.html
3. pytest 测试结果
4. 终端中打印最终任务包和 UAV 分配摘要

必须执行以下步骤。

第一步，检查环境和仓库。
- 执行 git status
- 检查 python --version
- 检查 server、agent4drone、mission_planner、examples 是否存在
- 若 mission_planner 不存在，使用当前目录中提供的 multiuav_task_planner_demo 内容复制进去
- 安装 requirements.txt 中的 ortools、requests、pytest
- 不修改用户已有未提交文件

第二步，检查并修复以下模块。
- mission_planner/domain.py
- mission_planner/aggregation.py
- mission_planner/allocation.py
- mission_planner/multiuav_adapter.py
- mission_planner/cli.py
- mission_planner/visualize.py

功能要求如下。

原子任务字段：
- id
- action
- region_id
- position
- duration_s
- required_capabilities
- predecessors
- target_id
- earliest_start_s
- latest_finish_s
- priority
- merge_policy

任务聚合要求：
- merge_policy=deduplicate 时才允许重复消除
- 其余任务只能组成任务包，不能丢失
- 每次合并后重新检查空间距离、时间窗口、能力可满足性、能源和任务包大小
- 输出 aggregation_trace
- 不使用 DBSCAN
- 示例应将重复搜索任务消除，并将 search 与 track 形成任务包
- relay 任务由于能力约束分配给具备 relay 能力的无人机

资源分配要求：
- 使用 OR-Tools CP-SAT
- 每个任务包恰好分配一次
- UAV 必须满足任务包全部能力
- 电量、能源预算、保留能源、最大工作时间必须满足
- 优化距离、估计能源、低电量惩罚和优先级
- 无解时输出明确诊断，列出每个任务包缺失的能力和可行 UAV 数量

可视化要求：
- report.html 为自包含 HTML，不加载外部 CDN
- 页面包含原子任务数、任务包数、无人机数、求解状态
- 展示 原子任务输入 → 约束任务聚合 → CP-SAT 分配 的流程
- 展示每架 UAV 的任务包和任务序列
- 展示任务包明细和 aggregation_trace
- HTML 在 Chrome 中直接打开可用

第三步，运行测试和演示。
执行：
python -m pytest -q
python -m mission_planner.cli --tasks examples/tasks.json --uavs examples/uavs.json --output outputs/plan.json
python -m mission_planner.visualize --plan outputs/plan.json --output outputs/report.html

第四步，验证输出。
- 解析 outputs/plan.json，确认所有输入任务均出现在任务包中，重复消除任务除外但必须在 aggregation_trace 中保留来源 ID
- 确认每个任务包只分配一次
- 确认每个分配 UAV 满足 required_capabilities
- 确认 outputs/report.html 存在且大小大于 5 KB
- 打印任务包数量、每个 UAV 的任务序列、solver_status、objective_value

第五步，提交最终说明。
仅报告：
- 新增和修改的文件
- 实际执行的命令
- pytest 结果
- plan.json 关键结果
- report.html 路径
- 尚未完成的限制

限制：
- 不调用 LLM 生成任务
- 不启动 XPlane
- 不修改 agent4drone/uav_agent.py
- 不实现在线重规划
- 不编造运行成功，所有结论必须来自真实命令输出
