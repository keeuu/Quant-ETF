# CODEX EXECUTION PLAN — Research V2 Weekly Momentum

> 本文件是实施合同，不是已经完成的实现或回测结论。编写日期：2026-09-21。
> 本次只新增本文件；后续执行时按阶段实现。Research V1 与 Paper Trading 均保持冻结。
> 默认采用本文明确给出的研究定义；不要根据回测收益重新解释参数或扩大搜索空间。

## 0. 执行摘要与已核实的仓库事实

研究问题：在固定八只 ETF、Long Only、No Leverage、No Short、Top 3 Equal Weight 的条件下，提高调仓频率是否能在成本、换手和噪音以后改善风险调整收益？允许得到“不改善”或“证据不足”的结论。

| 项目 | 本次只读核实结果 |
|---|---|
| 仓库 | `/Users/zhangzeyuan/Projects/quant-ETF` |
| Research V1 tag | `research-v1` → `d35840148354105cb4d719ba828e0ff7b4a44782` |
| 当前 HEAD / Paper 基线 | `3f708f888277a6564ea6db204dafde95c2a548c7`，提交标题 `Finalize safe paper execution engine` |
| 另一已有 tag | `paper-v1-ready`；阶段 1 再解析并记录，禁止移动 |
| 开始检查时工作区 | `git status --short` 为空 |
| 实际 V1 策略 | Monthly 12M-1M momentum、10M SMA、最多 3 只、入选资产之间等权；不足 3 只时不保留空槽现金 |
| 当前成本 / 现金假设 | 每笔买卖名义额 5 bps；现金收益 0 |
| 本地缓存 | `data/cache/{SPY,QQQ,IWM,EFA,EEM,GLD,TLT,IEF}.csv`；每只 5,462 行，2005-01-03 至 2026-09-18 |
| 已有结果覆盖 | `results/metrics.csv`：2006-02-01 至 2026-09-18；补充结果从 2015-01-02 开始 |
| 当前环境 | `.venv/bin/python`：Python 3.12.1；已安装 pandas、numpy、pytest、exchange-calendars；不假定 scipy / arch / statsmodels 已安装 |

检查了代码、文件结构、缓存日期和结果的日期元数据；未重跑 V1/Paper，未检验当前全部数据质量，也未在本次对九个模型计算收益。

**两个必须先解决的研究问题：**

1. V2 不仅调仓频率不同，skip 从 1M 变为 1W，trend 从 10M 变为周窗口。直接比较 V2 和 V1，不能将差异全部归因于 frequency。九个 Weekly 模型各配一个相同信号定义的 Monthly cadence control；这些是固定对照，不是额外可选策略。
2. V1 已有结果覆盖到 2026-09-18。现有历史尾段不能宣称是研究者完全未见的 Final OOS。本方案同时实现历史留出测试与真正前瞻 Final OOS；尚无未来数据时，后者明确为 `PENDING_DATA`，不得伪造完成。

## 1. 全局边界、阅读预算与复用矩阵

### 1.1 冻结合同 F0（每阶段均适用）

**F0 = 本次实施开始之前的所有已有项目文件均不可修改、删除、重命名、覆盖。** 特别包括：

- `src/**`、`config/**`、`scripts/**`、`tests/**`；包括所有 `__init__.py`。
- `results/**`、`data/cache/**`、`logs/**`（含 Paper state、lock、订单与对账文件）。
- `requirements.txt`、`.gitignore`、`implementation_plan.md`、`.env*`、原 `.venv/**`、`.agents/**`、`.codex/**`。
- `research-v1`、`paper-v1-ready` 引用；禁止 reset、clean、force push、覆盖已有 tag。
- `CODEX_EXECUTION_PLAN.md` 在实施开始后作为合同冻结；如确有定义错误，新增变更说明，不静默改历史合同。

V1 tag 和当前 HEAD **本来就存在合法差异**：Paper 新增模块，以及 `config/config.py`、`requirements.txt` 的 Paper 配置/依赖变化。不要把当前配置恢复到 tag；保护当前 HEAD 的文件字节，同时保护 tag 指向与 tag 中的对象。验收不是要求 `git diff research-v1` 为空。

新增范围只允许：`research_v2/**`、`tests_v2/**`、`docs/research_v2/**`。数据和运行产物都在 V2 自己的目录内；不借用原缓存、日志或结果目录写入。禁止 V2 导入 broker、execution、旧 config/logger 或旧运行脚本，禁止发单、调用 Paper CLI、读取凭证、安装依赖到原环境。网络仅用于显式依赖安装或将来独立数据获取，正式研究运行离线。

原目录仅可读取；执行已有测试/基线回测必须在隔离副本中，因为旧模块 import 就可能创建目录或写日志。不得直接在主仓库运行 `pytest tests`、`scripts/run_backtest.py`、`scripts/run_paper.py`。

### 1.2 固定阅读路线，禁止重复全仓库探索

每个独立 Codex 任务只读：本文 → `docs/research_v2/STATUS.md` → 本阶段 Files to inspect → 上一阶段 `phase_receipt.json`。已核实的模块定位如下；除非出现具体依赖错误，不得再次全仓库扫描、遍历全部历史结果或重读全部 Paper 模块。

阶段 1 生成 `REPO_MAP.md`，记录文件、函数、调用边界、基线哈希；后续据此导航。若映射不符，只检查差异涉及的路径，并记录原因。每阶段结束写交接摘要：已实现接口、验证命令、证据路径、未解决项和下一阶段读取清单。无需把整个仓库复制进提示词。

### 1.3 明确复用与新增

| 已有模块 / 资产 | 复用方式 | V2 决定与原因 |
|---|---|---|
| `data/cache/*.csv` | 只读字节复制与哈希核对 | 可复用历史输入；不得调用旧 fetcher 刷新，也不得把未声明的 Yahoo 历史修订称为 point-in-time 数据 |
| `results/*.csv`、`results/2015_onwards/*` | 冻结历史证据 | 只展示为 archived V1；可比指标必须在共同日期、统一度量下另算 |
| `src/data/fetcher.py:calculate_adjusted_ohlc` | 复用公式，不直接导入 | 新增 `research_v2/data.py`；旧模块会加载 config/logger，且缺 adj_close 时有 fallback，V2 禁止这一静默 fallback |
| `src/data/validator.py` | 复用验证意图与测试场景 | 新增严格 session 检查；旧 align 记录缺口后仍取交集，V2 不允许由 inner join 吞掉数据缺口 |
| `src/data/fetcher.py:get_monthly_dataset/build_rebalance_schedule` | 只作 V1 语义参照 | 新增 exchange-session 驱动的 weekly/monthly period builder，不能改 `M` 为 `W-FRI` 就复用 |
| `src/signals/momentum.py`、`trend.py` | V1 公式作为固定比较基线 | 新增 V2 周信号；不修改旧 defaults，不泛化旧函数 |
| `src/signals/selector.py` | 复用不足 3 只仍满配入选资产的规则 | 新增纯函数，固定 tie-break；旧模块间接写日志，不直接导入 |
| `src/backtest/engine.py` | 参考事件与每日估值思路 | 新增自融资 ledger。旧实现先预留固定费率再扣实际成本、负现金可能被截为 0；不得照抄此会计处理 |
| `src/backtest/metrics.py` | 复用数学定义、独立测试比对 | 虽接近纯函数，V2 仍独立度量以明确首日收益/年化口径；不增加生产路径对 `src` 的依赖 |
| `src/execution/trading_calendar.py` | 复用底层第三方 `exchange_calendars` | 新增无 logger/broker 依赖的 XNYS wrapper；旧 wrapper 不直接导入 |
| 现有 `tests/*.py` | 冻结的回归输入和场景参考 | 只在隔离副本运行，新增测试放 `tests_v2/` |
| Paper 模块、旧 plotter/logger/CLI | 不复用运行入口 | V2 没有 broker、订单或 Paper 状态接入点 |

结论：**复用冻结输入、策略约定、数学公式、测试案例和已有第三方库；新增独立研究运行链路。** 旧模块的直接执行仅存在于阶段 5 的隔离 V1 oracle 子进程，V2 主进程不导入它们。

### 1.4 目标目录与接口合同

```text
research_v2/
  __init__.py                # 空/无副作用
  .gitignore                # 只在新目录内忽略 .venv、data、artifacts、临时文件
  requirements.lock         # V2 独立精确版本锁，不 include 根 requirements.txt
  protocol.json             # 在数据收益计算前锁定的研究协议
  cli.py                    # 唯一 V2 CLI，各阶段逐步新增子命令
  contracts.py              # DatasetManifest, PeriodEvent, SignalFrame, RunSpec, RunResult
  guards.py                 # 路径、冻结、依赖、输出不可覆盖检查；初版 stdlib-only
  provenance.py             # canonical JSON、hash、trial registry、阶段凭证
  data.py                   # 离线加载、复权代理校验、分区访问
  calendar.py               # XNYS、timezone、完整周/月与 next session
  splits.py                 # Train / Validation / holdout / prospective / WF folds
  signals.py                # momentum + SMA + deterministic selection
  schedule.py               # signal close -> next-session open
  ledger.py                 # 自融资成交、成本、现金、持仓
  engine.py                 # 每日 mark-to-market
  metrics.py                # 统一 daily 风险指标与 turnover
  baselines.py              # monthly matched、V1-spec、SPY
  legacy_oracle.py          # 隔离旧代码执行器，不在主进程 import src
  experiments.py            # 9 个固定模型运行、分区控制
  walkforward.py            # nested chronological selection
  selection.py              # 确定性模型锁定与 no-go
  stability.py              # 3x3 邻域与跨期排名
  regimes.py                # 预先定义且滞后的市场状态
  bootstrap.py              # 成对 stationary block bootstrap
  multiple_testing.py       # family / Holm / trial audit
  oos.py                    # 一次性历史解封与前瞻 OOS 门控
  forward_data.py            # 锁定后独立新数据导入/复权单位映射，不改冻结 data.py
  reporting.py              # 表格、图、结论与限制
  replay.py                 # 离线重放与语义哈希比较
  data/                     # 本地冻结快照；不进 Git
  artifacts/                # 各 run_id 的不可覆盖产物；不进 Git
tests_v2/
  conftest.py                # 合成数据、网络禁用、独立临时目录
  fixtures/                 # 小型手算 fixture，不存真实未来结果
  test_*.py
docs/research_v2/
  REPO_MAP.md
  STATUS.md
  RESEARCH_PROTOCOL.md
  baseline_manifest.json
  dataset_manifest.json
  phase_01.md ... phase_09.md
  protocol_lock.json
  model_lock.json
  final_report.md
  release_manifest.json
```

最小接口要在阶段 1 定义，后续尽量不改签名：

