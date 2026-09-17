# MultiUAV Task Planner Starter

该目录作为 `zhangsheng93/MultiUAV-Plat` 的独立任务规划扩展，完成以下功能：

1. 将前级任务分解结果读取为结构化原子任务。
2. 对明确标记为可去重的任务执行重复消除。
3. 通过硬约束检查和贪心层次聚合形成任务包。
4. 使用 OR-Tools CP-SAT 完成任务包与无人机分配。
5. 输出供后续航迹规划模块使用的 JSON 执行方案。

## 1. 放入 MultiUAV-Plat

```bash
git clone https://github.com/zhangsheng93/MultiUAV-Plat.git
cd MultiUAV-Plat
git checkout -b feature/task-aggregation

cp -r /path/to/multiuav_task_planner_starter/mission_planner .
cp -r /path/to/multiuav_task_planner_starter/examples .
cp -r /path/to/multiuav_task_planner_starter/tests/test_planner.py tests_task_planner.py
cp /path/to/multiuav_task_planner_starter/requirements.txt requirements_planner.txt
```

也可以直接把整个目录放在仓库旁边独立运行。

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

## 3. 离线运行

```bash
python -m mission_planner.cli   --tasks examples/tasks.json   --uavs examples/uavs.json   --output outputs/plan.json
```

## 4. 读取 MultiUAV-Plat 实时无人机状态

先启动 MultiUAV-Plat server：

```bash
cd /path/to/MultiUAV-Plat/server
python -m pip install -r requirements.txt
python main.py
```

再执行规划：

```bash
python -m mission_planner.cli   --tasks examples/tasks.json   --server http://127.0.0.1:8000   --capability-profiles examples/uav_capabilities.json   --output outputs/plan.json
```

`GET /drones` 提供位置、电量、速度和状态。能力载荷暂由
`uav_capabilities.json` 补充，算法稳定后再扩展服务端 Drone 模型。

## 5. 测试

```bash
pytest -q
```

## 6. 当前模型边界

- 一个任务包由一架无人机执行。
- 同一无人机可以接收多个任务包。
- 包内任务时序由前置依赖保存，详细开始时间和跨包路径由航迹规划模块处理。
- CP-SAT 只处理能力、电量、工作时长和分配代价。
- 多无人机联合执行同一任务、通信拓扑和在线重规划属于后续扩展。


## 7. 一键生成汇报文件

Linux 或 macOS：

```bash
bash run_demo.sh
```

Windows：

```bat
run_demo.bat
```

生成：

```text
outputs/plan.json
outputs/report.html
```

`outputs/sample_report.html` 是结构演示文件，其求解状态标记为 `ILLUSTRATIVE`。
正式汇报应使用实际运行生成的 `outputs/report.html`。
