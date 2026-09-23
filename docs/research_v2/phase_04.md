# Research V2 — Stage 4: Self-Financing Ledger, Daily MTM & Metrics

> 阶段名称: Stage 4 — Self-Financing Ledger / Daily MTM / Transaction Costs / Metrics
> 完成时间: 2026-09-23
> 阶段状态: **STAGE_4 = PASS**

---

## 1. 目标与执行范围

Stage 4 建立了 Research V2 独立且自洽的每日逐日盯市 (Daily Mark-to-Market) 会计账本与风险收益度量层。
严格执行以下边界与原则：
- **自融资守恒**：求解非线性自融资平衡方程 $V_{post} + c \sum |w_i V_{post} - a_i| = V_{pre}$，残差严格 $\le 10^{-10} \times \max(1, V_{pre})$；
- **真实时序流转**：昨日收盘持仓 $\rightarrow$ 隔夜跳空至今日 execution open $\rightarrow$ 按目标权重调仓并扣除交易成本 $\rightarrow$ 日内持有至今日收盘 MTM 估值；
- **首日收益计入**：初始 \$100,000 全现金，首日调仓成本直接反映在首日收益率中，不使用 `dropna()` 丢失首日；
- **非调仓日开盘价解耦**：非调仓日不依赖 `adj_open`，持仓与现金严格保持不变，仅以 `adj_close` 进行 MTM 估值；
- **换手率双口径严格分离**：
  - 目标单边换手率（含 CASH）：$0.5 \sum_{k} |w^{target}_k - w^{pretrade}_k|$；
  - 风险资产实际成交额比率：$\sum |trade_i| / V_{pre}$；
- **度量规范**：CAGR 基于 timezone-aware 起止时间戳的 `total_seconds() / (365.25 * 86400)`，杜绝整数 `.days` 截断；Sharpe 基于日收益率样本标准差 ($ddof=1, \sqrt{252}$)，零波动时返回 `None`；
- **严格隔离**：严禁访问 Validation 与 Holdout，仅运行一个固定 Train canary (`W_M12_T40`, 5 bps)，不比较模型，不优化调参。

---

## 2. 核心数学模型与求解器 (`research_v2/ledger.py`)

### 2.1 自融资平衡方程
在任何调仓日执行开盘：
$$V_{post} + c \sum_{i \in \text{ETFs}} |w_i V_{post} - a_i| = V_{pre}$$
- 费率定义域：$0 \le c < 1$。负费率、$\ge 1$、NaN、inf 严格 FAIL CLOSED；
- 单调性与有界性：导数 $F'(V) \ge 1 - c > 0$，函数严格单调递增，根严格落入 $[0, V_{pre}]$；
- 求解算法：确定性有界 Brent / Bisection 算法；
- 实际观测最大残差：$2.91 \times 10^{-11}$（远低于容差 $10^{-10} \times \max(1, V_{pre})$）；
- 严禁人工 cash clamp，真实负现金严格 fail closed。

---

## 3. 手算与合成测试 (`tests_v2/`)

所有合成与时序测试均已加入自动化回归套件并通过：