- `load_dataset(manifest_path, partition, as_of) -> DatasetView`：只返回允许区间与历史 warm-up，不让 selector 获得 holdout。
- `build_periods(sessions, bars, as_of) -> PeriodTable`：真实 session、close 时间、完整性，不用周五标签冒充交易日。
- `compute_signals(weekly_close, daily_close, model_spec) -> SignalFrame`：逐条保留 numerator/denominator/trend 数据截止点。
- `build_schedule(periods, cadence, as_of) -> list[PeriodEvent]`。
- `simulate(dataset_view, targets, schedule, run_spec) -> RunResult`：纯输入输出；结果路径由 CLI 决定。
- `evaluate(daily_nav, trades, initial_nav, interval) -> Metrics`。
- `RunSpec` 必含 model_id、role、partition、cost_bps、seed、protocol/data/code/calendar hash。

所有 CLI 必须 `--help` 可用、未知参数报错、缺输入失败；不得创建“返回 PASS”的占位实现。运行写新目录，已有 run_id 时拒绝覆盖。JSON 严禁裸 NaN/Infinity，用 null + reason。`STATUS.md` 仅为人读索引，不能代替机器验收凭证。

## 2. 锁定研究定义（实施时不得自行调优）

### 2.1 九个基础模型与日历语义

- Universe 顺序固定为 `SPY, QQQ, IWM, EFA, EEM, GLD, TLT, IEF`；精确同分按该顺序处理。
- model_id 为 `W_M06_T36`、`W_M06_T40`、`W_M06_T44`、`W_M09_T36`、`W_M09_T40`、`W_M09_T44`、`W_M12_T36`、`W_M12_T40`、`W_M12_T44`。
- **M = 自然月，不把 6/9/12M 静默替换成 26/39/52W。** 周 `t` 的信号日为最后一个 XNYS session 的实际收盘。
- `s_t` = 当前完整周最后 session；`s_(t-1)` = 上一完整周最后 session；`a_L(t)` = 不晚于 `s_t - DateOffset(months=L)` 的最近交易日。
- `momentum_L(t) = AdjClose[s_(t-1)] / AdjClose[a_L(t)] - 1`。起点相对当前信号日回溯 L 个自然月，终点跳过整个最近完整周；不是从 numerator 再回溯 L 月。
- `SMA_K(t) = mean(weekly_adj_close[t-K+1:t])`，K ∈ {36,40,44}，含当前完整周，要求 K 个连续且完整的周观察。
- `eligible = finite(momentum) AND finite(SMA) AND weekly_adj_close(t) > SMA_K(t)`；不要新增 momentum > 0 过滤。
- 通过 trend 后再按 momentum 选 Top 3。3/2/1 只分别为 1/3、1/2、1；0 只全现金。无杠杆、无空头，现金利息 0；允许研究用小数份额。
- 无止损、vol targeting、risk parity、额外风控择时、交易阈值、择优跳过周、优化权重或额外资产。

### 2.2 完整周与信息集

周分组固定为当地自然周 Monday–Sunday（可用 W-FRI 作内部 label，但依据实际周内 XNYS session，不依据 label 日期判断完成）。只有所有预期 session 的八只 ETF 数据齐全、最后 session 已实际收盘，才是完整周。周五休市时周四可成为信号日；周一休市时执行顺延至下个真正 session。一个交易日的合法假日周仍是完整周。

传入显式带时区 `as_of`，以 America/New_York 解释，存储 UTC timestamp 和 session_date。禁止依赖本机北京时间、`now()` 或“数据最后一行就是周末”。首个截断周不作有效历史点；末尾未完成周不能产生信号，但已经完成的日线仍必须参与持仓 MTM。

`execution_session = XNYS.next_session(signal_session)`，`execution_open_ts > signal_close_ts`。如果 execution 超出快照/as_of，记录 pending event，不成交。信号只可读截至 signal close 的输入；execution open 仅可用于成交和换算份额，不能用于排名。正常 fixture 中必须覆盖隔夜跳空。

价格采用 `adj_open = raw_open * (adj_close / raw_close)` 和 `adj_close` 的一致复权代理；不要再次记分红现金。报告明确其为总回报近似/研究价格，不能称为真实可成交报价或完整 point-in-time corporate-action 数据。冻结快照、防未来截断测试不等于供应商数据从未事后修订。

### 2.3 对照与可归因结论

| 对照 | 定义 | 作用 |
|---|---|---|
| `M_MATCH_<model_id>` × 9 | 与对应 Weekly 使用同一个“截至该时点最近完整周”的信号函数；仅在完整月末做决策，次 session open 执行 | 主 frequency 对照：固定窗口、skip、selector、成本、ledger，只改变决策/再平衡日历 |
| `V1_SPEC_UNIFIED` × 1 | 冻结 V1 12M-1M、10M SMA、Monthly、Top 3 规则，用 V2 会计/日历重放 | 同会计口径的 V1 策略比较 |
| `V1_FROZEN_ORACLE` × 1 | 隔离执行 research-v1 tag 原始代码，不修正算法 | 历史行为审计，保留与统一口径的桥接差异 |
| `SPY_BUY_HOLD` | 相同起止日期、初始资本、建仓成本、不强制期末清仓 | 外部基准，不参与模型选择 |

Monthly matched 的月末若周尚未完成，使用前一完整周信号，不利用月末半周。周更新的特征函数在两种 cadence 中一致；两者信息年龄分布可以不同，这正是此定义下 cadence 的结果。不要把此对照称作传统月线 V1。

主 endpoint：同日期、5 bps 下 `ΔSharpe = daily_Sharpe(Weekly) - daily_Sharpe(M_MATCH)`；同时必须报告相对 `V1_SPEC_UNIFIED` 的 ΔSharpe、ΔCAGR、ΔMaxDD。Sharpe(rW-rM) 是另一统计量，不可冒充两个 Sharpe 的差。

### 2.4 Train / Validation / 留出 / Final OOS

日期按当前已知缓存设定，不看收益后改切分：

| 区间 | 默认范围 | 使用权限 |
|---|---|---|
| warm-up | 2005-01-03 起 | 只用于历史特征；不计入绩效 |
| Train | 2006-02-01 至 2015-12-31 | 开发、测试、训练内 WF；起点沿用已知 V1 最长窗口起始区间 |
| Validation | 2016-01-01 至 2020-12-31 | 冻结规则下选一个候选与稳定性评估 |
| Retrospective holdout | 2021-01-01 至 2026-09-18 | 模型锁定后一次性打开；标记 `RETROSPECTIVE_HOLDOUT_EXPOSED_BY_V1` |
| Prospective Final OOS | 模型锁定且所需历史数据已可用后的第一个合格周执行日，开始前全现金；预注册 24 个月 | 真正 Final OOS。首次成熟评估不少于 504 sessions 且达到 24 个自然月；不提前根据盈亏终止或调参 |

原有历史绩效可能影响过研究者的选择，故 Train/Validation 切分也不能抹去总体研究者偏差。确认性推断只能在严格新数据与锁定协议下进行。

若实际执行时数据已更新：先核验与当前记录的差异；不得自动延长历史研究终点或重划窗口。优先固定到 2026-09-18 的快照。新数据放前瞻流程；如锁定时研究者已看到新数据，其已见日期必须列入 exposure ledger，OOS 起点严格晚于它。历史不足/缺口则停止对应数据阶段，不缩短长窗口凑结果。

每个独立 scored segment 默认从共同初始资本 100,000、全现金开始；所有策略共享 segment 第一 session 和末 session，只在自己的下一个有效 schedule 执行，建仓前现金收益为 0。允许使用 segment 之前的历史特征；不允许把 warm-up 持仓收益计入 scored segment。不得各自删掉首笔交易前现金天数。

### 2.5 会计、成本与指标口径

在执行日开盘，旧持仓先承担前一收盘到本日开盘的 overnight P&L。定义 `Vpre = cash + Σ(q_old * p_open)`，`a_i=q_old_i*p_open_i`，目标风险资产权重 `w_i`，费率 `c=bps/10000`。求解唯一自融资根：

`Vpost + c * Σ |w_i * Vpost - a_i| = Vpre`，然后 `q_new_i = w_i*Vpost/p_open_i`，`cash_new=(1-Σw_i)*Vpost`。

使用有界二分，0≤Vpost≤Vpre；禁止通过 `max(cash,0)` 消灭会计亏空，禁止预扣固定 5 bps 代替真实成本。成交费用 = c × ETF 买卖绝对名义额，不对 CASH 收费。交易方向与账本保留；每期相同目标也可能因漂移产生真实交易，只有当前实际权重已经等于目标时才零交易。

- 成本场景固定 `{0,5,10,20,30,50}` bps / 每一边名义额，5 为主场景。其他成本仅压力测试，不据此选另一个模型。费用包括简化手续费/价差/冲击假设，不宣称校准了市场冲击。
- 每个场景完整重跑 ledger；不得把 0 bps 净值末尾减去估计费用作为成本结果。gross-return 路径定义为同信号 0 bps 独立运行。
- `gross_traded_notional_j = Σ_ETF |trade_notional_ij|`（货币金额）；`gross_trade_ratio_j = gross_traded_notional_j / Vpre_j`。
- `one_way_target_turnover_j = 0.5 * Σ_(8 ETFs + CASH) |w_target - w_pretrade|`；它是目标权重口径，明确与实际成交口径区别。
- `annualized_one_way_turnover = Σ one_way_target_turnover / elapsed_years`；`annualized_gross_traded_notional_ratio = Σ gross_trade_ratio / elapsed_years`。另报总货币交易额、总费、交易次数、买额/卖额。不能把 gross/2 自动当作含 CASH 单边换手率。
- 首次建仓计成本和换手；主结果不强制期末清仓。segment/Fold 重启规则与建仓成本显式记录。
- 每个 XNYS session 收盘记录 `cash + Σq*adj_close`。首日 return = 首日收盘 NAV / 初始资本 - 1；以后 = NAV_t / NAV_(t-1) - 1，不能用 pct_change 丢掉首日成本与收益。
- Sharpe = mean(daily_return)/sample_std(ddof=1) × sqrt(252)，rf=0；vol 同 sqrt(252)。CAGR 使用实际首日开盘至末日收盘 elapsed seconds / (365.25×86400)。MaxDD 从含初始 NAV 的逐日路径计算；年度/周/月收益由每日收益连乘。
- 零波动、不足两点、未恢复回撤均显式 NA+原因；MaxDD 用正损失幅度，差值正数表示回撤更大。所有年化、统计量、分段样本量都有统一定义和测试。

### 2.6 可复现与防数据窥探

