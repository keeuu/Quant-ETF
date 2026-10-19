# Research Protocol — Research V2 Weekly Momentum

> 本协议是实施前的预注册合同文档，已严格锁定。
> Protocol SHA-256: `a35fe1e50f90b537bd543e9d41a78eff3d9ae9991c362adc603f42432a23e9fb`
> 机器可读源文件: `research_v2/protocol.json`
> 锁定凭据: `docs/research_v2/protocol_lock.json`

## 1. 资产池与投资范围 (Universe)

固定 8 只 ETF，顺序严格锁定（用于同分 tie-break）：
1. `SPY` (S&P 500)
2. `QQQ` (Nasdaq 100)
3. `IWM` (Russell 2000)
4. `EFA` (MSCI EAFE)
5. `EEM` (MSCI Emerging Markets)
6. `GLD` (Gold)
7. `TLT` (20+ Year Treasury)
8. `IEF` (7-10 Year Treasury)

**约束条件**:
- **Long Only**: 仅允许多头。
- **No Leverage**: 杠杆上限 1.0。
- **No Short**: 严禁空头。
- **Top 3 Equal Weight**: 入选资产之间等权；不足 3 只时全额配置入选资产，不留空槽现金；0 只入选时全额持有现金。
- **Cash Return**: 现金利息设为 0.0。
- **Initial Capital**: 100,000.0 美元。

---

## 2. 策略模型空间 (9 个固定模型)

由 3 个动量自然月回溯期与 3 个趋势周均线期交叉组成固定 9 个模型，禁止在测试中根据表现增删或调参：

| 模型 ID | 动量回溯窗口 ($L$ 月) | 趋势均线窗口 ($K$ 周) | 动量跳过周期 |
|---|---|---|---|
| `W_M06_T36` | 6 个自然月 | 36 个完整周 | 1 个完整周 |
| `W_M06_T40` | 6 个自然月 | 40 个完整周 | 1 个完整周 |
| `W_M06_T44` | 6 个自然月 | 44 个完整周 | 1 个完整周 |
| `W_M09_T36` | 9 个自然月 | 36 个完整周 | 1 个完整周 |
| `W_M09_T40` | 9 个自然月 | 40 个完整周 | 1 个完整周 |
| `W_M09_T44` | 9 个自然月 | 44 个完整周 | 1 个完整周 |
| `W_M12_T36` | 12 个自然月 | 36 个完整周 | 1 个完整周 |
| `W_M12_T40` | 12 个自然月 | 40 个完整周 | 1 个完整周 |
| `W_M12_T44` | 12 个自然月 | 44 个完整周 | 1 个完整周 |

- **动量定义**:
  $$\text{momentum}_L(t) = \frac{\text{AdjClose}[s_{t-1}]}{\text{AdjClose}[a_L(t)]} - 1$$
  其中 $s_{t-1}$ 为上一完整周最后 session；$a_L(t)$ 为不晚于当前信号 session 回溯 $L$ 个自然月的最近 XNYS session。
- **趋势过滤**:
  $$\text{SMA}_K(t) = \frac{1}{K} \sum_{i=0}^{K-1} \text{weekly\_adj\_close}_{t-i}$$
  合规资格条件: $\text{weekly\_adj\_close}(t) > \text{SMA}_K(t)$。
- **无额外过滤**: 不设 $\text{momentum} > 0$ 强制过滤。

---

## 3. 固定对照设计 (Cadence Controls)

1. **Matched Monthly Controls (`M_MATCH_<model_id>`) × 9**:
   采用与周模型完全相同的周特征函数，但仅在月末最后完整周收盘产生决策，次月首个 session 开盘执行。用于隔离决策频率与特征定义差异。
2. **V1 Spec Unified (`V1_SPEC_UNIFIED`) × 1**:
   V1 原始策略（12M-1M 动量、10M SMA、月度再平衡、Top 3），在 V2 统一会计与日历引擎中重放。
3. **V1 Frozen Oracle (`V1_FROZEN_ORACLE`) × 1**:
   隔离子进程中运行原始 `research-v1` tag 代码，审计历史行为与差异。
4. **SPY Buy & Hold (`SPY_BUY_HOLD`)**:
   外部被动基准，相同起止日与初始资金，不参与候选选择。

---

## 4. 成本与自融资会计模型

- **基准成本**: 每笔 ETF 买卖绝对名义额的 5 bps（双边各 5 bps）；CASH 调整不计费用。
- **压力测试成本**: `[0, 5, 10, 20, 30, 50]` bps。
- **自融资方程求解**:
  在调仓日开盘，求解唯一 $V_{\text{post}} \in [0, V_{\text{pre}}]$：
  $$V_{\text{post}} + c \sum_i |w_i V_{\text{post}} - a_i| = V_{\text{pre}}$$
  其中 $a_i = q^{\text{old}}_i p^{\text{open}}_i$ 为开盘前各持仓市值。使用有界二分法求解，严禁负现金 clamp。

---

## 5. 数据分区与防窥探门槛

| 分区 | 区间 | 用途与限制 |
|---|---|---|
| **Warm-up** | 2005-01-03 起 | 仅供初始技术指标计算，不计入回测收益 |
| **Train** | 2006-02-01 至 2015-12-31 | 探索、调试与训练内 Walk-Forward |
| **Validation** | 2016-01-01 至 2020-12-31 | 模型选择与参数鲁棒性评估（主决策区间） |
| **Historical Holdout** | 2021-01-01 至 2026-09-18 | 策略锁定后一次性解锁；标明已受 V1 暴露 |
| **Prospective Final OOS** | 未来数据 | 策略完全锁定后，需满足至少 504 sessions 且 24 个自然月 |

---

## 6. 模型选择与推断规则

- **入选准则**:
  - Validation 集相对对应 `M_MATCH` 的 $\Delta\text{Sharpe} \ge 0.10$。
  - 相对 V1 Unified 的 $\Delta\text{Sharpe} > 0$。
  - 在 10 bps 压力测试下 $\Delta\text{Sharpe} > 0$。
  - Max Drawdown 相对 Control 恶化不超过 0.05。
  - 5 年中有至少 60% 年份（即 $\ge 3$ 年）超额收益为正。
  - 3x3 参数正交邻域中至少 50% 邻居保持正超额收益。
- 若无模型满足条件，输出 `NO_GO`，严禁调参凑选。
