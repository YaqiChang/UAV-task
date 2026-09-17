# 侦察资源分配验证报告

输入：`examples/recon_allocation_request.json`

## 场景结果

| 场景 | 状态 | 主任务飞机 | 载荷 | 候选数 | 备用飞机 | 求解器 |
|---|---|---|---|---:|---:|---|
| VISIBLE + LOITER | ALLOCATED | C172-01 | payload.wescam_mx15 | 5 | 9 | ortools_cp_sat |
| INFRARED + LOITER | ALLOCATED | C172-01 | payload.wescam_mx15 | 5 | 9 | ortools_cp_sat |
| RADAR | ALLOCATED | SF50-01 | payload.lynx_mmr | 5 | 9 | ortools_cp_sat |
| DEPENDENCY BLOCKED | BLOCKED | — | — | 0 | 0 | — |
| RADAR WITHOUT PAYLOAD | INFEASIBLE | — | — | 0 | 0 | — |

## 可核验结论

- VISIBLE + LOITER：任务组 `G01` 分配给 `C172-01`，载荷为 `payload.wescam_mx15`。
- INFRARED + LOITER：任务组 `G01` 分配给 `C172-01`，载荷为 `payload.wescam_mx15`。
- RADAR：任务组 `G01` 分配给 `SF50-01`，载荷为 `payload.lynx_mmr`。
- DEPENDENCY BLOCKED：结果为 `BLOCKED`，原因是 `UNRESOLVED_DEPENDENCY`。
- RADAR WITHOUT PAYLOAD：结果为 `INFEASIBLE`，原因是 `NO_FEASIBLE_AIRCRAFT`。

## 解释边界

- C172 和 SF50 的飞行能力来自飞行手册参数整理后的仿真配置。
- MX-15 和 Lynx 的平台兼容关系属于虚拟载荷配置，用于验证资源匹配逻辑。
- 本报告验证任务组分配和候选筛选，不验证实际航迹、燃油消耗、通信带宽或飞控执行。
