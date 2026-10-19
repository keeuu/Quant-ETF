# Stage 2 验收凭证与执行总结

> 阶段名称: Stage 2 — 冻结数据、交易日历与分区权限  
> 完成时间: 2026-09-21  
> 阶段状态: **PASS**

---

## 1. 环境与启动说明 (Environment Bootstrap Note)

```text
ENV_BOOTSTRAP_METHOD = COPIED_FROM_FROZEN_ROOT_VENV
```
- **说明**:
  - 原根虚拟环境 `.venv/` 保持完全只读状态，未发生任何修改。
  - `research_v2/.venv` 为完全独立的虚拟环境目录，运行于 Python 3.12.1。
  - 所有安装包版本与 `research_v2/requirements.lock` 完全对齐，`pip check` 输出 `No broken requirements found.`。
  - 纯净离线重装验证状态：`clean-install reproducibility verification = PENDING`（不构成 Stage 2 阻断项）。

---

## 2. 执行命令与退出状态

| 序号 | 执行命令 | 退出码 | 结果与关键输出 |
|---|---|---|---|
| 1 | `research_v2/.venv/bin/python -m research_v2.cli guard --manifest docs/research_v2/baseline_manifest.json` (前置) | 0 | `{"status": "PASS", "issues": []}` |
| 2 | `research_v2/.venv/bin/python -m pytest -q -p no:cacheprovider tests_v2/...` | 0 | **27 passed in 0.42s** (包含 Stage 1 与 Stage 2 全部测试) |
| 3 | `research_v2/.venv/bin/python -m research_v2.cli snapshot --source data/cache --cutoff 2026-09-18 --as-of 2026-09-18T16:01:00-04:00 --snapshot-id snapshot_20260918` | 0 | 生成独立快照与 `docs/research_v2/dataset_manifest.json`，8 只 ETF 逐字节验证一致 |
| 4 | `research_v2/.venv/bin/python -m research_v2.cli validate-data --manifest docs/research_v2/dataset_manifest.json --run-id phase_02` | 0 | 5462 个 session 与 XNYS 100% 对齐，生成 1133 个完整周 (W_2005_W01 至 W_2026_W38) |
| 5 | `research_v2/.venv/bin/python -m research_v2.cli audit-splits --protocol research_v2/protocol.json --manifest docs/research_v2/dataset_manifest.json --metadata-only` | 0 | 分区与 10 折 Walk-Forward 边界验证通过，Holdout Gate 严格锁定 |

---

## 3. 数据快照与 XNYS 日历审计

### 3.1 资产覆盖与哈希摘要 (8 只 ETF)
- 起止日期: `2005-01-03` 至 `2026-09-18` (每只 5,462 行)
- Schema: `['Date', 'open', 'high', 'low', 'close', 'adj_close', 'adj_open', 'volume']`
- **Data SHA-256 (组合哈希)**: `65aa99046a92ae9b5fd80155cea26065d80ebcc78a740883452d2106e4000aa3`

| 资产 | 记录行数 | 起始日期 | 截止日期 | 原始/快照一致性 SHA-256 |
|---|---|---|---|---|
| `SPY` | 5,462 | 2005-01-03 | 2026-09-18 | `d9aca95184379e49cb737243912169b936b856e792e3997db8fa068e104ae9ff` |
| `QQQ` | 5,462 | 2005-01-03 | 2026-09-18 | `a43166f678fc83ff8269d0d6cfbf746b1981a4b513bcbf310b848bb2081dcfcf` |
| `IWM` | 5,462 | 2005-01-03 | 2026-09-18 | `e1486e5bd9a1f7e91d84fec1f885e35327c49020e980362f01fae29ec96c7fe4` |
| `EFA` | 5,462 | 2005-01-03 | 2026-09-18 | `e13d90d2e1a90c58a2d1d07c0ea91054ca6378e904b76aee4f981b9e078a6352` |
| `EEM` | 5,462 | 2005-01-03 | 2026-09-18 | `4d764bd98899fa524dbbbd5d1448b1bf76e9f2982d613941444e45d1656f5e30` |
| `GLD` | 5,462 | 2005-01-03 | 2026-09-18 | `3c17df85313a0da747d8ff24976cf365287fce3b8d4f4510aee7593c6f3e1a06` |
| `TLT` | 5,462 | 2005-01-03 | 2026-09-18 | `e44a4a1aa8290f65d6c82736561be6b19d45e69bfbebf792df9a65672ea4fcfe` |
| `IEF` | 5,462 | 2005-01-03 | 2026-09-18 | `d7fe2c24d6ce1fb4377bb4570bcba7f6e43105ff7ccf0e21975b9bf6f68c783e` |

### 3.2 XNYS 交易日历冻结
- **Calendar Library Version**: `exchange-calendars == 4.13.2`
- **Session Count**: 5,462 sessions (从 2005-01-03 至 2026-09-18)
- **Session Table SHA-256**: `48701e0c22c47190481caad853e2832683e8e094c87735265cc4ae0ccb7fbe7a`
- **Missing Sessions**: **0** (全体 8 只资产与日历完全对齐)
- **Complete Weeks**: **1,133** 周 (首周: `W_2005_W01`，末周: `W_2026_W38`)

---

## 4. 分区与 Holdout Gate 状态

- **Warmup**: `2005-01-03` 至 `2006-01-31` (272 sessions，仅作技术指标计算，不参与评分)
- **Train**: `2006-02-01` 至 `2015-12-31` (2,497 sessions)
- **Validation**: `2016-01-01` 至 `2020-12-31` (1,259 sessions)
- **Historical Holdout**: `2021-01-01` 至 `2026-09-18` (1,434 sessions，标记 `RETROSPECTIVE_HOLDOUT_EXPOSED_BY_V1`)
- **Prospective Final OOS**: `PENDING_DATA` (预注册需至少 504 sessions 且 24 个月)
- **Walk-Forward Folds**: 10 个年度折 (`WF_2011` 至 `WF_2020`)
- **Holdout Strategy Data Access**: **NOT_ACCESSED** (严禁并杜绝任何策略收益、持仓、换手、夏普计算)

---

## 5. 关键凭据与哈希

- **Dataset Manifest**: `docs/research_v2/dataset_manifest.json`
- **Phase 02 Receipt**: `research_v2/artifacts/phase_02/phase_receipt.json` (SHA-256: `dcb09e678d055eed84e8195e23a73ee9d855028a1ab5c42b8cc6163a6f08bdd5`)

---

## 6. 进入 Stage 3 准备状态

- **Stage 3 Ready**: **YES**
- **Next Minimal Read Set**:
  1. `CODEX_EXECUTION_PLAN.md` (Stage 3 小节)
  2. `docs/research_v2/STATUS.md`
  3. `docs/research_v2/REPO_MAP.md`
  4. `docs/research_v2/dataset_manifest.json`
  5. `research_v2/artifacts/phase_02/phase_receipt.json`
