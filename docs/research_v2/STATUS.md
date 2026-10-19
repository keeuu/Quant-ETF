# Research V2 Execution Status

> 记录 Research V2 各阶段的执行状态与流水线门控。

## 1. 阶段总览

| 阶段 | 描述 | 状态 | 凭据文件 |
|---|---|---|---|
| **Stage 1** | 冻结边界、独立骨架与研究协议 | **COMPLETED** | `research_v2/artifacts/phase_01/phase_receipt.json` |
| **Stage 2** | 冻结数据、交易日历与分区权限 | **COMPLETED** | `research_v2/artifacts/phase_02/phase_receipt.json` |
| **Stage 3** | 周信号、选择器与防未来执行日程 | **PASS_AFTER_DST_FIX** | `research_v2/artifacts/phase_03_fix/phase_receipt.json` |
| **Stage 4** | 自融资账本、交易成本与统一日线度量 | **COMPLETED** | `research_v2/artifacts/phase_04/phase_receipt.json` |
| **Stage 5** | 固定对照、SPY 基准与隔离 V1 Oracle 桥接 | **STAGE_5_PASS** | `research_v2/artifacts/phase_05/phase_receipt.json` |
| **Stage 6** | 验证集选择、Walk-Forward 与稳定性审计 | **STAGE_6_PASS** | `research_v2/artifacts/phase_06/phase_receipt.json` |
| **Stage 7** | 统计推断、多重检验调整与机制表现 | **STAGE_7_PASS** | `research_v2/artifacts/phase_07/phase_receipt.json` |
| **Stage 8** | 历史留出解锁与前瞻 OOS 门控 | **SEALED (Holdout Lock v5)** | `research_v2/artifacts/phase_08/phase_receipt.json`; `docs/research_v2/stage_09_holdout_lock_v5.json`; `research_v2/artifacts/phase_08/stage_09_holdout_lock_active_hash.txt` |
| **Stage 9** | 确定性重放、最终报告与交付清单 | **COMPLETED_EXPOSED** | `research_v2/artifacts/phase_09/stage_09_receipt.json`; `docs/research_v2/holdout_exposure_state.json`; `docs/research_v2/stage_09_final_report.md` |

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

### Stage 5-7
- **状态**: Stage 5 `STAGE_5_PASS`、Stage 6 `STAGE_6_PASS`、Stage 7 `STAGE_7_PASS`（各阶段 `verdict` 字段，见对应 `phase_receipt.json`）
- **Holdout Gate**: Stage 5/6 `NOT_ACCESSED`；Stage 7 验证集 `ACCESSED_AS_AUTHORIZED`、Retrospective Holdout `NOT_ACCESSED`
- **模型选择**: `NONE`（Stage 6/7 均仅作描述性排序，无事后模型定义）

### Stage 8
- **状态**: 封存（Seal）完成，最终有效 Holdout Lock 为 v5（Lock v1 为 `REJECTED_PRE_EXPOSURE`，v2/v3/v4 先后被拒或被替换，见 `docs/research_v2/stage_09_holdout_lock_v*.json`）
- **Active Lock Hash**: `4c24c4e65d9bb7df7c4e595bf40894eaf0fc11dd9c847094caf4b8746cd1acd9`（`research_v2/artifacts/phase_08/stage_09_holdout_lock_active_hash.txt`，与 v5 `lock_sha256` 一致）
- **Ready Tag**: `research-v2-stage8-ready-v2`（指向提交 `2789f8f`）
- **说明**: `research_v2/artifacts/phase_08/phase_receipt.json` 与 `docs/research_v2/stage_08_final_audit.json` 是封存过程中较早快照（分别记录 v4 候选与 `STAGE_8_BLOCKED`），以 v5 Lock 与 active hash 为准。

### Stage 9
- **状态**: `COMPLETED_EXPOSED`（`research_v2/artifacts/phase_09/stage_09_receipt.json`）
- **执行尝试**: 第 1 次 `FAILED_UNEXPOSED`（CASH 伪资产边界不匹配，未暴露绩效）；第 2 次 `SUCCESS`
- **Exposure State**: `COMPLETED_EXPOSED`，`successful_stage9_runs = 1`，`failed_unexposed_runs = 1`（`docs/research_v2/holdout_exposure_state.json`）
- **Holdout 类别**: `RETROSPECTIVE_HOLDOUT`，限制 `NOT_TRUE_FINAL_UNSEEN`；`model_selection = NONE`，`promotion_decision = NO_PROMOTION_DECISION`，用途 `DESCRIPTIVE_ONLY`
- **最终报告**: `docs/research_v2/stage_09_final_report.md`；产物清单 `research_v2/artifacts/phase_09/stage_09_artifact_manifest.json`
- **Prospective Final OOS**: `PENDING_DATA`（至少 504 个交易日 / 24 个月）

---

## 3. 后续入口

Research V2 的 Stage 1-9 均已执行完毕；Retrospective Holdout 已暴露，不可再作为 Final Unseen 证据。后续仅剩前瞻 OOS（Paper V2 本地影子运行，`PENDING_DATA`）。需要了解当前状态时读取：
1. `docs/research_v2/STATUS.md`
2. `research_v2/artifacts/phase_09/stage_09_receipt.json`
3. `docs/research_v2/holdout_exposure_state.json`
4. `docs/research_v2/stage_09_final_report.md`
5. `docs/research_v2/REPO_MAP.md`
