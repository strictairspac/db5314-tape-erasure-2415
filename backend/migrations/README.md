# 表结构

`src/models/tables.py` 定义关系结构。API 启动时显式创建演示库的表（`schema.create_all`），新增的表会一并创建。

复核、补录与重算涉及的增量表：

- `contract_amendments`：按日期段生效的合同更正，及冲突标记与稳定请求标识。
- `contract_locks`：按合同编号串行化并发更正，先入账者生效。
- `business_run_inputs`：每次执行入队时冻结的目录快照，重算与留存复核都读它。
- `run_recomputations`：源批次到重算批次的映射与请求标识。
- `run_amendments`：重算批次实际应用的更正集合。

此版本仍以 `create_all` 建表；已有部署做正式升级时应单独引入版本化迁移。