数据 SHA-256、代码 commit、依赖精确版本、Python/OS、calendar 版本和导出的 session 表 hash、协议 hash、随机算法/seed、模型网格、各 partition/fold 边界写入每个运行 manifest。正式输出只使用冻结 CSV 与导出的 calendar，不重新下载。

`trial_registry.jsonl` 在第一次计算每个实际尝试之前追加计划记录，之后追加成功/失败状态；保留失败、放弃、人工改规则、seed、成本、对照和运行 hash。九个基础模型 ≠ 九次全部尝试，报告参数选择数、固定对照数、场景数、实际运行数及未记录先验探索的限制。不从失败日志删除不利结果。

同输入重跑到新 run_id；数值 CSV/JSON canonical payload 应相同。timestamp/run_id/runtime 单独放 envelope，不能通过删除其他差异伪造复现。图形像素差异不作为主要数值复现判据。

## 3. 所有阶段通用执行合同

下面是**要实现后的目标命令**，不是当前仓库已有功能。阶段 1 建立 CLI / guards 骨架；每阶段先实现该阶段子命令与测试，再执行命令，禁止先调用不存在的 CLI 然后误报环境故障。

每个新终端先执行：

```bash
cd /Users/zhangzeyuan/Projects/quant-ETF
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export MPLCONFIGDIR="$PWD/research_v2/artifacts/_runtime/matplotlib"
export PYTHONHASHSEED=0
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export V2_PY="$PWD/research_v2/.venv/bin/python"
```

阶段 2–9 开始与结束均执行（guards 自己不得写原目录）：

```bash
"$V2_PY" -m research_v2.cli guard --manifest docs/research_v2/baseline_manifest.json
git diff --exit-code 3f708f888277a6564ea6db204dafde95c2a548c7 -- src config scripts tests results requirements.txt .gitignore implementation_plan.md .env.example
git rev-parse research-v1
git rev-parse paper-v1-ready
git status --short
```

guard 必须比较原 tracked 文件哈希、mode、mtime_ns，原缓存哈希与 mtime，以及旧输出/state 的基线清单（不输出内容、不读取凭证）。`.env*` 只记录存在性/元数据，不能把 secret 存进 manifest。atime 不作为失败依据。若 Paper 正在外部运行而旧日志/state 自主变化，报告并区分外部变化，不能把它误称为本任务已证明 untouched，也不能恢复覆盖外部 state。

每阶段生成 `research_v2/artifacts/phase_0N/phase_receipt.json` 和 `docs/research_v2/phase_0N.md`，收录命令、退出码、测试数、失败/skipped、输入/输出 hash、保护检查结果、下一阶段入口；更新 `STATUS.md`。阶段 1 即规定凭证 schema，后续不可用手写 PASS 替代实际结果。

Git checkpoints 均是建议：只有阶段验收通过且变更范围合法后才提交；stage 指定新增目录，不用 `git add -A`，不提交原始行情、凭证、虚拟环境或巨型产物。不得为提交而修改全局 Git 配置。权限不允许时保留已验证文件并报告未提交，不能声称 checkpoint 已完成。

## Stage 1 — 冻结边界、独立骨架与研究协议

### 1. Objective

把后续工作限制在 V2 新目录内，固定研究假设、输入输出合同和只读基线，使其他阶段不再重新探索仓库。依赖：无。

### 2. Files to inspect

本文；`config/config.py`、`requirements.txt`、`.gitignore`、`src/utils/logger.py`；V1/Paper tag 与当前 HEAD 元数据；旧文件路径清单。按 §1.3 定位读函数，不再通读全部 Paper 源码。对现有缓存只查 schema、起止日期与哈希，不看策略收益。

### 3. Files to create

`research_v2/{__init__.py,.gitignore,requirements.lock,protocol.json,cli.py,contracts.py,guards.py,provenance.py}`；`tests_v2/{conftest.py,test_guards.py,test_protocol.py}`；`docs/research_v2/{REPO_MAP.md,STATUS.md,RESEARCH_PROTOCOL.md,baseline_manifest.json,protocol_lock.json,phase_01.md}`；本阶段 artifacts/receipt。

### 4. Files allowed to modify

只有本阶段新建文件；没有任何已有项目文件的修改许可。新目录内部 `.gitignore` 忽略 `data/`、`artifacts/`、`.venv/`、`__pycache__/`，不要修改根 `.gitignore`。

### 5. Files explicitly forbidden to modify

完整 F0；特别是根配置、依赖、原 `.venv`、旧日志和 `.git` 已有 tag。禁止为了方便让旧配置接受 V2 mode。

### 6. Exact implementation requirements

1. `guards.py` 初始可用 stdlib 执行。记录所有原 tracked 文件 SHA-256/mode/mtime、缓存/结果/state 元数据、tag object ID 与 peeled commit ID；不跟随越界 symlink。manifest 自己及未来 V2 文件不列入旧文件集合。
2. 基线建立前检查 HEAD 等于本文记录，或是仅含本文的后续文档提交；发现其他代码漂移就只读核查差异，不能静默重置基线。
3. guard 实现 output root 白名单、路径 resolve 后检查、symlink/`../` 越界拒绝、已有 run_id 拒绝覆盖；CLI 导入无文件/网络副作用。
4. 独立 V2 环境从 Python 3.12 建立；只装 pandas、numpy、matplotlib、pytest、exchange-calendars 及其锁定传递依赖。精确版本先根据现环境 metadata 选取并验证，记录 wheel/来源；不凭“最新版”升级。隔离 legacy oracle 所需 alpaca-py/yfinance/python-dotenv 也固定在独立环境中，生产 V2 无 broker 依赖调用。
5. `protocol.json` 落实 §2 全部默认值，包括 9 个模型、6 个成本、所有 split、selection/WF/bootstrap/regime/OOS 规则、24 个月/504 session 前瞻门槛；canonical hash 写入 `protocol_lock.json`。该锁先于任何真实收益运行。阶段 2 可追加数据 hash，不能改已锁定参数。
6. `trial_registry` append-only，protocol 改动生成 amendment 和新 experiment_id；看过 holdout 后不得以“修 bug”为由继续沿用未见声明。
7. 在 `REPO_MAP.md` 固定 §1.3 的可复用/不导入原因；在 `STATUS.md` 写阶段依赖与命令表。初始验收只跑 synthetic/guard，不触发历史回测。

### 7. Tests to add

- import V2 不创建旧 logs/cache、不加载 `src.*` / `config.*` / broker。
- 临时 sandbox 内篡改受保护 fixture 的字节、mtime、mode、tag 引用可检出；**不能真的改原文件测试 guard**。
- symlink/相对路径逃逸/输出重名拒绝；不存在输出目录可在 V2 内安全创建。
- 九个 model_id 唯一，Universe/Top 3/无杠杆/参数全集固定；无效配置失败。
- 凭证、canonical JSON、hash 和 trial 去重/追加行为确定性。

### 8. Commands Codex should run

先实现 stdlib bootstrap/guard，再执行：

```bash
git status --short
git rev-parse HEAD research-v1 paper-v1-ready
.venv/bin/python -m venv research_v2/.venv
"$V2_PY" -m pip install -r research_v2/requirements.lock
"$V2_PY" -m pip check
"$V2_PY" -m research_v2.cli baseline-init --expected-head 3f708f888277a6564ea6db204dafde95c2a548c7 --output docs/research_v2/baseline_manifest.json
"$V2_PY" -m research_v2.cli protocol-lock --config research_v2/protocol.json --output docs/research_v2/protocol_lock.json
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_guards.py tests_v2/test_protocol.py
"$V2_PY" -m research_v2.cli guard --manifest docs/research_v2/baseline_manifest.json
```

若 HEAD 仅多了本文的文档提交，`baseline-init` 必须验证受保护路径对 expected-head 无差异，并记录实际 HEAD；不能简单拒绝合法文档提交，也不能豁免其他变更。独立环境已存在时先核对 lock，不反复重建或覆盖。依赖无法取得时保存明确环境阻塞，不把安装失败当作测试通过。

### 9. Expected outputs

锁定协议、机器可读旧文件基线、精简仓库映射、可工作的隔离保护测试、版本锁和阶段 1 凭证；尚无九模型真实收益结果。

### 10. Acceptance criteria

所有保护/协议测试通过；原文件保持字节及约定元数据不变；两 tag 不变；无凭证泄露；本次新增路径全集符合白名单；阶段 2 只需阅读本文、映射和凭证即可继续。

### 11. Git checkpoint recommendation

分支建议 `codex/research-v2-weekly`（先检查是否存在，不覆盖）。只 stage `research_v2/`、`tests_v2/`、`docs/research_v2/` 的非忽略文件；检查 staged diff 后提交：`research(v2): freeze protocol and isolate research workspace`。

## Stage 2 — 冻结数据、交易日历与分区权限

### 1. Objective

构造只读、可重放、严格校验的 V2 输入层，确立 complete-period 及分区规则。依赖：Stage 1。

### 2. Files to inspect

Stage 1 凭证/协议/映射；只读 `data/cache/*.csv`；`src/data/fetcher.py:calculate_adjusted_ohlc` 和 `src/data/validator.py` 的已定位函数；V2 guards/contracts。此阶段不读旧策略收益。

### 3. Files to create

`research_v2/{data.py,calendar.py,splits.py}`；`tests_v2/{test_data_snapshot.py,test_calendar.py,test_splits.py}`；小型 calendar/data fixtures；`docs/research_v2/{dataset_manifest.json,phase_02.md}`；`research_v2/data/snapshot_20260918/` 的八只 ETF 原始副本、规范化分区数据和 calendar；对应 artifacts。

### 4. Files allowed to modify

V2 `cli.py`、`contracts.py`（仅兼容补充）、`provenance.py`、`tests_v2/conftest.py`、`docs/research_v2/STATUS.md`。`protocol.json` 只读；数据绑定写新 dataset manifest。

### 5. Files explicitly forbidden to modify

F0；特别是 `data/cache/*.csv`、旧 fetcher/validator、Paper calendar、旧 cache 元数据；禁止刷新旧缓存、silent forward/back fill 或覆盖 snapshot。

### 6. Exact implementation requirements

