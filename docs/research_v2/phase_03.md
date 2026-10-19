# Stage 3 验收凭证与执行总结

> 阶段名称: Stage 3 — 周信号、选择器与防未来执行日程  
> 完成时间: 2026-09-21  
> 阶段状态: **PASS**

---

## 1. 核心研究公式与执行规则确认

### 1.1 Weekly Momentum 精确公式
设当前完整周为 $t$（以周最后一个真实 XNYS session $s_t$ 收盘为准）：
- $s_{t-1}$：上一完整周最后一个真实 XNYS session（严格跳过最近一个完整周）。
- $a_L(t)$：不晚于 $s_t - \text{DateOffset}(\text{months}=L)$ 的最近有效 XNYS 交易日（自然月回溯，绝非硬编码 26/39/52 周）。
- 动量计算公式：
  $$\text{momentum}_L(t) = \frac{\text{AdjClose}[s_{t-1}]}{\text{AdjClose}[a_L(t)]} - 1.0, \quad L \in \{6, 9, 12\}$$
- **Lineage 逐周追溯**: 记录 `(signal_session, numerator_date, numerator_price, denominator_date, denominator_price, momentum)`。

### 1.2 Trend Filter 精确公式
- 周收盘价序列: 每个完整周最后 session 的复权收盘价。
- $K \in \{36, 40, 44\}$ 周：
  $$\text{SMA}_K(t) = \frac{1}{K} \sum_{i=0}^{K-1} \text{weekly\_adj\_close}_{t-i}$$
  包含当前完整周，必须要求完整 $K$ 个周观察。
- 通过判定: $\text{weekly\_adj\_close}(t) > \text{SMA}_K(t)$（**严格大于**，等于则 FAIL；无 $\text{momentum} > 0$ 附加过滤）。

### 1.3 确定性选拔 (Selector)
- 选拔顺序：Trend Filter $\to$ Momentum 降序 $\to$ Top 3。
- 并列同分规则：严格按 Universe 固定顺序打破并列（`SPY > QQQ > IWM > EFA > EEM > GLD > TLT > IEF`）。
- 权重分配：0 个通过 $\to$ 100% 现金；1 个通过 $\to$ 100%；2 个通过 $\to$ 各 50%；$\ge 3$ 个通过 $\to$ Top 3 各 1/3。
- 约束：长头无空头、无杠杆（$\sum w_i \le 1.0$）、无止损、无波动率定目标。

### 1.4 执行日程 (Schedule)
- 信号生成在当前完整周最后一个 XNYS session 收盘时刻（`signal_close_ts`）。
- 执行成交在下一个有效 XNYS session 开盘时刻（`execution_open_ts`）。
- 严格满足 $\text{signal\_close\_ts} < \text{execution\_open\_ts}$，严禁同日收盘成交，严禁执行开盘价参与信号计算。
- Monthly Matched Cadence：严格使用截至月末收盘已可用的最新周信号，严禁月末半周产生新信号。

---

## 2. 防未来因果测试 (Look-Ahead Audit)

在 [`tests_v2/test_lookahead.py`](file:///Users/zhangzeyuan/Projects/quant-ETF/tests_v2/test_lookahead.py) 中进行了 5 组严格因果性测试，全部通过：

| 测试项 | 验证内容 | 结果 |
|---|---|---|
| **Prefix Invariance** | 截断到测试日计算的过去信号与全量数据同日计算完全相同（0 差异） | **PASS** |
| **Future Poisoning** | 人为篡改信号日之后的未来行情，过去的动量、趋势、排名和目标权重 100% 不变 | **PASS** |
| **Execution Open Poisoning** | 修改执行日的开盘价，过去的信号、趋势与目标权重 100% 不变 | **PASS** |
| **One-Week Skip Test** | 修改被 skip 的当前周收盘价，动量（分子用上一周）绝对不变，趋势（含当前周）如期改变 | **PASS** |
| **Anchor Sensitivity** | 修改分子日或分母日的价格，动量严格敏感变化 | **PASS** |

---

## 3. Train Partition 信号审计结果

- 审计命令: `research_v2/.venv/bin/python -m research_v2.cli audit-signals --partition train --all-models --run-id phase_03`
- **Train 统计区间**: `2006-02-01` 至 `2015-12-31`
- **Weekly Events 数量**: **517** 个决策周（首周 `2006-02-03`，末周 `2015-12-24`）
- **Monthly Matched Events 数量**: **118** 个自然月末
- **模型数量**: 9 个固定模型（每个模型在 Train 期间均生成 517 条完整信号记录）
  - `W_M06_T36`, `W_M06_T40`, `W_M06_T44`
  - `W_M09_T36`, `W_M09_T40`, `W_M09_T44`
  - `W_M12_T36`, `W_M12_T40`, `W_M12_T44`

### 3.1 代表性 Lineage 样本 (`W_M06_T36`)
1. **普通周 (2006-02-03)**:
   - 分子日期: `2006-01-27` (上一周收盘)
   - 分母日期: `2005-08-03` (6M 自然月回溯)
   - SPY: 动量 4.05%, 36W SMA 83.95, 收盘 86.49 (Trend: PASS)
   - QQQ: 动量 5.37%, 36W SMA 33.84, 收盘 34.94 (Trend: PASS)
2. **Good Friday 周 (2006-04-13 周四信号)**:
   - 信号日: `2006-04-13` (周五休市，周四为当周最后 session)
   - 分子日期: `2006-04-07`，分母日期: `2005-10-13`
   - SPY: 动量 11.34% (Trend: PASS)
   - QQQ: 动量 12.37% (Trend: PASS)
3. **月末周 (2006-03-31)**:
   - 信号日: `2006-03-31`
   - 分子日期: `2006-03-24`，分母日期: `2005-09-30`
   - SPY 动量 6.81%, QQQ 动量 4.99%
4. **闰年 2 月周 (2008-02-29)**:
   - 信号日: `2008-02-29` (闰日)
   - 分子日期: `2008-02-22`，分母日期: `2007-08-29`
   - SPY: 动量 -6.52%, 36W SMA 103.13, 收盘 95.08 (Trend: FAIL)
   - QQQ: 动量 -9.01%, 36W SMA 42.00, 收盘 36.90 (Trend: FAIL)

---

## 4. Holdout Gate 隔离状态

- **Holdout 策略表现访问**: **NOT_ACCESSED**
- 严格禁止在 `2021-01-01` 之后计算策略收益、持仓、换手、夏普或模型排名。
- 杜绝一切过拟合或前瞻选择。

---

## 5. 测试套件与 Guard 验证

- **Pytest 结果**: **43 passed in 0.57s** (Stage 1–3 全部 43 项测试通过，0 failed, 0 skipped)
- **F0 Guard 结果**: **PASS** (12,399 项受保护历史条目 0 issues，V1/Paper tags 均无漂移)
- **Phase 03 Receipt**: `research_v2/artifacts/phase_03/phase_receipt.json` (SHA-256: `72b6b50794217e76287e5cb7247e1b11d25eb3e8e7238dce52fbec3f5019cf4a`)

---

## 6. 进入 Stage 4 准备状态

- **Stage 4 Ready**: **YES**
- **Next Minimal Read Set**:
  1. `CODEX_EXECUTION_PLAN.md` (Stage 4 小节)
  2. `docs/research_v2/STATUS.md`
  3. `docs/research_v2/REPO_MAP.md`
  4. `docs/research_v2/dataset_manifest.json`
  5. `research_v2/artifacts/phase_03/phase_receipt.json`
