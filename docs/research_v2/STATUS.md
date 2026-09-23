# Research V2 Execution Status

> 记录 Research V2 各阶段的执行状态与流水线门控。

## 1. 阶段总览

| 阶段 | 描述 | 状态 | 凭据文件 |
|---|---|---|---|
| **Stage 1** | 冻结边界、独立骨架与研究协议 | **COMPLETED** | `research_v2/artifacts/phase_01/phase_receipt.json` |
| **Stage 2** | 冻结数据、交易日历与分区权限 | **COMPLETED** | `research_v2/artifacts/phase_02/phase_receipt.json` |
| **Stage 3** | 周信号、选择器与防未来执行日程 | **PASS_AFTER_DST_FIX** | `research_v2/artifacts/phase_03_fix/phase_receipt.json` |
| **Stage 4** | 自融资账本、交易成本与统一日线度量 | **COMPLETED** | `research_v2/artifacts/phase_04/phase_receipt.json` |
| **Stage 5** | 固定对照、SPY 基准与隔离 V1 Oracle 桥接 | PENDING | `research_v2/artifacts/phase_05/phase_receipt.json` |
| **Stage 6** | 验证集选择、Walk-Forward 与稳定性审计 | PENDING | `research_v2/artifacts/phase_06/phase_receipt.json` |
| **Stage 7** | 统计推断、多重检验调整与机制表现 | PENDING | `research_v2/artifacts/phase_07/phase_receipt.json` |
| **Stage 8** | 历史留出解锁与前瞻 OOS 门控 | PENDING | `research_v2/artifacts/phase_08/phase_receipt.json` |
| **Stage 9** | 确定性重放、最终报告与交付清单 | PENDING | `research_v2/artifacts/phase_09/phase_receipt.json` |

---

## 2. 执行进度记录

### Stage 1
- **完成日期**: 2026-09-21
- **状态**: `PASS`
- **保护条目数**: 12,399
- **Protocol 状态**: `LOCKED` (Hash: `a35fe1e50f90b537bd543e9d41a78eff3d9ae9991c362adc603f42432a23e9fb`)
- **Baseline Manifest**: `docs/research_v2/baseline_manifest.json`

### Stage 2
- **完成日期**: 2026-09-21
- **状态**: `PASS`
- **Dataset ID**: `snapshot_20260918`
- **Data SHA-256**: `65aa99046a92ae9b5fd80155cea26065d80ebcc78a740883452d2106e4000aa3`
- **Calendar SHA-256**: `48701e0c22c47190481caad853e2832683e8e094c87735265cc4ae0ccb7fbe7a`
- **XNYS Sessions**: 5,462 (0 missing)
- **Complete Weeks**: 1,133 周
- **测试结果**: 27 passed in 0.42s
- **Holdout Gate**: LOCKED (零策略收益计算)

### Stage 3
- **完成日期**: 2026-09-21
- **状态**: `PASS_AFTER_DST_FIX`
- **Finding**: `FIXED_OFFSET_DST_BUG` (hardcoded `-04:00` replaced by `market_as_of_after_close`)
- **Models**: 9 个固定周动量模型
- **Train Weekly Events**: 518 周 (已纠正原 517 计数偏差)
- **Train Monthly Matched Events**: 119 个月 (已纠正原 118 计数偏差)
- **末周/末月信号日**: 2015-12-31 (执行日 2016-01-04 记为 PENDING)
- **Look-Ahead Audit**: 5 组因果测试全部 PASS
- **测试结果**: 48 passed in 0.76s
- **Holdout Gate**: LOCKED (零策略收益计算)
- **Fix Receipt**: `research_v2/artifacts/phase_03_fix/phase_receipt.json`

### Stage 4
- **完成日期**: 2026-09-23
- **状态**: `COMPLETED`
- **Self-Financing Solver**: Piecewise-linear bounded Brent, 最大残差 $2.91 \times 10^{-11}$
- **Accounting Invariants**: 守恒定理严格验证，0 issues，无人工 cash clamp
- **Canary Model**: `W_M12_T40` (Train, 5 bps): 2,497 trading days, 517 rebalances, 1,653 trades, CAGR 8.47%, Sharpe 0.624, MaxDD 20.76%
- **测试结果**: 177 passed, 0 failed, 1 warning
- **Holdout Gate**: NOT_ACCESSED
- **Receipt**: `research_v2/artifacts/phase_04/phase_receipt.json` (SHA256: `4c3430c96ce63071c76f207975e6d806c2c1601e81cbc91ac8b5d42d4c57d335`)

---

## 3. 下一阶段入口 (Stage 5)

进入 Stage 5 时，仅需读取以下文件：
1. `CODEX_EXECUTION_PLAN.md` (Stage 5 小节)
2. `docs/research_v2/STATUS.md`
3. `research_v2/artifacts/phase_04/phase_receipt.json`
4. `docs/research_v2/REPO_MAP.md`
5. `docs/research_v2/dataset_manifest.json`