1. 原始字节复制前后核验哈希，读取 schema、供应商/已知下载时间（未知写 unknown）、起止 session、复权口径、单只行数；快照和新规范化数据分别 hash。
2. 明确供应商输入是 session date，不把 naive 日期误作 UTC 再转换而前移一天；timestamp 和 session label 分开存。
3. 使用 XNYS 在显式起止范围内生成 session/open/close 表；覆盖 historical exceptional closures 和 early close。由第三方 calendar 提供，不手写假日表。冻结导出表与 library version。
4. 逐 ETF 对照预期 sessions 验证缺日、重复、乱序、NaN、inf、非正价格、负 volume、OHLC 关系、复权因子一致。缺口必须列出并失败，不能 inner join 掩盖单只或全体缺日。
5. 两级 completeness：日线需已收盘；周/月需覆盖所有应有 sessions 且该 period 最后 session 已收盘。数据快照截止显式为 `2026-09-18T16:01:00-04:00`；不要求下周数据证明周已完成。
6. 构造 §2.4 partitions；按 manifest 读取 train/validation 的 DatasetView 不含后续行情。原始整段副本仅用于审计，真实研究 CLI 不允许裸路径绕过 partition。
7. 基础 snapshot 可含 holdout，但阶段 2 仅进行价格/日期质量校验，不计算持仓/策略收益/排名。不是操作系统级密码学隔离：文档承认研究者仍能接触历史；逻辑 gate 负责防止流水线误用。
8. `splits.json` 区分 feature_start、score_start、score_end；列出所有 annual WF 折（细节见 Stage 6）。始终使用完整历史 warm-up，不在分区边界重新 rolling 而改变信号。

### 7. Tests to add

- Good Friday 2026-04-03：本周结束于 04-02，下一 session 04-06；周一假日 2026-09-07：上一周信号执行于 09-08。
- 黑五 early close、DST、跨年 ISO/week label、闰日、首尾半周、单 session 合法周（合成日历）。
- 周五 close 前 as_of 不能使用周五日线，close 后可；周四缺失/所有 ETF 同缺一天均失败。
- 禁止非 finite/非法 OHLC、重复/错误时区；adj_close 缺失不能 fallback 到 close。
- 读取 Train/Validation 不泄露 holdout；warm-up 不计入 score，日期边界无重复无漏日。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_data_snapshot.py tests_v2/test_calendar.py tests_v2/test_splits.py
"$V2_PY" -m research_v2.cli snapshot --source data/cache --cutoff 2026-09-18 --as-of 2026-09-18T16:01:00-04:00 --snapshot-id snapshot_20260918
"$V2_PY" -m research_v2.cli validate-data --manifest docs/research_v2/dataset_manifest.json --run-id phase_02
"$V2_PY" -m research_v2.cli audit-splits --protocol research_v2/protocol.json --manifest docs/research_v2/dataset_manifest.json --metadata-only
```

运行通用 guard 前/后检查。`snapshot` 自动写预定 dataset manifest，不接受任意 V1 输出路径。

### 9. Expected outputs

数据 manifest、独立不可覆盖快照、冻结 XNYS sessions、coverage/completeness/splits audit、明确的 missing-data 错误或 PASS、阶段凭证。

### 10. Acceptance criteria

八只 ETF 全覆盖所需 sessions；原缓存字节与 mtime 不变；完整周/月边界全部通过；分区日期只依赖协议与 calendar，不依赖收益；无任何需要网络的正式数据检查。

### 11. Git checkpoint recommendation

提交新增数据/日历代码、测试与小型 manifest，**不提交行情副本**：`research(v2): freeze data and enforce session-aware partitions`。

## Stage 3 — 周信号、选择器与防未来执行日程

### 1. Objective

精确实现九模型规则，并证明未来价格变化不能修改历史信号和已发生的执行。依赖：Stage 2。

### 2. Files to inspect

§2.1–2.3；V2 `data.py/calendar.py/contracts.py/splits.py`；旧 `src/signals/{momentum,trend,selector}.py` 只读一次作语义参照；阶段 2 calendar fixtures。

### 3. Files to create

`research_v2/{signals.py,schedule.py}`；`tests_v2/{test_weekly_signals.py,test_selection.py,test_schedule.py,test_lookahead.py}`；手算 fixture；`docs/research_v2/phase_03.md`；signal lineage 输出。

### 4. Files allowed to modify

V2 `cli.py`、兼容性的 `contracts.py`、`tests_v2/conftest.py`、`STATUS.md`。如 Stage 2 有已定位 bug，只能先补其测试再修其 V2 文件并记录 amendment；不改研究定义。

### 5. Files explicitly forbidden to modify

F0；协议参数、数据快照、分区边界和 stage 2 的已冻结证据；禁止把旧 Monthly 函数泛化以服务 V2。

### 6. Exact implementation requirements

1. 严格实现自然月 anchor 与整周 skip；SMA 包含当前已完成周，窗口内任一不完整/缺价都不可计算，不能缩小 min_periods。
2. 每个 symbol/model/signal 存 numerator_date、denominator_date、trend_start/end、latest_input_ts、score、trend_pass、rank、selected、weight；不足历史记录原因。
3. 先 trend 后 ranking；同分遵循固定 Universe 顺序。NaN 不得被 bool(NaN) 当作通过。所有 risk weights≥0，总和≤1，现金补足至 1。
4. event schema 包含 period_id、signal_session、signal_close_ts、execution_session、execution_open_ts、feature_cutoff、status。next-session 来自完整 calendar，不取“数据中下一行”替代缺失 execution session。
5. 分别构建 Weekly 与 Monthly matched cadence；Monthly 使用 month-end 时已经可用的最新 Weekly signal，不在月末从半周计算新信号。
6. 有合法信号但没有 execution 价格时，期末未到执行日记 pending；历史内部应有执行日却缺价则 fail-closed。执行/价格存在不影响信号本身。
7. 暂不执行真实组合回测；只允许 Train 的 signal audit。九模型矩阵和 lineage 中每个日期均可追溯。

### 7. Tests to add

- 自然月非交易日 anchor、2 月/月末偏移、6/9/12 月手算、one-week skip off-by-one；证明结果不等同于硬编码 26/39/52W。
- 36/40/44 周 SMA 当前周包含、严格大于、相等不过线；0/1/2/3/8 个合格、并列分数。
- Prefix invariance：截断到每个 signal close 重算，与全量同日期结果完全一致。
- Future poisoning：替换信号之后的 close/open、append 未来行，不改变过去 score/weights/events；改变 execution open 只能影响成交份额，不能影响权重。
- 改变 skip 周内价格不改变 momentum 分子/分母，但允许改变当周 trend；不能写成“所有信号都不变”的错误测试。
- 周五休市/月末半周/周一休市的信号与执行时间精确一致；不存在同日 close 成交。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_weekly_signals.py tests_v2/test_selection.py tests_v2/test_schedule.py tests_v2/test_lookahead.py
"$V2_PY" -m research_v2.cli audit-signals --partition train --all-models --run-id phase_03
```

加通用 guard；查看少量明确 signal 日期的 lineage，不 dump 全部日线和模型矩阵到聊天上下文。

### 9. Expected outputs

九模型信号矩阵、weekly/monthly schedule、精选日期 lineage、warm-up/pending events 说明、防未来测试报告和阶段凭证。

### 10. Acceptance criteria

九个模型数量和公式精确匹配协议；prefix/future poisoning 测试通过；所有 events 满足 signal close < execution open；不得用生成了“周标签”替代 complete-period 验收。

### 11. Git checkpoint recommendation

`research(v2): add causal weekly signals and execution schedules`；只提交本阶段代码/测试/文档，不提交真实 signal 大表。

## Stage 4 — 自融资账本、Daily MTM 与成本度量

### 1. Objective

建立独立、可信的回测会计与绩效层；先用手算数据验证，再跑一个 Train canary。依赖：Stage 3。

### 2. Files to inspect

§2.5；V2 contracts/schedule；旧 `src/backtest/engine.py` 的成本与 MTM 段、`src/backtest/metrics.py`；旧 `tests/test_phase45.py` 仅作为案例定位。

### 3. Files to create

`research_v2/{ledger.py,engine.py,metrics.py}`；`tests_v2/{test_ledger.py,test_daily_mtm.py,test_metrics.py,test_costs_turnover.py}`；手算账本 fixture；`docs/research_v2/phase_04.md`。

### 4. Files allowed to modify

V2 `cli.py`、`contracts.py`、`provenance.py`、`tests_v2/conftest.py`、`STATUS.md`。仅修 V2 已定位接口问题，不改研究协议。

### 5. Files explicitly forbidden to modify

F0；尤其旧 `engine.py/metrics.py`。即使发现 V1 会计问题，也只能在桥接报告说明，不能修冻版本以求统一。

### 6. Exact implementation requirements

1. 按 §2.5 自融资方程计算 post-cost 目标，根残差默认≤`1e-10 * max(1,Vpre)`；数值截断仅在 tolerance 内且记录 residual，绝不可隐藏真实负现金。
2. 每日顺序：旧持仓 overnight → open pretrade NAV → 如有 schedule 则交易与扣费 → 新持仓 intraday → close NAV。非交易日不造收益行，非调仓日保持份额不变。
3. 记录 `daily_nav.csv`、`daily_positions.csv`、`trades.csv`、`rebalance.csv`、`metrics.json`。交易表含 old/new qty、open proxy、signed notional、gross notional、fee、pre/post cash/NAV、event_id。
4. 每日断言 position/cash/NAV 勾稽、无负持仓、无实质负现金、gross exposure≤1+tolerance；缺持仓估值价时失败，不能把资产估值为 0。
5. turnover 同时报告含 CASH 的目标权重口径与 ETF 实际名义额口径；初始建仓计入，固定 100,000 初始资本。相同信号不等于零换手。
6. 首日收益、期末 partial week 日线、现金日和最初 NAV 都纳入风险计算。月度/周度聚合仅作展示，主风险以 daily 测量；partial period 展示必须标注，不当成完整周期独立样本。
7. 性能要求：缓存不变的信号，不重复网络/日历构造；核心优先正确清楚，不先做 JIT/多进程。真实 canary 仅 `W_M12_T40` 的 Train 区间，不按结果调整参数。

### 7. Tests to add

