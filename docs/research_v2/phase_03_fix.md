# Stage 3 独立审计发现修复凭证 (DST Timezone Offset Fix)

> 阶段名称: Stage 3 — 周信号、选择器与防未来执行日程 (Bugfix)  
> 完成时间: 2026-09-21  
> 阶段状态: **PASS_AFTER_DST_FIX**  
> 审计问题: **FIXED_OFFSET_DST_BUG**

---

## 1. 发现与根本原因 (Finding & Root Cause)

### 1.1 审计发现
在 Stage 3 独立对抗审计过程中，发现 Train 分区（`2006-02-01` 至 `2015-12-31`）独立日历推导应有 **518** 个周事件与 **119** 个月度匹配事件，但原 `phase_03` 仅统计到 **517** 个周事件与 **118** 个月度匹配事件。

### 1.2 根本原因 (Root Cause)
- 原 `cli.py` 中构建 Train 分区截止点时使用了硬编码的 `-04:00` 夏令时偏移：
  ```python
  as_of_train = f"{train_end}T16:01:00-04:00"
  ```
- 2015-12-31 处于冬季，纽约真实时区为 **EST (UTC-5)**。由于 2016-01-01（元旦）休市，2015-12-31（周四）为该周最后一个合法交易 session，并在美东时间 16:00:00（UTC 21:00:00）收盘。
- 错误的 `-04:00` 偏移使 `as_of` 被解析为 `2015-12-31T20:01:00 UTC`，早于实际收盘时间 `21:00:00 UTC`。
- `build_complete_weeks` 与 `build_monthly_matched_schedule` 因而保守地判定 2015-12-31 当日收盘尚未完成，导致该完整周（`W_2015_W53`）与当月末（`M_MATCH_2015_12`）被错误剔除。

---

## 2. 修复方案 (Unified Timezone Construction)

### 2.1 统一时间构造 Helper
在 [`research_v2/calendar.py`](file:///Users/zhangzeyuan/Projects/quant-ETF/research_v2/calendar.py) 中新增统一市场截止时间构造器：
```python
def market_as_of_after_close(
    session_date: str | datetime | pd.Timestamp,
    minutes_after_close: int = 1,
) -> str:
    """Construct an America/New_York timezone-aware market as_of timestamp strictly after session close.

    Uses XNYS actual session_close as authority to correctly handle:
    - Winter EST (-05:00) vs Summer EDT (-04:00) transitions
    - Early closes (e.g. 13:00 on Black Friday or Christmas Eve)
    - Exceptional calendar schedules
    """
    cal = get_trading_calendar()
    ts = pd.Timestamp(session_date)
    if cal.is_session(ts):
        close_utc = cal.session_close(ts)
        close_ny = close_utc.astimezone(NY_TZ)
        as_of_dt = close_ny + pd.Timedelta(minutes=minutes_after_close)
        return as_of_dt.isoformat()
    else:
        dt = datetime(ts.year, ts.month, ts.day, 16, minutes_after_close, 0, tzinfo=NY_TZ)
        return dt.isoformat()
```

### 2.2 彻底消除手工字符串拼接
- 将 `research_v2/cli.py` 中 `as_of_train` 统一改为 `market_as_of_after_close(train_end)`。
- 将 `tests_v2/test_lookahead.py` 中所有手工 `f"{sessions[-1]}T16:01:00-04:00"` 统一替换为 `market_as_of_after_close(sessions[-1])`。
- 绝不手工根据月份推算 DST，完全委托 `exchange_calendars` 的真实收盘时刻与 `zoneinfo.ZoneInfo("America/New_York")` 处理。

---

## 3. 回归测试套件 (Regression Tests Added)

在 [`tests_v2/test_calendar.py`](file:///Users/zhangzeyuan/Projects/quant-ETF/tests_v2/test_calendar.py) 与 [`tests_v2/test_schedule.py`](file:///Users/zhangzeyuan/Projects/quant-ETF/tests_v2/test_schedule.py) 中补充了以下确定性回归测试：

1. **Winter EST 测试 (`test_market_as_of_after_close_winter`)**:
   - 输入 `2015-12-31`，验证输出为 `2015-12-31T16:01:00-05:00`（UTC-5）。
   - 验证 `build_complete_weeks` 正确输出 1 个完整周（`W_2015_W53`，信号日 `2015-12-31`）。
   - 对比断言：使用旧的 `-04:00` 时事件数确实为 0（重现并锁定该 Bug）。
2. **Summer EDT 测试 (`test_market_as_of_after_close_summer`)**:
   - 输入 `2026-09-18`，验证输出为 `2026-09-18T16:01:00-04:00`（UTC-4）。
3. **DST 切换测试 (`test_market_as_of_after_close_dst_transitions`)**:
   - 春季切换：`2024-03-08`（EST, `-05:00`） $\to$ `2024-03-11`（EDT, `-04:00`）。
   - 秋季切换：`2024-11-01`（EDT, `-04:00`） $\to$ `2024-11-04`（EST, `-05:00`）。
4. **提前闭市 Early Close 测试 (`test_market_as_of_after_close_early_close`)**:
   - 黑色星期五 `2024-11-29`（13:00 收盘）：输出 `2024-11-29T13:01:00-05:00`，而非错误的 16:01。
   - 独立日前夕 `2024-07-03`（13:00 收盘）：输出 `2024-07-03T13:01:00-04:00`。
5. **事件计数回归测试 (`test_train_partition_event_counts_regression`)**:
   - 严格断言 Train 期间 Weekly Events 计数为 **518**。
   - 严格断言 Train 期间 Monthly Matched Events 计数为 **119**。
   - 确认最后一周信号日为 `2015-12-31`，执行日为 `2016-01-04`（状态为 `PENDING`）。
   - 确认最后一个月度事件对应 `M_MATCH_2015_12`，信号日为 `2015-12-31`，执行日为 `2016-01-04`。

---

## 4. 修复后计数对比与重新审计

| 项目 | 修复前 (phase_03) | 修复后 (phase_03_fix) | 独立审计理论推导 |
|---|---|---|---|
| Train Weekly Events | 517 | **518** | 518 |
| Train Monthly Matched Events | 118 | **119** | 119 |
| 9 模型 Train 每模型信号总数 | 517 | **518** | 518 |
| 末周信号日 | 2015-12-24 | **2015-12-31** | 2015-12-31 |
| 末周执行日 | 2015-12-28 (COMPLETE) | **2016-01-04 (PENDING)** | 2016-01-04 (PENDING) |
| 末月度匹配决策周期 | 2015-11 | **2015-12** (`M_MATCH_2015_12`) | 2015-12 |

---

## 5. 因果性与隔离验证

- **防未来因果测试**: 全部 5 组因果测试（Prefix invariance, Future poisoning, Execution open poisoning, Skip-week test, Anchor sensitivity）保持 **PASS**。
- **Holdout Gate 状态**: **LOCKED_NOT_ACCESSED**，无任何 2021-01-01 之后的数据或收益被访问。
- **Pytest 结果**: `tests_v2` 中 **48 passed in 0.76s**（0 failed, 0 skipped）。
- **F0 Guard**: **PASS**（受保护条目 12,399，0 issues；`research-v1` 与 `paper-v1-ready` tags 完全一致）。
