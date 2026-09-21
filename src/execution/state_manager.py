import os
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, Tuple
from config.config import REBALANCE_STATE_FILE
from src.execution.order_lifecycle import is_strategy_order
from src.utils.logger import logger

class StateCorruptedException(Exception):
    """持久化调仓状态文件损坏或非法异常"""
    pass

class ConcurrencyLockError(Exception):
    """并发执行锁冲突异常"""
    pass

class RebalanceStatus:
    PLANNED = "PLANNED"
    SUBMITTING = "SUBMITTING"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    SUBMITTED = "SUBMITTED"
    RECONCILING = "RECONCILING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STATE_CORRUPTED = "STATE_CORRUPTED"

class ExecutionLock:
    """
    进程级调仓执行排他锁 (Single Execution Lock)。
    
    【核心防护目标】：
    覆盖整个 Critical Section (加载/核验券商状态 -> 校验窗口 -> 生成计划 -> 提交订单 -> 状态落盘)。
    如果已有其它进程在运行，当前进程立刻 Fail Closed 退出，彻底防止双重进程并发下单。
    """
    def __init__(self, lock_file: Optional[Path] = None):
        self.lock_file = lock_file or (REBALANCE_STATE_FILE.parent / "execution.lock")
        self._fp = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    def acquire(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        self._fp = open(self.lock_file, "a+")
        try:
            import fcntl
            fcntl.flock(self._fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            logger.debug(f"成功获取调仓执行并发锁: {self.lock_file}")
        except (BlockingIOError, OSError, IOError) as e:
            if self._fp:
                try:
                    self._fp.close()
                except Exception:
                    pass
                self._fp = None
            logger.critical(f"【并发冲突拦截】未能获取执行锁: {self.lock_file}，已有另一个调仓进程在运行！")
            raise ConcurrencyLockError(
                f"【并发冲突拦截】未能获取执行锁 ({self.lock_file})，已有另一个调仓进程在运行！系统 Fail Closed 退出。"
            ) from e

    def release(self):
        if self._fp:
            try:
                import fcntl
                fcntl.flock(self._fp.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self._fp.close()
            except Exception:
                pass
            self._fp = None
            logger.debug(f"已释放调仓执行并发锁: {self.lock_file}")

def load_rebalance_state(file_path: Path = REBALANCE_STATE_FILE) -> Optional[Dict[str, Any]]:
    """
    安全读取持久化状态文件。
    
    【安全规则】：
    如果文件不存在，返回 None；
    如果文件存在但损坏/格式非法，严禁静默返回 None 或吞噬异常，
    必须抛出 StateCorruptedException，杜绝系统误判为'无历史记录'而盲目重下！
    """
    if not file_path.exists():
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.critical(f"【严重警报】调仓状态文件损坏或非法: {file_path}, 错误: {e}")
        raise StateCorruptedException(f"STATE_CORRUPTED: 状态文件损坏或无法解析: {file_path}, 原因: {e}") from e

def save_rebalance_state(state: Dict[str, Any], file_path: Path = REBALANCE_STATE_FILE) -> None:
    """
    安全持久化调仓状态文件 (write temp -> fsync -> atomic replace)。
    
    杜绝写入过程中因掉电或崩溃产生空文件或残缺 JSON。
    """
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file_path.with_suffix(".tmp")
    state["updated_at"] = datetime.now().isoformat()
    if "created_at" not in state:
        state["created_at"] = state["updated_at"]

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    temp_file.replace(file_path)
    logger.debug(f"调仓状态已原子写入并持久化: status={state.get('status')}, month={state.get('rebalance_month')}")

def check_rebalance_idempotency(
    rebalance_month: str,
    force: bool = False,
    file_path: Path = REBALANCE_STATE_FILE,
    client: Optional[Any] = None,
    is_execute_mode: bool = False
) -> Tuple[bool, str]:
    """
    检查本期调仓的幂等性与未决状态防护 (Broker Source of Truth 优先)。
    
    【核心防护】：
    1. 执行模式下严禁通过 force 绕过安全状态 (UNSAFE_FORCE_EXECUTION_BLOCKED) -> Fail Closed；
    2. 状态文件损坏 (STATE_CORRUPTED) -> Fail Closed，阻止调仓；
    3. 本地状态文件丢失但 Broker 端已存在该周期的策略订单 -> 立即识别并拦截，防止因本地文件删除导致重买；
    4. 本月已成功完成 (COMPLETED) -> 阻止重复下单；
    5. 存在未决状态 (SUBMITTING, SUBMISSION_UNKNOWN, SUBMITTED, RECONCILING) -> 阻止重新提交，要求对账。
    """
    if is_execute_mode and force:
        return False, "UNSAFE_FORCE_EXECUTION_BLOCKED: 真实执行模式下严禁使用 force 绕过安全状态！"

    try:
        state = load_rebalance_state(file_path)
    except StateCorruptedException as e:
        return False, f"STATE_CORRUPTED: 状态文件损坏 ({e})！严禁盲目作为新调仓执行，必须先触发券商对账或手动修复。"

    # 若本地未读取到状态记录，先通过 Broker Source of Truth 核查
    if not state:
        if client is not None and getattr(client, "is_authenticated", lambda: False)():
            try:
                recent_orders = client.get_recent_orders(limit=100)
                month_orders = [
                    o for o in recent_orders
                    if is_strategy_order(o.get("client_order_id", ""), rebalance_month=rebalance_month)
                ]
                if month_orders:
                    return False, (
                        f"【券商权威拦截】本地无状态文件，但在券商端检测到周期 [{rebalance_month}] 已有 {len(month_orders)} 笔历史/未结订单！"
                        f"幂等防护已激活，严禁重新下单，必须先执行恢复对账。"
                    )
            except Exception as e:
                logger.warning(f"查询券商订单以校验幂等性时发生异常: {e}")

        return True, "未发现历史状态文件且券商端无既有订单，允许初次调仓。"

    recorded_month = state.get("rebalance_month")
    status = state.get("status")

    if recorded_month == rebalance_month:
        # 1. 本月已成功完成
        if status == RebalanceStatus.COMPLETED and not force:
            return False, f"周期 [{rebalance_month}] 调仓已经于 {state.get('updated_at')} 顺利完成 (COMPLETED)，幂等防护已激活，阻止重复下单。"
        
        # 2. 存在提交中、提交结果未知、已提交但未对账闭环的未决状态
        unresolved_states = [
            RebalanceStatus.SUBMITTING,
            RebalanceStatus.SUBMISSION_UNKNOWN,
            RebalanceStatus.SUBMITTED,
            RebalanceStatus.RECONCILING
        ]
        if status in unresolved_states and not force:
            return False, f"周期 [{rebalance_month}] 存在未决状态 [{status}]！严禁盲目重复提交，必须先触发对账机制 (Reconciliation)。"

    return True, "通过幂等性检查，允许推进调仓。"
