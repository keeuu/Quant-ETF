# Stage 3.3 — Paper Test Isolation & F0 Integrity Fix

> 阶段名称: Stage 3.3 — Paper Test Isolation & F0 Integrity Fix
> 完成时间: 2026-09-21
> 阶段状态: **INTEGRITY_FIX = PASS**

---

## 1. 背景

Stage 3.2 只读审计确认：

| 问题编号 | 描述 |
|---|---|
| **H1** | `reconcile_with_broker()` 内部 `save_rebalance_state(state)` 无 `file_path` 参数，默认写 `logs/rebalance_state.json`（生产路径） |
| **H2** | `src/utils/logger.py` 模块级 `logger = setup_logger()`，任何 `import src.*` 都自动打开 `logs/app.log` 的 FileHandler |
| **H3** | `baseline_manifest.json` 在 test 污染发生之后（18:35 CST）创建，捕获了 synthetic test state 作为 baseline |

本阶段修复 H1、H2，处理 H3，并重新定义 Guard 对动态 Paper state 的保护语义。

---

## 2. H1 修复：reconciliation state path propagation

### 2.1 问题

`src/execution/reconciliation.py` 第 165/169/173/177 行（4 处）：

```python
# 修复前 — 全部 fallback 到生产路径
save_rebalance_state(state)
```

### 2.2 修复

- `reconcile_with_broker` 签名新增 `state_file: Optional[Path] = None`
- 所有 4 处改为 `if state_file is not None: save_rebalance_state(state, file_path=state_file)`
- `state_file=None` 时只做内存状态更新，不写任何文件（最安全默认值）
- `executor.py` 中 3 处 `reconcile_with_broker(client, state)` 全部改为 `reconcile_with_broker(client, state, state_file=state_file)` 显式传 state_file

### 2.3 H1 FIXED ✅

---

## 3. H2 修复：logger import side-effect

### 3.1 问题

`src/utils/logger.py` 末行：

```python
logger = setup_logger()   # 模块级：import 时立即执行，创建 FileHandler(logs/app.log)
```

### 3.2 修复（最小兼容方案：NullHandler at import + explicit configure）

| 函数 | 作用 |
|---|---|
| `_get_null_logger()` | import 时使用，只挂 `NullHandler`，不写文件 |
| `configure_production_logging()` | 仅 `scripts/run_paper.py` 等 production entrypoint 调用，显式创建 FileHandler |
| `configure_test_logging(tmp_path)` | 测试专用，重定向 FileHandler 到 tmp 目录 |

修复后末行：

```python
logger = _get_null_logger()   # import 时安全：只带 NullHandler，不打开任何文件
```

**兼容性**：所有 `from src.utils.logger import logger` 引用无需修改。`setup_logger()` 函数签名完全保持不变。

### 3.3 H2 FIXED ✅

---

## 4. H3 处理：baseline 语义修正

### 4.1 问题

原 `baseline_manifest.json` 在 2026-09-21 18:35:28 CST 创建，此时 `logs/rebalance_state.json` 已被 `test_rejected_order_not_completed` 写入了合成测试状态（`order-rej-1`, `FAILED`），`logs/app.log` 也已被 pytest import side-effect 追加了 fault injection 测试日志。

### 4.2 处理

- **原 `baseline_manifest.json` 永久保留**作为历史证据，不删除不修改
- 新增 `baseline_manifest_v2.json`，其中：
  - `static_files`：原 baseline 中非 `logs/` 文件，继续作为静态冻结基准
  - `dynamic_external_state`：`logs/` 路径单独分类，标注 `DYNAMIC_EXTERNAL_STATE`
  - `amendment` 字段明确记录：`BASELINE_V1_CONTAINED_SYNTHETIC_PAPER_STATE`

### 4.3 H3 ADDRESSED ✅

---

## 5. Guard 重新定义：STATIC_FROZEN / DYNAMIC_EXTERNAL_STATE

`research_v2/guards.py` 新增：

```python
DYNAMIC_EXTERNAL_PATHS = frozenset({
    "logs/app.log",
    "logs/rebalance_state.json",
    "logs/execution.lock",
})

def verify_baseline_v2(root, manifest) -> dict:
    # static_issues  : STATIC_F0_MUTATION 只对非 logs/ 路径
    # dynamic_observations : EXTERNAL_DYNAMIC_STATE_CHANGED 只报告，不为失败
```

**语义**：`logs/` 路径变化 → `EXTERNAL_DYNAMIC_STATE_CHANGED`（仅观测），不触发 `static_status=FAIL`。Research V2 写 `logs/` 路径的禁止由 `ALLOWED_ROOTS` 和 `audit_imports()` 独立执行。

