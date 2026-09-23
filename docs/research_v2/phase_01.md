# Stage 1 验收凭证与执行总结

> 阶段名称: Stage 1 — 冻结边界、独立骨架与研究协议  
> 完成时间: 2026-09-21  
> 阶段状态: **PASS**

---

## 1. 执行命令与退出状态

| 序号 | 执行命令 | 退出码 | 结果与关键输出 |
|---|---|---|---|
| 1 | `git status --short` | 0 | 仅包含未跟踪文件 `CODEX_EXECUTION_PLAN.md` 与允许新建目录 `research_v2/`, `tests_v2/`, `docs/research_v2/` |
| 2 | `git rev-parse HEAD research-v1 paper-v1-ready` | 0 | HEAD: `3f708f88...`, `research-v1`: `d3584014...`, `paper-v1-ready`: `34039df6...` 均无漂移 |
| 3 | `research_v2/.venv/bin/pip check` | 0 | `No broken requirements found.` 环境与 `requirements.lock` 完全对齐 |
| 4 | `research_v2/.venv/bin/python -m research_v2.cli baseline-init` | 0 | 生成 `docs/research_v2/baseline_manifest.json`，保护条目 12,399 项 |
| 5 | `research_v2/.venv/bin/python -m research_v2.cli protocol-lock` | 0 | 锁定协议生成 `docs/research_v2/protocol_lock.json`，Protocol SHA-256 吻合 |
| 6 | `research_v2/.venv/bin/python -m pytest -q -p no:cacheprovider tests_v2/test_guards.py tests_v2/test_protocol.py` | 0 | **9 passed in 0.06s** |
| 7 | `research_v2/.venv/bin/python -m research_v2.cli guard --manifest docs/research_v2/baseline_manifest.json` | 0 | `{"status": "PASS", "issues": []}` |

---

## 2. 关键哈希与基线锚点

- **Baseline Manifest SHA-256**: `e7d83f6e3fdc5e8a56115d1960bc6d1a429beb40db79e55788ffe8f3bad6c217`
- **Protocol Object SHA-256**: `a35fe1e50f90b537bd543e9d41a78eff3d9ae9991c362adc603f42432a23e9fb`
- **Execution Plan SHA-256**: `5d8dc33318214cd0493ad320ecc122e46b057ab74b9c1b54a80a0b1f0b9cbeea`
- **Phase Receipt SHA-256**: `5d3ba5b7f36beea12822f2d040d71c1d6474310a885d8942a8e1fdb8f0574615`

---

## 3. 测试通过清单 (`tests_v2`)

1. `test_v2_import_isolation`: 静态 AST 检查及运行时模块边界，严禁导入 `src.*`, `config.*`, `scripts.*`, `alpaca`, `yfinance`, `dotenv`。
2. `test_checked_output_path_allowlist`: 严格白名单路径限制，拒绝绝对路径、路径遍历（`..`）以及凭证（`.env*`）。
3. `test_open_output_exclusive_and_atomic`: 独占原子写入、防止覆盖、允许日志 append。
4. `test_reserve_run`: 运行目录原子占位，防止已有 `run_id` 覆写。
5. `test_verify_baseline_in_sandbox`: 隔离沙盒中的文件内容篡改、删除、未授权添加、tag 漂移检出。
6. `test_provenance_canonical_json_and_trial_registry`: 规范化 JSON 序列化、拒绝非有限浮点数、TrialRegistry 哈希链审计。
7. `test_protocol_constants_and_validation`: 严格验证固定 Universe（8 只）、9 个固定周模型、Top 3 等权、长头无杠杆等约束。
8. `test_protocol_tamper_rejection`: 拒绝任何对 Universe、模型网格、投资组合规则或成本参数的修改。
9. `test_protocol_lock_file`: 验证 `protocol_lock.json` 与执行合同及代码 commit 的完整匹配。

---

## 4. F0 边界与隔离保护核实

- **V1 Tag** (`research-v1`): 未修改。
- **Paper Tag** (`paper-v1-ready`): 未修改。
- **原代码与配置** (`src/**`, `config/**`, `scripts/**`, `tests/**`, `results/**`, `data/cache/**`): 零修改，零访问写入。
- **原虚拟环境** (`.venv/**`): 保持原样未修改，V2 使用独立的 `research_v2/.venv`。
- **根目录配置**: 未修改根 `requirements.txt` 和根 `.gitignore`。

---

## 5. 进入 Stage 2 的准备就绪状态

- **Stage 2 Ready**: **YES**
- **Next Minimal Read Set**:
  1. `CODEX_EXECUTION_PLAN.md` (Stage 2 小节)
  2. `docs/research_v2/STATUS.md`
  3. `docs/research_v2/REPO_MAP.md`
  4. `docs/research_v2/protocol_lock.json`
  5. `research_v2/artifacts/phase_01/phase_receipt.json`