| 测试项 | 描述 | 验证结果 |
|---|---|---|
| **Test A** | All Cash $\rightarrow$ 100% A | ✅ PASS (turnover=1, gross ratio $\approx 1$) |
| **Test B** | 100% A $\rightarrow$ Cash | ✅ PASS (turnover=1, gross ratio $\approx 1$) |
| **Test C** | 100% A $\rightarrow$ 100% B | ✅ PASS (turnover=1, gross ratio $\approx 2$, 双向扣费) |
| **Test D** | 三资产漂移再平衡 | ✅ PASS (精确等权，残差守恒) |
| **Test E** | 零成本场景 ($c=0$) | ✅ PASS ($V_{post}=V_{pre}$, $fee=0$, residual=0) |
| **Test F** | 零交易场景 ($w_{target} == w_{pre}$) | ✅ PASS (0 成交, 0 费用, 0 换手) |
| **Overnight Gap** | 隔夜价格跳空 | ✅ PASS (先计入 $V_{pre}$，旧组合不漏享隔夜 PnL) |
| **Intraday Crash** | 开盘买入当日日内暴跌 | ✅ PASS (当日收盘 NAV 与 MaxDD 立即反映) |
| **First-Day Cost** | 首日建仓扣费 | ✅ PASS (首日收益负值精确反映成本) |
| **Open Decoupling** | 非调仓日开盘价解耦 | ✅ PASS (非调仓日缺 open 正常 MTM；调仓日缺 open 则 FAIL CLOSED) |
| **Elapsed Time** | 精确秒数年化 | ✅ PASS (分数天不被整数 `.days` 截断) |
| **Multi-Cost** | 0/5/10/20/30/50 bps 单调性 | ✅ PASS (费用随费率严格递增，净值非递增) |

---

## 4. Train Canary 实证运行结果

命令：
```bash
python -m research_v2.cli canary --partition train --model W_M12_T40 --cost-bps 5 --run-id phase_04
python -m research_v2.cli audit-ledger --run-id phase_04
```

| 指标 | 结果 |
|---|---|
| 模型 | `W_M12_T40` |
| 分区 | `train` (2006-02-01 至 2015-12-31) |
| 交易日天数 | 2,497 天 |
| 调仓事件数 | 517 次 |
| 待执行跨期事件 | 1 次 (2015-12-31 信号，execution 在 2016-01-04，在 Train 内保持 PENDING) |
| 成交记录数 | 1,653 笔 |
| 初始资金 | \$100,000.00 |
| 最终资产净值 | \$223,818.83 |
| 累计收益率 | +123.82% |
| 年化复合增长率 (CAGR) | 8.47% |
| 日线年化波动率 | 14.78% |
| 日线夏普比率 ($R_f=0$) | 0.624 |
| 日线最大回撤 | 20.76% |
| 累计交易成本 | \$8,919.92 |
| 年化单边换手率 (含 CASH) | 5.79x |
| 年化实际成交名义额比率 | 11.48x |
| 平均现金占比 | 11.23% |
| 最大现金占比 | 100.00% |
| 账本审计状态 | **PASS** (0 issues, max residual $2.91 \times 10^{-11}$) |

---

## 5. 产物与测试全景

### 产物清单 (`research_v2/artifacts/phase_04/`)
- `daily_nav.csv` (2,497 行日度净值、收益、现金及暴露)
- `positions.csv` (每日持仓资产与市值)
- `trades.csv` (1,653 笔成交名义额、数量与逐笔手续费)
- `rebalance.csv` (517 次调仓事件 $V_{pre}$, $V_{post}$, 费用与双口径换手率)
- `pending_events.csv` (1 笔待执行跨段调度事件元数据与状态码)
- `metrics.json` (结构化性能与风险指标)
- `ledger_audit.json` (账本不变量审计凭证)
- `phase_receipt.json` (机器可读阶段收据，SHA256: `4c3430c96ce63071c76f207975e6d806c2c1601e81cbc91ac8b5d42d4c57d335`)

### Pytest 结果
```
177 passed, 1 warning
```
- 原有测试：150 PASS
- Stage 4 新增测试：27 PASS
- 合计：**177 PASS, 0 FAIL, 0 SKIPPED**

### 生产状态与历史标签不变性
- `logs/app.log`: 完全不变 (SHA256: `015a85822e8d7951034d86aad70188f76d463e7665524964c042a87a126c3393`)
- `logs/rebalance_state.json`: 完全不变 (SHA256: `59a9e800d56c6bbb8d486033d33aa5a10bc868115643982501154b0c79caacd4`)
- Tags: `research-v1`, `paper-v1-ready`, `research-v2-stage3-ready` 绝对未动。
- Holdout: `NOT_ACCESSED`。