- 全现金→单资产、单资产→现金、A→B 满仓轮换、三资产漂移再平衡；手算 cash/fee/notional 一致。
- 零成本、零实际交易、费用只能随实际成交产生、根方程与 no-leverage 断言。
- 100% A→100% B：one-way target turnover=1，gross ratio 约为2（有费时按实际值）；100% A→现金：两者为1，不是 gross/2。
- 交易日开盘跳空不被新持仓提前赚取；非交易日无 NAV，周内暴跌能进入 daily MaxDD。
- 首日成本/收益与初始 drawdown 不丢；现金-only 的 Sharpe NA；年度连乘与全段 NAV 比例相等。
- 固定信号的多成本完整路径、总费用等于逐笔费用和；一般测试最终净值随线性费率不改善，不能要求 Sharpe 对成本严格单调。
- 合成除息/拆股复权一致，不重复发红利；输入 price 不变时 ledger 不制造财富。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_ledger.py tests_v2/test_daily_mtm.py tests_v2/test_metrics.py tests_v2/test_costs_turnover.py
"$V2_PY" -m research_v2.cli canary --partition train --model W_M12_T40 --cost-bps 5 --run-id phase_04
"$V2_PY" -m research_v2.cli audit-ledger --run-id phase_04
```

执行通用 guard；输出 audit 摘要，不能因为策略收益差而重调 ledger/窗口。

### 9. Expected outputs

手算测试证据、Train canary 全部 ledger/MTM/metrics、accounting residual 汇总、成本与换手单位字典、阶段凭证。

### 10. Acceptance criteria

所有会计/时间顺序测试通过；无人工 cash clamp；0 bps/gross 与有成本净路径可分别重放；每日/逐笔/汇总指标互相勾稽；原回测代码与结果不变。

### 11. Git checkpoint recommendation

`research(v2): add self-financing daily research ledger`。这是后续研究可信度的关键检查点；未通过不可进入真实模型比较。

## Stage 5 — Monthly 对照、V1 桥接与 Paper 回归隔离

### 1. Objective

使 frequency 比较可解释，同时证明 V1/Paper 受保护；解释旧引擎与新会计的差异，不篡改旧成果。依赖：Stage 4。

### 2. Files to inspect

§1.3、§2.3；V2 ledger/metrics/schedule；`git show research-v1:src/backtest/engine.py`、tag 中的 signal/selector/fetcher/config；`results/metrics.csv` 仅日期/schema；现有测试路径与入口，不读 broker 凭证。

### 3. Files to create

`research_v2/{baselines.py,legacy_oracle.py}`；`tests_v2/{test_baselines.py,test_legacy_bridge.py,test_legacy_isolation.py}`；`docs/research_v2/{COMPARISON_CONTRACT.md,phase_05.md}`；隔离 oracle/regression 工作目录与 bridge report。

### 4. Files allowed to modify

V2 `cli.py`、`contracts.py` 的结果元数据、`provenance.py`、`tests_v2/conftest.py`、`STATUS.md`；必要的 V2 baseline 输出接口。不允许改 V1 算法以消除差异。

### 5. Files explicitly forbidden to modify

F0；特别是旧测试、旧配置、既有 `results/` 和原日志/state。禁止在主进程/主目录运行旧模块，禁止旧脚本自动下载，禁止 copy `.env` 到 oracle。

### 6. Exact implementation requirements

1. 实现 §2.3 的九个 matched Monthly、固定 V1-spec 和 SPY buy-hold。所有比较拥有共同 scored sessions、初始资本、现金回报、复权输入和成本场景。
2. 每个 matched model 只变 cadence，signal 函数/参数/selection/ledger 必须共享；测试强制检测其他字段差异。V1_SPEC 用月线原始公式，不把它误写为 12M-1W。
3. `legacy_oracle` 从 `research-v1` 的 Git 对象导出代码到新的 V2 artifacts sandbox。另从固定 HEAD 导出 Paper 回归副本；不以移动 tag/checkout 当前目录实现隔离。
4. 子进程 cwd/sys.path 指向隔离副本，移除全部 Alpaca/secret 环境变量，不拷 `.env`；仅使用固定、经过质量验证的缓存副本；拦截网络 socket/HTTP。为子进程单独设置sandbox内的MPLCONFIGDIR/临时目录，并禁用pyc/pytest缓存；文件写入只能落在sandbox（包括配置import创建的logs/cache）。退出后检查主仓库guard；旧测试要有真实路径溯源，防止意外import主仓库`src`。
5. 回归执行两组：research-v1 自带测试；HEAD 自带研究+Paper+fault-injection 测试。测试完全 mock/离线，不启动 `run_paper.py`，不建立真实 broker 连接。旧失败必须完整记载，并区分基线已失败/环境问题，不能改旧测试。
6. 阶段 5 只对 **Train** 进行策略输出比较，oracle 输入也物理截断到 Train 末，不把整个历史收益带入上下文。数据完整性测试所需>100月可由 2005–2015 缓存满足。
7. 分层桥接：A=原 tag 原始结果；B=同 A 日 NAV 用统一指标重算（首日收益等）；C=原 oracle signals/events 在新 ledger 执行；D=新日历下 V1_SPEC_UNIFIED。分别解释 metric、cost accounting、calendar/schedule 差异，记录 changed events，不把 A↔D 差异都归为 frequency。
8. 最低 parity：合成零费、正常完整月、不触发异常分支时，原始 V1 信号/选股与 V1_SPEC 一致；相同 events/targets/price 的零费 ledger NAV 在 tolerance 内一致。有费差异按自融资方程说明，不强行要求完全相同。
9. 原始 archived results 完整字节保护即可，不能仅凭当前缓存相同日期声称已精确复现旧历史产物；缺原数据/runtime provenance 时写 `ARCHIVE_REPRODUCTION_UNVERIFIED`。

### 7. Tests to add

- 相同 cadence/signal/ledger 两条路径应完全一致；只改 cadence 后参数/成本/度量仍相同。
- V1 12-1 numerator/denominator 与 10M trend 手算；月末 holiday、partial terminal month 差异记录。
- 子进程不读 `.env`、不连网络、不写主目录，实际 imports 源于隔离副本。
- 原始 V1 行为 golden fixture 不修改；新指标补回首日收益；有费 bridge 保留差异。
- 旧 test suite 的输出、退出码、passed/failed/skipped 进入 receipt，不能以未运行代替通过。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_baselines.py tests_v2/test_legacy_bridge.py tests_v2/test_legacy_isolation.py
"$V2_PY" -m research_v2.cli legacy-regression --refs research-v1,3f708f888277a6564ea6db204dafde95c2a548c7 --offline --data-partition train --run-id phase_05_regression
"$V2_PY" -m research_v2.cli compare-baselines --partition train --all-models --cost-bps 0,5 --run-id phase_05
"$V2_PY" -m research_v2.cli bridge-v1 --partition train --run-id phase_05_bridge
```

加通用 guard。legacy 子进程内部才执行 `python -m pytest -q -p no:cacheprovider tests`；CLI 必须记录隔离 cwd，不能把这条命令在主目录运行。

### 9. Expected outputs

对照定义表、Train 配对日收益、旧/新 V1 分层 bridge、两份隔离 regression 日志、冻结检查、阶段凭证。

### 10. Acceptance criteria

对照无日期/资本/费率错配；配对 Monthly 不偷偷改变信号；legacy regression 全通过，或存在可复现且明确的基线失败则阶段标记阻塞而非 PASS；原 V1/Paper 文件与 state 未被本任务修改；V1 差异逐项解释。

### 11. Git checkpoint recommendation

`research(v2): add matched monthly controls and frozen V1 bridge`。checkpoint 附 regression/bridge 小摘要，不提交复制出的旧仓库或大型曲线。

## Stage 6 — 九模型 Train/Validation 与 Walk-forward

### 1. Objective

用固定分区和预注册筛选法产生可审计的候选研究结果，验证选择规则的时间因果性。依赖：Stage 5。

### 2. Files to inspect

protocol/splits/COMPARISON_CONTRACT；V2 engine/metrics/baselines；Stage 5 receipt；无需再读原仓库。

### 3. Files to create

`research_v2/{experiments.py,walkforward.py,selection.py,stability.py}`；`tests_v2/{test_experiments.py,test_walkforward.py,test_selection_policy.py,test_stability.py}`；`docs/research_v2/phase_06.md`；九模型 tables、fold lineage、provisional selection 输出。

### 4. Files allowed to modify

V2 `cli.py`、`contracts.py`、`provenance.py`、`STATUS.md`；不修改 frozen protocol、数据和前序 receipt。

### 5. Files explicitly forbidden to modify

F0；所有冻结输入；禁止读 historical holdout 的策略表现、禁止 shuffle CV/random train-test split、禁止以 2021+ 结果选参数。

### 6. Exact implementation requirements

1. 明确调度 9 个 Weekly + 9 个配对 Monthly + V1_SPEC + SPY；Train 和 Validation 分开输出，禁止把两段拼在一起挑冠军。Stage 6 主/筛选成本为 5 和 10 bps；Stage 7 再补齐全成本网格。
2. **Walk-forward 年份 2011–2020**，一年一步、测试一年、不重叠。每个 Y：inner Train 从 2006-02-01 至 Y-3 年末，inner Validation 为 Y-2、Y-1 两个完整自然年，测试为 Y 年。最早折：Train 2006–2008、Validation 2009–2010、Test 2011；最后折：Train 至2017、Validation2018–2019、Test2020。
3. inner Train 用于工程/历史 fit 信息边界，不能看 inner Validation 之后的数据；momentum/Equal Weight 本身无估计参数，须明说“训练”不意味着拟合机器学习模型。每折候选只由 inner Validation 及以前决定。
4. 每折运行同一个固定选模函数；允许借用测试前历史算特征，但不使用测试收益算排名/阈值/成本。无 eligible 模型则该折策略持现金，明确 `NO_GO`，不能挑一个表现最好者强行执行。
5. WF 主诊断路径在相邻外测年边界**延续真实份额/现金**，不做免费重置；新模型生效于该年第一合格周执行日，之前旧持仓继续 MTM；配对 Monthly 对照也按其自身首个合法月度 event 换模型。切换费用完整计入。
6. 另提供 per-fold 从全现金起步的可比测试表，明确标识 `reset_fold_diagnostic`，不能把免费 reset 的曲线拼成可交易的 WF NAV；不可对各折 Sharpe 直接求均值冒充整条路径 Sharpe。
7. WF 路径只评估选择程序，不成为“第十个候选”。2016–2020 折使用较早 Validation 年份是顺序学习，不是全局未见确认；在 report 中分清 Train 内2011–2015诊断与2016–2020 validation-time 诊断。不得声称两者是独立证据。
8. 固定最终候选筛选（先写规则，再运行）：基于完整 2016–2020 Validation，要求 5 bps ΔSharpe_vs_matched≥0.10、ΔSharpe_vs_V1>0、10 bps ΔSharpe_vs_matched>0、MaxDD 比 matched 恶化≤0.05绝对值；年度 paired ΔSharpe 为正的年份≥ceil(0.6×有效年数)。五个年度应均有效，否则 eligibility 为 false，不能删掉坏年。
9. 稳定性 gate：3×3 网格的上下左右直接邻居（不含对角）至少一半在 Validation 的 5 bps paired ΔSharpe>0。角/边/中间邻居数分别2/3/4。选择 eligible 模型中完整 Validation paired ΔSharpe 最大者；差≤1e-8 时先低 annualized gross trade ratio，再 model_id 字典序。
10. inner WF 的两年 Validation 使用同一 gate，年度正值要求 ceil(0.6×2)=2，其余定义相同。年度不足、Sharpe NA 则不 eligible。Stage 6 输出 provisional selection；Stage 7 不得依据其他探索性统计换候选，只能按原规则锁定或 NO_GO。
11. 参数稳定性额外报告 nine-model rank、邻域中位/最差 ΔSharpe、spread、各折 selected frequency、top-3 frequency 和跨年 rank correlation（ties 固定算法）。这些诊断不触发新参数搜索。

