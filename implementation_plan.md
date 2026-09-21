# ETF 动量交易系统实施计划 (ETF Momentum Trading System)

本项目旨在搭建一个简单、低频、可靠、可长期扩展的 ETF 动量量化交易系统。系统严格限定在模拟交易（Paper Trading）与历史回测环境，严禁连接 Live 实盘。

---

## 策略设计规范

### 1. 资产池 (Universe)
固定 8 只代表性大类资产 ETF：
- 股票/权益：`SPY` (标普500), `QQQ` (纳斯达克100), `IWM` (罗素2000), `EFA` (发达市场), `EEM` (新兴市场)
- 大宗/商品：`GLD` (黄金)
- 债券/固收：`TLT` (20年+美债), `IEF` (7-10年美债)

### 2. 信号与指标定义（严格杜绝未来函数）
系统按月调仓，执行日为每月第 1 个交易日（调仓日 $T$）。
信号计算**仅使用调仓日前已经闭盘的月度数据**，即截至上一自然月最后一个交易日（月末 $t-1$）的复权收盘价：
- **12-1 Momentum (经典动量)**：
  衡量 $t-12$ 月末到 $t-1$ 月末的累计收益率（跳过最近 1 个月，即不使用 $t$ 月初至今的实时波动，且经典定义跳过最近 1 个月可有效规避短期反转效应与微观流动性噪声）：
  $$\text{Mom}_{12-1} = \frac{P_{t-1}}{P_{t-12}} - 1$$
- **10-Month SMA (趋势过滤)**：
  计算 $t-10$ 到 $t-1$ 连续 10 个月末收盘价的简单移动平均：
  $$\text{SMA}_{10} = \frac{1}{10} \sum_{i=1}^{10} P_{t-i}$$
  **过滤规则**：仅保留满足 $P_{t-1} > \text{SMA}_{10}$ 的 ETF。若 $P_{t-1} \le \text{SMA}_{10}$，则不论动量多高均被淘汰。
- **Top 3 资产配置**：
  在通过趋势过滤的资产中按 $\text{Mom}_{12-1}$ 从高到低排序，等权配置：
  - $\ge 3$ 个通过：选取前 3 名，各占 $1/3$ 权重；
  - $2$ 个通过：各占 $50\%$ 权重；
  - $1$ 个通过：占 $100\%$ 权重；
  - $0$ 个通过：$100\%$ 保持现金（Cash）。

---

## 系统架构与模块设计

```
quant-ETF/
├── README.md                  # 项目使用与架构说明
├── requirements.txt           # 核心依赖清单
├── .env.example               # Alpaca API 配置模板
├── config/
│   └── config.py              # 全局参数：资产池、交易费率、路径、API配置
├── data/
│   └── cache/                 # 历史行情本地缓存 (CSV/Parquet)
├── logs/
│   ├── app.log                # 结构化运行与决策审计日志
│   └── rebalance_state.json   # 幂等状态记录（防止当月重复调仓）
├── src/
│   ├── data/
│   │   ├── fetcher.py         # 历史行情获取、复权清洗、本地缓存
│   │   └── validator.py       # 数据完整性与 NaN/缺失检查
│   ├── signals/
│   │   ├── momentum.py        # 12-1 动量计算
│   │   ├── trend.py           # 10M SMA 计算
│   │   └── selector.py        # 趋势过滤与 Top 3 选择器
│   ├── portfolio/
│   │   └── allocator.py       # 权重分配、目标股数计算（含现金缓冲）
│   ├── broker/
│   │   └── alpaca_client.py   # Alpaca Paper API 封装与强制 Paper 保护
│   ├── execution/
│   │   └── rebalance.py       # 调仓执行器（幂等判断、dry-run、先卖后买）
│   ├── backtest/
│   │   ├── engine.py          # 严格防未来的历史回测引擎
│   │   ├── metrics.py         # CAGR, Sharpe, MaxDD, Turnover 等指标
│   │   └── plotter.py         # 策略 vs SPY 净值曲线生成
│   └── utils/
│       └── logger.py          # 决策与执行审计日志记录
├── tests/
│   ├── test_momentum.py       # 12-1 动量测试
│   ├── test_trend.py          # 10M SMA 测试
│   ├── test_selector.py       # 过滤与 Top 3/现金回退测试
│   ├── test_allocator.py      # 权重与分配测试
│   ├── test_lookahead.py      # 防未来函数严格测试
│   ├── test_idempotency.py    # 幂等性与防重复下单测试
│   └── test_safety.py         # Live 拦截安全测试
└── scripts/
    ├── run_backtest.py        # 回测入口（Mode A）
    └── run_paper.py           # Paper Trading 调仓入口（Mode B）
```

---

## 关键安全机制与设计细节

1. **强制 Paper-only 保护**：
   - `AlpacaClient` 初始化时硬性校验：`paper=True`；
   - 检查 API Base URL，若包含 `api.alpaca.markets` 且不包含 `paper-api`，立即抛出 `RuntimeError` 终止程序；
   - 检查环境变量中的 `APCA_API_BASE_URL`，默认锁定 `https://paper-api.alpaca.markets`。
2. **幂等性与防重复调仓 (Idempotency)**：
   - 状态持久化到 `logs/rebalance_state.json`；
   - 每次运行前比对 `last_rebalanced_month == current_month`（格式如 `2026-09`）；若已调仓且未指定 `--force`，则输出日志并直接退出。
3. **Dry-Run 默认保护**：
   - `scripts/run_paper.py` 默认仅生成并打印 Rebalance Plan；
   - 只有显式传入 `--execute` 参数，才真正向 Alpaca 提交订单。
4. **订单执行安全**：
   - 先执行「卖出/减仓」订单，释放资金后再执行「买入/加仓」订单；
   - 预留小额现金缓冲（如 $1\%\sim 2\%$），防止市价单波动导致购买力超支；
   - 下单前查询 Alpaca 账户当前挂单（Open Orders），若有未成交订单则报警提示，不盲目加单。
5. **数据防泄漏与复权处理**：
   - 数据源使用 Yahoo Finance (`yfinance`) 获取日度复权价格（Adjusted Close），清洗对齐日期；
   - 月度采样提取月末收盘价，严格确保月度指标使用历史切片。

---

## 验证与测试计划 (Verification Plan)

### 1. 自动化单元测试矩阵 (`pytest`)
- `tests/test_momentum.py`: 手工构造固定数值序列，验证跳过最近1个月收益计算准确性。
- `tests/test_trend.py`: 验证 10M SMA 计算及低于均线剔除逻辑。
- `tests/test_selector.py`: 测试 0/1/2/3/多资产通过时的排序与等权分配。
- `tests/test_allocator.py`: 测试股数计算与权重之和。
- `tests/test_lookahead.py`: 时间序列切片测试，确保 $T$ 时刻计算决不读取 $T$ 及未来数据。
- `tests/test_idempotency.py`: 模拟连续运行两次，验证第二次被正确拦截；验证 `--force` 可跳过拦截。
- `tests/test_safety.py`: 模拟 live 配置注入，验证抛出致命异常并拒绝初始化。

### 2. 回测验收 (Mode A)
- 运行 `python scripts/run_backtest.py`
- 时间范围：2015-01-01 至最新数据
- 包含：交易成本 5 bps、基准 SPY 对比、完整指标表与净值曲线输出。

### 3. 实盘模拟验收 (Mode B)
- 运行 `python scripts/run_paper.py` (dry-run)，验证打印账户持仓、目标组合与调仓计划。
- 在配置好有效 Alpaca Paper API Key 时，运行 `--execute` 验证模拟挂单、状态保存与日志落盘。