---

## 6. tests/ 网络隔离（新增 conftest.py）

`tests/conftest.py` 新增 autouse fixtures：

| Fixture | 作用 |
|---|---|
| `disable_network` | monkeypatch `socket.connect` → 禁止所有真实网络连接 |
| `force_kill_switch` | 强制 `ORDER_SUBMISSION_ENABLED = False` |
| `redirect_logger` | 调用 `configure_test_logging(tmp_path)` 重定向 FileHandler 到临时目录 |
| `assert_production_logs_unchanged` | 每个测试前后断言 `logs/app.log` 和 `logs/rebalance_state.json` byte/hash/mtime 不变 |
| `tmp_state_file` / `tmp_lock_file` | 提供隔离的临时 state/lock 文件路径 |

---

## 7. 回归测试（tests/test_isolation_regression.py）

10 个回归测试，全部通过：

| 编号 | 测试名 | 状态 |
|---|---|---|
| 1 | `test_rejected_reconciliation_uses_tmp_state` | ✅ PASS |
| 2 | `test_production_state_unchanged_after_reconciliation` | ✅ PASS |
| 3 | `test_import_state_manager_does_not_mutate_app_log` | ✅ PASS |
| 4 | `test_logger_can_redirect_to_temp` | ✅ PASS |
| 5 | `test_network_is_blocked` | ✅ PASS |
| 6 | `test_guard_static_vs_dynamic_classification` | ✅ PASS |
| 7 | `test_dynamic_change_does_not_fail_static_guard` | ✅ PASS |
| 8 | `test_research_v2_has_no_dynamic_state_write_path` | ✅ PASS |
| 9 | `test_paper_kill_switch_is_off` | ✅ PASS |
| 10 | `test_no_order_submission_via_client` | ✅ PASS |

---

## 8. 生产 logs/ 不变性验证（最重要验收条件）

整个测试套件运行前后：

| 文件 | 前 SHA-256 | 后 SHA-256 | 匹配 |
|---|---|---|---|
| `logs/app.log` | `015a8582...` | `015a8582...` | ✅ IDENTICAL |
| `logs/rebalance_state.json` | `59a9e800...` | `59a9e800...` | ✅ IDENTICAL |

size / mtime_ns 三项全部一致。

---

## 9. 完整测试结果

```
pytest tests/ tests_v2/ -v -p no:cacheprovider
======================== 150 passed, 1 warning in 3.60s ========================
```

- `tests/` (旧 Paper tests + 新回归): 96 passed
- `tests_v2/` (Research V2): 54 passed
- 合计: **150 passed, 0 failed, 0 error**

---

## 10. 文件变更清单

### 修改文件

| 文件 | 变更内容 |
|---|---|
| `src/execution/reconciliation.py` | 新增 `state_file` 参数，4 处 save 调用显式传路径 |
| `src/execution/executor.py` | 3 处 `reconcile_with_broker` 调用传 `state_file=state_file` |
| `src/utils/logger.py` | NullHandler at import + `configure_production_logging()` + `configure_test_logging()` |
| `research_v2/guards.py` | 新增 `DYNAMIC_EXTERNAL_PATHS` / `verify_baseline_v2()` / `_is_dynamic_external()` |

### 新增文件

| 文件 | 内容 |
|---|---|
| `tests/conftest.py` | 网络隔离、kill switch 强制、logger 重定向、logs/ 不变性断言 |
| `tests/test_isolation_regression.py` | 10 个隔离回归测试 |
| `docs/research_v2/baseline_manifest_v2.json` | 语义修正的 v2 baseline（动态/静态分类） |
| `docs/research_v2/phase_03_integrity_fix.md` | 本文档 |
| `research_v2/artifacts/phase_03_integrity_fix/phase_receipt.json` | 机器可读凭证 |

### 不变文件

- `docs/research_v2/baseline_manifest.json`（原始历史证据，保留）
- `docs/research_v2/phase_01.md` / `phase_02.md` / `phase_03.md` / `phase_03_fix.md`（历史凭证，保留）
- 所有 Research V2 策略逻辑（momentum formula、DST fix、signal schedule、splits、holdout）

---

## 11. 未执行操作（遵守约束）

- ❌ 未修改 `logs/app.log`
- ❌ 未修改 `logs/rebalance_state.json`
- ❌ 未删除 `baseline_manifest.json`
- ❌ 未覆盖历史 phase_03*.md
- ❌ 未进入 Stage 4
- ❌ 未运行任何 Paper CLI 命令
- ❌ 未提交任何 broker 订单

---

## 12. Stage 4 建议

**`READY_FOR_STAGE_4`**

所有 12 项验收条件均满足（见最终报告）。