### 7. Tests to add

- 九模型/对照/成本运行计数、trial_id 一致，无漏模型或重复计数；失败模型保留明确状态。
- 改未来 fold/test/holdout 数据，过去折选模/排名/现金轨迹完全不变。
- 各 fold 日期、最大 feature timestamp、选择发生时间、测试第一天与下一有效执行日可证明合法。
- hand fixture 检验 selection gate、边界0.10/0.05、tie-break、邻域数、NO_GO；禁止看到真实结果后变阈值。
- WF 模型切换完整扣费、持仓延续，无免费清仓；reset 与 continuous 两种输出不能混标。
- 共同验证日期不得由最佳模型开始日期决定；不均衡样本和全现金 Sharpe 不可被当成0分隐式胜出。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_experiments.py tests_v2/test_walkforward.py tests_v2/test_selection_policy.py tests_v2/test_stability.py
"$V2_PY" -m research_v2.cli grid --partitions train,validation --cost-bps 5,10 --run-id phase_06_grid
"$V2_PY" -m research_v2.cli walk-forward --first-test-year 2011 --last-test-year 2020 --run-id phase_06_wf
"$V2_PY" -m research_v2.cli select --source-run phase_06_grid --provisional --run-id phase_06_selection
```

加通用 guard；run count 从协议自动核对，不打印每条日线。

### 9. Expected outputs

Train/Validation 九模型与对照表、continuous WF 日净值、reset fold 诊断、每折选模 lineage、3×3 参数稳定性表、provisional candidate 或 NO_GO、trial registry 与阶段凭证。

### 10. Acceptance criteria

所有模型只使用允许信息集；WF 时序/费用正确；候选规则可从协议与结果重算且唯一；任何未达门槛情况明确 NO_GO，不因任务要求而挑赢家；holdout 未解封。

### 11. Git checkpoint recommendation

`research(v2): add chronological validation and fixed selection policy`；不在此 checkpoint 宣称已经通过 Final OOS。

## Stage 7 — 成本、Regime、Block Bootstrap 与 Multiple-testing Audit

### 1. Objective

检验收益改善是否耐成本、参数/时期扰动和相关样本不确定性，并在看 holdout 前锁定候选。依赖：Stage 6。

### 2. Files to inspect

Stage 6 grid/WF 摘要与 selection policy；protocol；V2 daily return/ledger schema。方法引用见 §7；不用重新搜索或扩展统计方法列表。

### 3. Files to create

`research_v2/{regimes.py,bootstrap.py,multiple_testing.py}`；`tests_v2/{test_regimes.py,test_bootstrap.py,test_multiple_testing.py,test_model_lock.py}`；`docs/research_v2/{model_lock.json,phase_07.md}`；cost/regime/bootstrap/multiplicity audit tables。

### 4. Files allowed to modify

V2 `experiments.py`、`selection.py`（加入锁定序列化，不改规则）、`stability.py`、`cli.py`、`provenance.py`、`STATUS.md`。任何数学实现修正需 synthetic regression，不改变 threshold/endpoint。

### 5. Files explicitly forbidden to modify

F0；protocol、历史留出数据/收益、前序证据；禁止挑最有利 cost、regime、block length、bootstrap seed 后只报告一个结果；禁止静默扩大候选网格。

### 6. Exact implementation requirements

1. 补齐 `{0,5,10,20,30,50}` bps，在 Train/Validation 分别对全部九模型及对照完整 ledger 重跑，复用已有 hash 一致的 5/10 bps 产物可避免重复。主比较与模型选择始终用协议指定成本；输出 paired ΔSharpe/ΔCAGR、annualized turnover、gross notionals、fees 与成本侵蚀。
2. 成本 break-even 仅报告网格上的正/负区间；可插值但标 `approximate`，不可把六点网格插值当精确容量/冲击模型。
3. Regime 默认四类：SPY 前一 session close 相对截至前一 session 的200日 SMA（above/below-or-equal），以及截至前一 session 的63日日收益波动×sqrt(252) 相对20%阈值（high/low）。d日收益只能用d-1可用信息标注，阈值固定且不为提高效果而调优。
4. Regime 汇总每类 observations、exposure、mean return、vol、Sharpe、turnover/fee贡献和配对差；小于126个 session 标 `LOW_SAMPLE`。分散 regime 日期的收益不是连续投资路径，禁止把拼接 regime 的 MaxDD/CAGR 标成真实连续持有绩效。
5. 辅助固定压力窗口：2008全年、2020-02-01至2020-06-30、2022全年；当前阶段只输出已授权 Train/Validation 中的窗口，2022 等待 Stage 8。这些仅描述，不再选“最难看/最好看”的起止点，不影响选模。
6. **Paired stationary block bootstrap**：按真实对齐的 daily returns 行共同抽样，所有 Weekly、matched Monthly、V1、SPY 用同一个 index path，以保留跨策略相关；连续 block 长度服从均值 L 的几何分布，circular wrapping 明确记录。主 L=20 sessions，敏感性 L∈{5,10,40}，B=10,000，`numpy.random.Generator(PCG64)`，主 seed=20260921，子流由固定 model/family/partition 编号派生，不用 Python hash。
7. 分区分别 bootstrap，不跨 Train/Validation/holdout 边界混抽；不打乱单个 ETF 重新生成假的市场、不把周/月重叠收益当独立样本。stationarity 假设、结构变点及有限 block 数限制必须写报告。
8. 主估计为 `T_hat = Sharpe(W)-Sharpe(control)`；每个共同抽样路径重新算两个 Sharpe 再相减。报告95% paired percentile CI；同法可对 ΔCAGR 作次要描述，drawdown bootstrap 仅探索性。CI 包含相关性与时间依赖近似，不能保证因果解释。
9. 一侧 H0: ΔSharpe≤0 的 bootstrap 检验使用 null-centered statistic：`T0_b = T*_b - T_hat`，`p = (1 + count(T0_b >= T_hat))/(B+1)`；标明它是平滑统计量的渐近 centered bootstrap 检验，不是有限样本精确检验。**禁止把未中心化样本中 ΔSharpe>0 的比例称为 p-value。** 零方差/退化抽样返回 null+原因；有效 replicates<99%时该项 inference 不可用，不能静默丢弃。
10. Validation 主 family 在5 bps、L=20下共18项：9个 W vs matched，9个 W vs V1_SPEC。对全部18个一侧 p 做 Holm step-down FWER 0.05，输出 raw p、adjusted p、family size、方向、endpoint、block/seed。相同 monthly/V1/模型高度相关不允许把 trial 数降为1；无效项以 p=1进入 family，仍报告不可推断。
11. Holm 算法：排序p_(i)，adjusted_(i)=min(1,max_{j≤i}[(m-j+1)p_(j)])，映射回原顺序；ties 稳定。无需引入统计大库；用小型手算基准校验。Train 与历史留出各自另外报告完整 family，不跨 split 拼接 p，也不宣称18项修正已涵盖所有人工先验探索。
12. 其他成本/L/regime 只做公开敏感性描述，不进行择优显著性宣传；若研究者将其作为独立发现，必须计入新的、完整声明的 family 并降低结论级别。记录全部尝试，包括失败/放弃/修改，不只记录赢家。
13. 不强制追加 DSR、PBO、SPA 等新方法以扩大工程面；本阶段已要求 trial ledger + 预注册 endpoint + paired block inference + Holm。若未来另加方法，作为 amendment，不能把未经自相关修正的 DSR 当成独立证明。
14. 在 validation 稳健性报告之后写不可覆盖 `model_lock.json`：protocol/data/code hash、完整已见数据日期 ledger、selected model或NO_GO、选模理由、未来起点规则、OOS horizon、比较模型、成本/统计方法、lock时间。candidate 必须等于 Stage 6 确定性规则结果，不根据 p/regime 临时换人。NO_GO 时保留完整证据。

### 7. Tests to add

- Regime label 使用d-1信息；future poisoning 不改变历史标签；零波动/样本不足标识正确。
- 固定 seed 精确重放，共同 block 保持两条相同序列的 ΔSharpe=0；L=1接近IID结构、几何 block 平均长度与设定相符（固定 seed/容差，避免易抖动测试）。
- 同策略对比退化情况、正/负 effect 的 p方向、无效 replicate比例；中心化 bootstrap 与手算小 fixture 一致。
- Holm fixture p=[0.01,0.04,0.03] 得 adjusted=[0.03,0.06,0.06]；乱序、ties、null、family size不足均处理明确。
- trial ledger包括九模型/固定对照/成本/失败；未知配置拒绝执行；bootstrap不读取未授权 partition。
- model lock拒绝覆盖、hash匹配、模型与规则输出一致；NO_GO不偷偷选择central model代替。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_regimes.py tests_v2/test_bootstrap.py tests_v2/test_multiple_testing.py tests_v2/test_model_lock.py
"$V2_PY" -m research_v2.cli robustness --partitions train,validation --cost-bps 0,5,10,20,30,50 --run-id phase_07_robustness
"$V2_PY" -m research_v2.cli infer --partition validation --bootstrap stationary --blocks 5,10,20,40 --replicates 10000 --seed 20260921 --family weekly_vs_matched_and_v1 --run-id phase_07_inference
"$V2_PY" -m research_v2.cli audit-trials --run-id phase_07_audit
"$V2_PY" -m research_v2.cli lock-model --selection-run phase_06_selection --audit-run phase_07_audit --output docs/research_v2/model_lock.json
```

bootstrap 可先跑 synthetic 少量 replicates 验证，正式 B固定；不得依据 CI 变化反复换 seed。加通用 guard。

### 9. Expected outputs

完整成本表、3×3稳定性/Regime图表、bootstrap CI/p/有效样本数、Holm18项表、尝试次数审计、模型锁或NO_GO、阶段凭证。此时仍无 historical holdout结果。

### 10. Acceptance criteria

成本和换手勾稽；bootstrap保留配对与序列依赖、统计量定义正确；所有18项和所有压力场景公开；候选不可被稳健性结果临时替换；所有锁在holdout打开之前创建并可证明先后关系。

### 11. Git checkpoint recommendation

`research(v2): audit robustness and lock the selected model`。记录该 commit 作为锁定审计锚点；若模型锁包含该提交不可自引用，锁内写前一 source commit+当前文件树hash，提交hash写后续receipt。

## Stage 8 — 历史留出解封与前瞻 Final OOS 门控

### 1. Objective

