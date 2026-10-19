# Repository Map — Research V2

> 本文件是 Research V2 的代码与文件架构导航映射。记录了模块边界、接口合同以及对 V1 历史模块的复用与隔离原则。
> 后续阶段的 Agent 应当直接查阅本映射，禁止重新进行全仓库扫描。

## 1. 核心边界与基线状态

- **Repository Root**: `/Users/zhangzeyuan/Projects/quant-ETF`
- **Baseline Expected HEAD**: `3f708f888277a6564ea6db204dafde95c2a548c7`
- **Research V1 Tag**: `research-v1` -> `d35840148354105cb4d719ba828e0ff7b4a44782`
- **Paper Tag**: `paper-v1-ready` -> `34039df69c16b2c63439556b5762c06b50785384`
- **Baseline Manifest Hash**: `e7d83f6e3fdc5e8a56115d1960bc6d1a429beb40db79e55788ffe8f3bad6c217`
- **Protocol SHA-256**: `a35fe1e50f90b537bd543e9d41a78eff3d9ae9991c362adc603f42432a23e9fb`
- **Execution Plan SHA-256**: `5d8dc33318214cd0493ad320ecc122e46b057ab74b9c1b54a80a0b1f0b9cbeea`

---

## 2. 模块复用与隔离矩阵 (严禁直接导入)

| 资产 / 历史模块 | 路径 | 复用方式 | V2 决策与隔离原因 |
|---|---|---|---|
| 历史数据缓存 | `data/cache/*.csv` | 只读文件哈希核对与字节复制 | 仅作只读历史输入。不得调用原 fetcher 刷新，不得原处修改 |
| 历史结果 | `results/**` | 只读历史证据 | 作为已归档的 V1 结果，不可被覆写或作为 V2 目标路径 |
| 复权公式 | `src/data/fetcher.py:calculate_adjusted_ohlc` | 公式算法复用，严禁代码导入 | 旧模块导入会加载 config/logger 并存在静默 fallback；V2 在 `research_v2/data.py` 独立实现 |
| 校验规则 | `src/data/validator.py` | 验证意图与测试场景复用 | 旧模块 inner join 吞掉缺口；V2 实施严格 session 检查，缺日 fail-closed |
| 信号定义 | `src/signals/momentum.py`, `trend.py` | 数学公式参照 | V2 引入周级别自然月跳周定义与独立周 SMA，不改动旧 defaults |
| 资产选择器 | `src/signals/selector.py` | 资产满配逻辑复用 | V2 采用独立纯函数与确定性 tie-break，不触发日志 I/O |
| 回测撮合引擎 | `src/backtest/engine.py` | 仅供逻辑对比 | 旧实现预留费率/负现金截断处理；V2 采用严格自融资二分求解 ledger |
| 绩效度量 | `src/backtest/metrics.py` | 度量公式比对 | V2 统一首日收益计入、年化日历天数及 drawdown 计算，独立实现 |
| 交易日历 | `src/execution/trading_calendar.py` | 底层 `exchange_calendars` 复用 | V2 直接基于 `exchange_calendars.get_calendar("XNYS")`，无 broker 依赖 |
| 隔离 V1 Oracle | `research_v1` tag | 子进程隔离执行 | 仅在 Stage 5 用于重放对比，V2 主进程严禁 `import src.*` |

---

## 3. V2 目录结构与接口映射

```text
research_v2/
  ├── __init__.py           # 无副作用包声明
  ├── .gitignore           # 忽略 .venv, data, artifacts, __pycache__
  ├── requirements.lock    # 精确依赖版本锁定
  ├── protocol.json        # 预注册研究协议规范 (已锁定)
  ├── cli.py               # 统一命令行入口 (baseline-init, protocol-lock, guard, environment)
  ├── contracts.py         # 核心数据结构与接口契约
  ├── guards.py            # F0 边界保护、路径白名单、沙盒篡改检测
  ├── provenance.py        # Canonical JSON、SHA-256 审计链、TrialRegistry
  ├── data.py              # (Stage 2) 离线快照加载与 session 校验
  ├── calendar.py          # (Stage 2) XNYS 日历与交易周期划分
  └── splits.py            # (Stage 2) 数据分区权限控制与 Walk-Forward 窗口

tests_v2/
  ├── conftest.py          # 网络阻断 fixture 与共享路径
  ├── test_guards.py       # 保护契约、路径逃逸与沙盒篡改测试
  └── test_protocol.py     # 协议规范与哈希锁定测试

docs/research_v2/
  ├── REPO_MAP.md          # 本文件
  ├── STATUS.md            # 各阶段执行状态跟踪
  ├── RESEARCH_PROTOCOL.md # 人读版完整研究协议
  ├── baseline_manifest.json # F0 受保护路径只读快照
  ├── protocol_lock.json   # 协议锁定凭据
  └── phase_01.md          # Stage 1 验收报告
```

---

## 4. 接口契约定义 (`research_v2/contracts.py`)

- `DatasetManifest`: 数据快照唯一标识、SHA-256、分区元数据。
- `DatasetView`: 依据当前 `as_of` 和指定 `partition` 过滤的视图，阻断未来数据泄露。
- `PeriodEvent`: 包含 `signal_session`, `signal_close_ts`, `execution_session`, `execution_open_ts`。
- `PeriodTable`: 完整交易周期表格。
- `SignalFrame`: 包含特征截断点、分子、分母与趋势状态的信号跟踪帧。
- `RunSpec`: 运行规范（模型参数、角色、分区、成本、随机种子、所有关联组件哈希）。
- `RunResult`: 运行产物（NAV 序列、成交记录、指标度量字典）。