在不重新选模的情况下评价历史尾段，并建立真正前瞻 OOS 的可执行流程；诚实处理尚未产生的数据。依赖：Stage 7模型锁，包括合法NO_GO。

### 2. Files to inspect

`model_lock.json`、protocol/dataset manifest、Stage 7 receipt、V2 selection/inference APIs；只在门控核验后读取historical holdout收益。

### 3. Files to create

`research_v2/{oos.py,forward_data.py}`；`tests_v2/{test_oos_gate.py,test_oos_replay.py}`；`docs/research_v2/{OOS_STATUS.md,phase_08.md}`；holdout receipt、exposure ledger、forward registration/append manifest。

### 4. Files allowed to modify

V2 `cli.py`、`provenance.py`、`STATUS.md`。forward快照导入/revision检查放新增`forward_data.py`，通过已锁定接口调用`data.py`，不改其归一化逻辑。冻结`model_lock.json`不改；状态追加到新的receipt。

### 5. Files explicitly forbidden to modify

F0；protocol/model lock/旧快照/候选排名；禁止根据holdout换参数、重定起点、删坏年份、换成本或将该段改称“研究者未见”。禁止启动Paper或创建自动交易任务。

### 6. Exact implementation requirements

1. `evaluate-holdout`核验锁时间、source/data/protocol hash、Stage 7已完成、trial审计无遗漏；先append一次性解封事件，再计算策略结果。中断重试只能相同输入/lock，不允许换候选后复用同一实验。
2. historical holdout 2021-01-01至2026-09-18：主报告锁定candidate、matched、V1_SPEC、SPY；九模型固定网格可作为完整公开的描述性附录，不能在其上重选。对照成本仍全部固定，2022压力窗口在此补齐。
3. 历史网格18项主family依Stage 7计算，报告选择前已锁定candidate的结果及全网格修正结果；所有历史统计标`RETROSPECTIVE`，不能通过Bonferroni/Holm“修复”研究者已经见过V1历史的事实。
4. 同时核对存档V1的输入/运行环境来源；只有能证明精确输入和行为相同时才给archive replay PASS，否则保留旧产物不动，输出可比V1 bridge与未确认项。
5. 若NO_GO：历史九模型附录可完成，结论为没有通过预注册筛选的candidate；forward状态`NOT_SCHEDULED_NO_GO`。不可用holdout里赢家复活模型。
6. 若有candidate：先注册前瞻起点，严格晚于模型锁时间及exposure ledger所有已见行情时间。第一信号close不得早于锁定，执行为其下一session open；第一成交以前全现金。为保持可比，各策略共享forward scored起点，Monthly在自己的下一合法事件成交。
7. future价格无法在今天回测。`forward-status`默认只验证数据覆盖/完整性/哈希，不展示期间Sharpe/CAGR或发出策略表现判断，不创建监控automation。OOS成熟条件固定为达到24个自然月且至少504sessions，期间技术失败/缺数可记录，不按盈亏提前停或选新终点。
8. 新行情只写`research_v2/data/forward/<snapshot_id>/`，独立来源manifest；不更新旧缓存/冻结snapshot。供应商重新下载可能改变历史复权数值：重叠期逐ETF检查factor，只有可证明每只ETF为单一比例缩放且重叠returns/open-close关系不变时可映射到冻结单位；非比例历史修订fail并保留两版本，不静默覆盖或改变旧信号。记录此近似与数据供应商限制。
9. 前瞻选择固定后仅跑该Weekly、其matched、V1_SPEC、SPY及预注册cost sensitivities，不能在新数据上重开九模型选拔。成熟时主5bps两项假设（vs matched、vs V1）用同一paired bootstrap，并对2项做Holm；其余敏感性不参与择优。
10. 未成熟时允许实现/单测验收通过并输出`ENGINEERING_READY_RESEARCH_PENDING`；统计研究完成度必须单独为pending。不得为了交付文件把Final OOS标为PASS。
11. 若解封后发现实现bug，旧产物保留，标`INVALIDATED_AFTER_EXPOSURE`，新增amendment并重跑工程；修复后historical只算已见诊断，新模型需重新注册真正未来OOS，不能回滚exposure ledger。

### 7. Tests to add

- 无锁/错hash/未审计/错误候选无法解封；解封后不能覆盖记录或改变模型。
- maturity边界：仅504sessions但未24个月不够，仅24个月但样本不足不够；两条件均满足才允许evaluate-forward。
- 合成未来数据验证信号时序、现金起点、成本、append-only、revision拒绝和可接受比例复权映射。
- NO_GO无隐式candidate；pending不产出虚假Sharpe；重复运行确定性但不算新的独立OOS。
- 解封后bug保留invalidated证据；训练进程不能反向导入holdout输出。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2/test_oos_gate.py tests_v2/test_oos_replay.py
"$V2_PY" -m research_v2.cli evaluate-holdout --lock docs/research_v2/model_lock.json --partition historical_holdout --run-id phase_08_holdout
"$V2_PY" -m research_v2.cli register-forward --lock docs/research_v2/model_lock.json --horizon-months 24 --min-sessions 504 --run-id phase_08_forward
"$V2_PY" -m research_v2.cli forward-status --registration-run phase_08_forward --metadata-only
```

这些命令在未来实施本阶段时，锁定条件满足即可执行，无需在每次可逆本地研究操作前重复请求许可。今天没有forward数据，**不运行真实`evaluate-forward`**；接口必须以synthetic测试证明成熟/未成熟路径正确。将来仅在数据门槛满足后使用`evaluate-forward --registration-run phase_08_forward --run-id final_oos_01`，不得自动提前调用。

### 9. Expected outputs

一次性历史留出报告及完整标签、锁定模型与对照表现、forward registration、明确的pending/no-go状态、数据revision日志（若有）、阶段凭证。

### 10. Acceptance criteria

历史评估没有反向改变候选；曝光与运行时间链完整；OOS语言与实际信息边界一致。**工程验收可以通过，Final OOS证据可以仍未完成**，二者不得混合。真实统计结论必须等到足够未来数据出现。

### 11. Git checkpoint recommendation

`research(v2): evaluate historical holdout and register prospective OOS`；不得创建暗示真OOS成功的tag。若为NO_GO，提交标题改为明确记录NO_GO研究结论。

## Stage 9 — 研究报告、离线复现与冻结交付

### 1. Objective

把全部证据整合成可复现、可解释、可交接的研究包，证明V1/Paper未被影响，并如实区分策略结果与工程完成度。依赖：Stage 8；真实前瞻OOS成熟不是当前软件交付的前提。

### 2. Files to inspect

阶段1–8 receipts与manifest、模型锁、OOS状态、汇总结果表；读取各阶段固定证据路径，不重扫原仓库或重新探索参数。

### 3. Files to create

`research_v2/{reporting.py,replay.py}`；`tests_v2/{test_reporting.py,test_replay.py,test_end_to_end.py}`；`docs/research_v2/{final_report.md,release_manifest.json,RUNBOOK.md,phase_09.md}`；最终report figures、replay diff和protected-files报告。

### 4. Files allowed to modify

V2 `cli.py`、`provenance.py`（仅交付索引）、`STATUS.md`；新增报告文件。若需修正计算错误，退回对应阶段、补测试并按解封状态失效处理，不能在reporting层改数值。

### 5. Files explicitly forbidden to modify

F0；所有已冻结数据/协议/锁/运行产物；禁止覆盖不利报告、改V1结果使其与V2“统一”、部署或对接Paper。

### 6. Exact implementation requirements

1. 报告回答问题、证据、限制和后续决策，而非源码导览。固定包括：九模型完整表；paired Monthly与V1对比；Train/Validation/historical holdout/前瞻状态；daily NAV/MaxDD；成本曲线、turnover、gross notionals；WF；3×3参数稳定性；regime；bootstrap CI；18项multiplicity；数据/代码版本；未通过/失败记录。
2. 用统一日期和5bps主结果画图，历史与前瞻分界清楚；gross与net明确标识；不能放“最佳全历史策略”图冒充选模前证据。所有CSV保留数值列，展示百分比只在report层格式化。
3. 最终结论分两轴：engineering=`PASS/BLOCKED/FAIL`；research=`NO_GO / HISTORICAL_EVIDENCE_ONLY / INCONCLUSIVE / PROSPECTIVE_SUPPORTED / PROSPECTIVE_NOT_SUPPORTED / PENDING_DATA`，附精确原因。工程PASS不代表alpha存在。
4. 只有预注册candidate在成熟Final OOS主5bps对两基线的ΔSharpe均≥0.10、两项Holm-adjusted p≤0.05、10bps paired ΔSharpe>0、MaxDD相对matched恶化≤5个百分点，且此前validation稳定性gate通过，才能标`PROSPECTIVE_SUPPORTED`。CI和低功效限制照报。未过显著性且CI跨零一般为`INCONCLUSIVE`，不能把“不显著”等同于“证明无效”；明显负向/经济门槛不达如实说明。
5. 这些经济/统计门槛在Stage 1已进入协议；Stage 9不能第一次看结果后添加。当前预计只能给historical/no-go/pending结论，不能预承诺改善。
6. 提供机器可读release manifest，引用所有hash与运行路径；真实行情不入Git时给出受控本地位置/归档索引和校验命令，不能暗示只clone仓库即可获得未提交数据。
7. 从锁定数据/协议/模型/源码在全新V2输出目录完全离线重放；已解封的历史holdout重放标`REPLAY_NOT_NEW_OOS`。相比源运行canonical数值结果、signals、trades、metrics、bootstrap samples/digests完全一致；环境跨平台差异允许事先声明浮点容差并同时报告bitwise与numeric两种结果，不能事后放宽。
8. 最终执行完整tests_v2；在隔离副本重跑Stage 5的原V1/Paper suites，确认基线未变。保护报告包括tag ID、所有原文件hash/mode/mtime、原缓存与结果hash、旧state变更溯源；不写任何旧状态。
9. `RUNBOOK.md`给出每个子命令、依赖顺序、失败恢复、重复运行新run_id、数据缺失、NO_GO/OOS等待说明。不会默认启动Paper、联网刷新或创建定时任务。

### 7. Tests to add

- report表与原metrics精确一致、所有9模型/6成本和失败行可见，无选择性省略。
- OOS pending时不显示“通过”；负结果不被改写为改善；V1 archived与unified指标标签不会混淆。
- 端到端synthetic从snapshot→signals→ledger→selection→bootstrap→holdout→report可重放。
- 新run_id允许重放，原run_id拒绝覆盖；canonical hash忽略且只忽略声明的envelope字段。
- 最终AST/import依赖检查、网络禁用和文件写入边界测试仍通过。

### 8. Commands Codex should run

```bash
"$V2_PY" -m pytest -q -p no:cacheprovider tests_v2
"$V2_PY" -m research_v2.cli report --lock docs/research_v2/model_lock.json --run-id phase_09_report
"$V2_PY" -m research_v2.cli replay --release-manifest docs/research_v2/release_manifest.json --offline --run-id phase_09_replay
"$V2_PY" -m research_v2.cli compare-replay --source-manifest docs/research_v2/release_manifest.json --replay-run phase_09_replay
"$V2_PY" -m research_v2.cli legacy-regression --refs research-v1,3f708f888277a6564ea6db204dafde95c2a548c7 --offline --data-partition train --run-id phase_09_regression
"$V2_PY" -m research_v2.cli guard --manifest docs/research_v2/baseline_manifest.json
git diff --check
git status --short
```

`report`先生成release manifest供replay读取，replay验证结果通过后新增独立verification receipt并链接到最终报告，避免manifest递归hash自身或覆盖原锁。

### 9. Expected outputs

`final_report.md`、必要图表/数值表、RUNBOOK、release manifest、离线复现证明、旧文件保护证明、全部测试/阶段凭证，以及清楚的engineering/research双状态。

### 10. Acceptance criteria

所有必要测试与离线重放通过；V1/Paper冻结被证据支持；完整披露九模型/成本/统计调整；没有真实未来数据就不宣称Final OOS已完成。阶段成功的衡量是研究有效可复现，不要求Weekly胜出。

### 11. Git checkpoint recommendation

`research(v2): publish reproducible weekly momentum research package`。可建议新增`research-v2-weekly-initial` tag，但不移动任何旧tag、不自动推送。若前瞻尚待验证，tag注释明确`prospective OOS pending`；NO_GO也可以冻结完整研究包。

## 4. Recommended Codex execution order

推荐严格顺序：**1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9**。每阶段独立作为一个Codex任务执行，引用同一工作分支和上一阶段receipt。独立是指可单独验收和交接，不代表可以跳过依赖。

| 执行单元 | 先决条件 | 结束后应具备的能力 | 不应提前做的事 |
|---|---|---|---|
| 1 | 只读核验基线 | 隔离环境、研究协议、保护措施 | 计算九模型收益 |
| 2 | 协议已锁 | 冻结数据、日历、partition | 看holdout策略表现 |
| 3 | 数据可信 | 周信号与执行时序可信 | 修改旧Monthly函数 |
| 4 | 时序可信 | 账本/MTM/成本可信 | 未验证会计即跑大批结果 |
| 5 | 账本可信 | 可归因的Monthly比较、V1桥接 | 用不可比的V1指标宣布频率改善 |
| 6 | 比较口径可信 | 九模型验证与因果WF | 看holdout挑赢家 |
| 7 | 选择程序固定 | 成本/统计审计、candidate锁 | 调参迎合显著性 |
| 8 | candidate已锁 | 历史留出与前瞻门控 | 无新数据宣称Final OOS成功 |
| 9 | 阶段证据齐全 | 可复现报告与交付 | 对接Paper/实盘 |

不需要自行开多个agent。后续若用户明确要求并行，可以在Stage 1接口固定后并行开发相互独立的synthetic测试/报告模板；Stage 2–8的真实研究读取、选模、解封顺序仍严格串行。不要让两个任务同时写protocol、STATUS、trial registry或model lock。

每阶段任务可使用以下完整指令模板（把N换成阶段号即可）：

> 执行 quant-ETF 的 CODEX_EXECUTION_PLAN.md 中 Stage N。先读全局边界、STATUS、该阶段Files to inspect和上一阶段receipt，不重新探索整个仓库。仅修改本阶段允许的新V2文件；F0全部冻结。按Exact implementation requirements完成实现、Tests和Commands，给出Expected outputs及Acceptance criteria逐项证据。真实运行保持离线与指定partition；不得越阶段读取holdout。完成后生成本阶段receipt/交接说明，运行冻结guard并给出Git checkpoint；如遇真实阻塞，保留证据和已完成工作，不声称PASS，也不要修旧V1/Paper文件。阶段代码错误可在允许的V2范围内修复后重测，不重复询问已授权的可逆本地实施步骤。

阶段交付回复仅需要：完成内容、测试/结果路径、保护验证、未完成项/原因、下一阶段入口。不要把数千行日志或整个日收益矩阵送回上下文。

## 5. 哪些任务最消耗 Codex reasoning / token

以下是**本项目任务复杂度估计**，不是任何Codex模型定价、剩余额度或官方token保证。大量数值重跑主要消耗机器时间；重复读仓库、回传巨型CSV、反复解释规则才会额外浪费上下文。

| 优先级 | 阶段 / 难点 | Reasoning需求 | Token风险 | 如何节省而不降低验收 |
|---|---|---|---|---|
| 1 | Stage 4：overnight/intraday会计、自融资、费用与turnover | 很高 | 高 | 先固定手算fixture和不变量，再写引擎；只看勾稽失败的event |
| 2 | Stage 3 + Stage 2日历：complete-week、自然月anchor、未来信息边界 | 很高 | 高 | 直接使用列出的边界日期、prefix/future poisoning测试，避免口头反复讨论 |
| 3 | Stage 5：V1原行为与统一会计、频率对照、隔离回归 | 很高 | 很高 | 只读取定位函数；用A→B→C→D bridge分类，不试图抹平所有差异 |
| 4 | Stage 6：nested WF、真实状态延续、selection规则 | 很高 | 高 | 先手画两折日期/事件并做测试，不从全历史输出倒推设计 |
| 5 | Stage 7：成对bootstrap、中心化检验、multiplicity | 很高 | 高 | 固定方法；小fixture验证后批量运行；回传统计摘要和hash |
| 6 | Stage 8：OOS曝光与锁、forward revision | 高 | 中高 | 先写状态机和失败路径；真实未来数据等待无需持续运行Codex |
| 7 | Stage 1：隔离/协议/接口 | 中高 | 中 | 本文即规格，不二次架构发散 |
| 8 | Stage 2数据搬运/校验的常规部分 | 中 | 中低 | 固定schema与manifest，避免读出所有价格 |
| 9 | Stage 9：报告装配、离线重放、交接 | 中 | 中低 | 报告由既有机器结果生成，不重新统计或搜索模型 |

**剩余额度优先投入建议：**不要违反依赖顺序跳跑阶段。先用较少上下文完成Stage 1–2的隔离/输入；把较多推理预算留给Stage 3–5的核心实现与Stage 6–7的研究有效性。若只能完成一段，优先交付经过验证的Stage 1–5基础设施，停止在validation/holdout之前；尚未开始的统计研究必须标为未完成。不要为了用完额度先跑九模型“看哪个最好”，这会污染后续研究设计。

## 6. 发布前审计清单与错误处理

| 检查 | 必需证据 |
|---|---|
| Research V1仍冻结 | research-v1对象/commit不变，原tracked代码/结果hash一致 |
| Paper仍隔离 | HEAD基线文件不变，无broker连接/订单，旧state无本任务写入；隔离回归通过 |
| 周期完整且无未来 | XNYS完整period证明、event lineage、prefix/future poisoning测试 |
| 数据可复现 | snapshot、session calendar、依赖与protocol hash、源路径和数据修订说明 |
| 九模型范围受控 | 固定grid、trial ledger、全部失败/额外尝试披露 |
| 频率归因可解释 | 九个matched Monthly，加V1_SPEC与frozen oracle bridge |
| 账本可信 | 自融资根残差、无现金clamp、每日NAV/逐笔交易/成本勾稽 |
| 换手未混淆 | 含CASH目标单边turnover、ETF gross货币额/比率、年化分母分别明确 |
| 选择未泄漏 | train/validation/年度WF边界、候选锁时间、holdout解封记录 |
| 统计审计完整 | paired blocks、固定B/L/seed、18项family、Holm、限制说明 |
| OOS表述准确 | 历史曝光标签，forward maturity proof或PENDING_DATA/NO_GO |
| 结果可交付 | 代码/输入锁、runbook、报告、离线replay和phase receipts |

遇到缺价、非法calendar、hash漂移、未授权路径写入、统计量不可定义，应输出有上下文的错误并终止对应run，保留失败目录和trial记录。不能换参数、补造数据、静默drop样本、修改已有结果或提高tolerance使其通过。

阶段间改动受保护计算接口时，应重新执行受影响的已有V2测试；全部检查已通过且无新变化，不需要无限重复全套测试。模型锁之后允许新增report/OOS编排代码；锁定校验针对明确列举的**计算模块文件树hash**（data normalization、calendar、signals、schedule、ledger、engine、metrics、selection、bootstrap定义），以及protocol/dataset，不要求后续HEAD永远等于锁前HEAD。任何这些核心模块变动必须记录锁失效/再验证；不要用正常新增报告文件导致的HEAD变化误报研究改变，也不要漏审真实计算变化。

`register-forward`对NO_GO生成不可调度状态凭证，不报假成功；对候选生成registration。`phase_receipt`由各阶段命令日志/测试结果汇编，含当前实际code hash；可由provenance中的统一receipt工具生成，不允许手写“测试通过”。本文命令的固定run_id用于首次执行；重复验证必须使用带递增suffix的新run_id并更新索引，原目录保留。

## 7. 方法依据与适用边界

本方案的窗口、分区、成本网格、选模与经济阈值是**本项目预注册设计选择**，不是文献证明的最优参数。以下原始/官方资料只支持对应方法，不提供本项目Weekly能够胜出的证据：

- [exchange_calendars 官方仓库](https://github.com/gerrymanoim/exchange_calendars)：session、open/close、交易所日历的实现接口依据；安装时锁定实际验证版本，正式运行冻结导出日历。
- [Politis & Romano, The Stationary Bootstrap，Stanford 原始技术报告页面](https://statistics.stanford.edu/technical-reports/stationary-bootstrap)：对相关时间序列采用随机长度连续块重采样的依据。
- [arch 官方 StationaryBootstrap 文档](https://arch.readthedocs.io/en/stable/bootstrap/generated/arch.bootstrap.StationaryBootstrap.html)：seed和多序列共同重采样的实现参照；本方案不要求新增arch依赖。
- [statsmodels 官方 multipletests 文档](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html)：Holm多重检验修正的接口/方法参照；本方案用独立小函数与手算测试即可，不要求引入整个库。
- [Bailey & López de Prado, The Deflated Sharpe Ratio，作者论文](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)：解释择优回测、多个尝试和非正态对Sharpe解释的影响；本方案不把DSR当成已执行统计检验。

本阶段的完成标准是**得到可信、可重复、边界清楚的研究判断**。允许Weekly劣于Monthly，允许交易成本吃掉收益，允许统计不显著；不得把“开发成功”写成“策略已证明有效”。
