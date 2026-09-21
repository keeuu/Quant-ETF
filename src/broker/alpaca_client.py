import os
import time
from urllib.parse import urlparse
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest, GetCalendarRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce, OrderType
from alpaca.common.exceptions import APIError
from config.config import (
    ALPACA_API_KEY, 
    ALPACA_SECRET_KEY, 
    ALPACA_BASE_URL, 
    DEFAULT_PAPER_BASE_URL,
    ALLOWED_PAPER_HOSTS,
    ORDER_SUBMISSION_ENABLED
)
from src.utils.logger import logger

class SecurityException(Exception):
    """安全违规异常：严禁实盘凭证或 Live Trading 模式，或 Kill-Switch 拦截"""
    pass

class SubmissionUnknownException(Exception):
    """
    提交订单结果不确定异常：
    发生网络超时、连接重置或 5xx 故障，无法确定 Broker 是否已成功入库。
    严禁盲目自动重试提交，必须进入 SUBMISSION_UNKNOWN 并通过 get_order_by_client_order_id 确认！
    """
    def __init__(self, client_order_id: str, message: str, original_error: Optional[Exception] = None):
        super().__init__(f"SUBMISSION_UNKNOWN for [{client_order_id}]: {message}")
        self.client_order_id = client_order_id
        self.original_error = original_error

def _format_order_dict(o: Any) -> Dict[str, Any]:
    """统一格式化 Alpaca Order 对象为字典格式"""
    return {
        "id": str(o.id) if hasattr(o, "id") and o.id else "",
        "client_order_id": str(o.client_order_id) if hasattr(o, "client_order_id") and o.client_order_id else "",
        "symbol": str(o.symbol) if hasattr(o, "symbol") else "",
        "qty": float(o.qty) if getattr(o, "qty", None) is not None else 0.0,
        "filled_qty": float(o.filled_qty) if getattr(o, "filled_qty", None) is not None else 0.0,
        "notional": float(o.notional) if getattr(o, "notional", None) is not None else 0.0,
        "filled_avg_price": float(o.filled_avg_price) if getattr(o, "filled_avg_price", None) is not None else 0.0,
        "side": o.side.value if hasattr(o.side, "value") else str(o.side),
        "status": o.status.value if hasattr(o.status, "value") else str(o.status).lower(),
        "type": o.type.value if hasattr(o.type, "value") else str(getattr(o, "type", "market")),
        "time_in_force": o.time_in_force.value if hasattr(o.time_in_force, "value") else str(getattr(o, "time_in_force", "day")),
        "created_at": o.created_at.isoformat() if hasattr(o, "created_at") and hasattr(o.created_at, "isoformat") else str(getattr(o, "created_at", "")),
        "submitted_at": o.submitted_at.isoformat() if hasattr(o, "submitted_at") and hasattr(o.submitted_at, "isoformat") else str(getattr(o, "submitted_at", "")),
        "filled_at": o.filled_at.isoformat() if hasattr(o, "filled_at") and hasattr(o.filled_at, "isoformat") else str(getattr(o, "filled_at", "")),
        "canceled_at": o.canceled_at.isoformat() if hasattr(o, "canceled_at") and hasattr(o.canceled_at, "isoformat") else str(getattr(o, "canceled_at", "")),
        "failed_at": o.failed_at.isoformat() if hasattr(o, "failed_at") and hasattr(o.failed_at, "isoformat") else str(getattr(o, "failed_at", ""))
    }

class AlpacaPaperClient:
    """
    Alpaca Paper Trading 专属只读与安全管控客户端。
    
    【核心安全保证】：
    1. 强制锁定 paper=True，禁止任何参数覆盖；
    2. 终结点 Host 必须严格属于 ALLOWED_PAPER_HOSTS 允许列表，杜绝 fake-paper 伪造或 live host；
    3. 不再以 API Key 前缀（AK/PK）作为 Paper/Live 判据，真正的安全边界由 paper=True 与专用 Endpoint 保证；
    4. 凭证存在但认证失败时 Fail Closed，拒绝自动回退至 Mock 假象；
    5. 严禁暴露 API Secret；
    6. 硬性 Kill-Switch ORDER_SUBMISSION_ENABLED 阻断，当前阶段绝对禁止调用真实发单；
    7. 读操作允许有限幂等重试，写操作 (submit_order) 严禁无脑重复 POST 重试。
    """
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        base_url: Optional[str] = None,
        paper: bool = True
    ):
        # 1. 强制校验 paper 标识，绝不允许任何形式的 paper=False
        if not paper:
            raise SecurityException("【最高安全拦截】系统硬性禁止 Live Trading (paper=False)！只允许使用 Paper Trading 模拟账户。")

        self.api_key = (api_key if api_key is not None else ALPACA_API_KEY).strip()
        self.secret_key = (secret_key if secret_key is not None else ALPACA_SECRET_KEY).strip()
        self.base_url = (base_url or ALPACA_BASE_URL or DEFAULT_PAPER_BASE_URL).strip()

        # 2. 严格白名单校验 Base URL Host (严禁字符串包含式模糊校验)
        parsed = urlparse(self.base_url)
        host = parsed.netloc.lower()
        if not host:
            host = parsed.path.lower().split("/")[0]

        if host not in ALLOWED_PAPER_HOSTS:
            raise SecurityException(
                f"【最高安全拦截】终结点主机 '{host}' 不在合法 Paper 白名单 {ALLOWED_PAPER_HOSTS} 中！"
                f"系统严格拒绝连接任何实盘终结点或伪造的第三方服务器。"
            )

        # 3. 提示性非权威检查 (不作为安全判断依据，仅输出警告)
        if self.api_key.startswith("AK"):
            logger.warning("提示: API Key 以 'AK' 开头，请确认是否为 Paper 凭证。安全边界由强制 paper=True 与合法 Paper 域名保证。")

        # 4. 初始化底层 TradingClient (硬编码 paper=True)
        if self.api_key and self.secret_key:
            self._client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=True,
                url_override=self.base_url
            )
            masked_key = self.api_key[:4] + "..." + self.api_key[-4:] if len(self.api_key) > 8 else "***"
            logger.info(f"Alpaca Paper Trading 客户端初始化完成 (API Key: {masked_key}, Host: {host}, paper=True)")
        else:
            self._client = None
            logger.debug("未检测到完整的 ALPACA_API_KEY 与 ALPACA_SECRET_KEY，客户端将以 OFFLINE_MOCK 模式待命。")

    def __repr__(self) -> str:
        masked_key = self.api_key[:4] + "***" if self.api_key else "None"
        return f"<AlpacaPaperClient(api_key='{masked_key}', paper=True, base_url='{self.base_url}')>"

    def __str__(self) -> str:
        return self.__repr__()

    def has_credentials(self) -> bool:
        """是否提供了 API 凭证"""
        return bool(self.api_key and self.secret_key)

    def is_authenticated(self) -> bool:
        """检查凭证客户端对象是否已构造"""
        return self._client is not None

    def _execute_read_with_retry(self, func, *args, max_retries: int = 3, backoff_factor: float = 0.5, **kwargs):
        """
        只读操作安全有限重试。
        只读操作（GET）具有天然幂等性，可安全进行短暂退避重试以应对偶发网络抖动。
        """
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_err = e
                # 若是明确认证失败则不重试
                if "PAPER_AUTHENTICATION_FAILED" in str(e) or (isinstance(e, APIError) and e.status_code in (401, 403)):
                    raise
                if attempt < max_retries:
                    sleep_time = backoff_factor * (2 ** (attempt - 1))
                    logger.warning(f"只读请求失败 (第 {attempt}/{max_retries} 次)，将在 {sleep_time:.2f} 秒后重试: {e}")
                    time.sleep(sleep_time)
        raise last_err

    def get_account(self) -> Dict[str, Any]:
        """
        获取 Paper 账户核心概况信息 (只读)。
        若凭证错误，严格抛出 PAPER_AUTHENTICATION_FAILED 异常，杜绝静默回退。
        """
        if not self._client:
            raise RuntimeError("Alpaca Paper API 凭证未配置，无法查询账户。")

        def _do_get():
            try:
                acc = self._client.get_account()
                return {
                    "account_id": str(acc.id),
                    "status": acc.status.value if hasattr(acc.status, "value") else str(acc.status),
                    "currency": acc.currency,
                    "cash": float(acc.cash),
                    "equity": float(acc.equity),
                    "buying_power": float(acc.buying_power),
                    "portfolio_value": float(acc.portfolio_value) if hasattr(acc, "portfolio_value") else float(acc.equity),
                    "pattern_day_trader": getattr(acc, "pattern_day_trader", False)
                }
            except Exception as e:
                logger.error(f"调用 Alpaca Paper get_account 失败: {e}")
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: Alpaca Paper 认证或查询失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_positions(self) -> Dict[str, Dict[str, float]]:
        """
        获取当前 Paper 账户所有持仓标的 (只读)。
        """
        if not self._client:
            raise RuntimeError("Alpaca Paper API 凭证未配置，无法查询持仓。")

        def _do_get():
            try:
                positions_list = self._client.get_all_positions()
                positions_dict = {}
                for p in positions_list:
                    sym = p.symbol
                    positions_dict[sym] = {
                        "qty": float(p.qty),
                        "market_value": float(p.market_value),
                        "current_price": float(p.current_price),
                        "unrealized_pl": float(p.unrealized_pl) if hasattr(p, "unrealized_pl") else 0.0,
                        "side": p.side.value if hasattr(p.side, "value") else str(p.side)
                    }
                return positions_dict
            except Exception as e:
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 查询持仓失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_open_orders(self) -> List[Dict[str, Any]]:
        """
        获取当前未成交 / 挂起的所有委托单 (只读)。
        """
        if not self._client:
            raise RuntimeError("Alpaca Paper API 凭证未配置，无法查询未结订单。")

        def _do_get():
            try:
                req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
                orders = self._client.get_orders(req)
                return [_format_order_dict(o) for o in orders]
            except Exception as e:
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 查询未结订单失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_recent_orders(self, limit: int = 50, status: str = "all") -> List[Dict[str, Any]]:
        """
        获取最近的所有历史委托单 (包含已完成、已取消、被拒绝等订单 - 只读)。
        """
        if not self._client:
            raise RuntimeError("Alpaca Paper API 凭证未配置，无法查询历史订单。")

        def _do_get():
            try:
                query_status = QueryOrderStatus.ALL
                if status.lower() == "open":
                    query_status = QueryOrderStatus.OPEN
                elif status.lower() == "closed":
                    query_status = QueryOrderStatus.CLOSED

                req = GetOrdersRequest(status=query_status, limit=limit)
                orders = self._client.get_orders(req)
                return [_format_order_dict(o) for o in orders]
            except Exception as e:
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 查询历史订单失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_order_by_client_order_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        """
        根据 client_order_id 查询特定订单状态 (只读)。
        若订单在券商端不存在 (404)，返回 None。
        """
        if not self._client:
            raise RuntimeError("Alpaca Paper API 凭证未配置，无法查询订单。")

        def _do_get():
            try:
                order = self._client.get_order_by_client_id(client_order_id)
                return _format_order_dict(order)
            except APIError as e:
                # 404 表明该 client_order_id 在券商端尚未记录
                if e.status_code == 404 or (hasattr(e, "code") and e.code == 40410000):
                    return None
                raise RuntimeError(f"查询订单 [{client_order_id}] 失败: {e}") from e
            except Exception as e:
                # 处理异常消息中明确的 404 标识
                if "not found" in str(e).lower() or "404" in str(e):
                    return None
                raise RuntimeError(f"查询订单 [{client_order_id}] 失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_asset_metadata(self, symbol: str) -> Dict[str, Any]:
        """
        读取单个标的在 Alpaca 市场的元数据 (tradable, fractionable, status)。
        离线模式下显式标为 OFFLINE_UNVERIFIED / STATIC_ASSUMPTION。
        """
        if not self._client:
            return {
                "symbol": symbol,
                "tradable": True,
                "fractionable": True,
                "status": "active (assumed)",
                "is_eligible": False,
                "eligibility_display": "UNVERIFIED (STATIC_ASSUMPTION)",
                "source": "OFFLINE_UNVERIFIED",
                "verified_by_api": False,
                "note": "未经 Alpaca Paper API 实时确认，仅为离线静态假设"
            }

        def _do_get():
            try:
                asset = self._client.get_asset(symbol)
                status_str = asset.status.value if hasattr(asset.status, "value") else str(asset.status).lower()
                tradable = bool(asset.tradable)
                fractionable = bool(asset.fractionable)
                is_active = status_str == "active"
                is_eligible = tradable and fractionable and is_active

                return {
                    "symbol": symbol,
                    "tradable": tradable,
                    "fractionable": fractionable,
                    "status": status_str,
                    "is_eligible": is_eligible,
                    "eligibility_display": "PASS (API_VERIFIED)" if is_eligible else "FAIL",
                    "source": "ALPACA_API_VERIFIED",
                    "verified_by_api": True,
                    "note": "经 Alpaca Paper API 实时核验"
                }
            except Exception as e:
                logger.error(f"[{symbol}] 获取资产元数据失败: {e}")
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 查询资产 [{symbol}] 失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def check_universe_eligibility(self, symbols: List[str]) -> Tuple[bool, Dict[str, Dict[str, Any]]]:
        """
        检查资产池所有标的的交易资格 (Asset Eligibility)。
        只有全部经真实 API 核验且符合条件时，all_eligible 才能为 True。
        """
        report = {}
        all_eligible = True
        for sym in symbols:
            info = self.get_asset_metadata(sym)
            report[sym] = info
            if not (info.get("verified_by_api", False) and info.get("is_eligible", False)):
                all_eligible = False
                if self.is_authenticated():
                    logger.error(f"[{sym}] 标的不具备 Paper 交易资格: tradable={info['tradable']}, fractionable={info['fractionable']}, status={info['status']}")

        return all_eligible, report

    def get_calendar(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        从 Alpaca 市场日历 API 获取指定日期范围内的真实交易日与开闭市时间 (只读)。
        """
        if not self._client:
            raise RuntimeError("未配置 Alpaca 凭证，无法从 API 获取市场日历。")

        def _do_get():
            try:
                s_d = datetime.strptime(start_date, "%Y-%m-%d").date() if isinstance(start_date, str) else start_date
                e_d = datetime.strptime(end_date, "%Y-%m-%d").date() if isinstance(end_date, str) else end_date
                req = GetCalendarRequest(start=s_d, end=e_d)
                cal_list = self._client.get_calendar(req)
                return [
                    {
                        "date": c.date,
                        "open": c.open,
                        "close": c.close
                    }
                    for c in cal_list
                ]
            except Exception as e:
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 获取 Alpaca 市场日历失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def get_clock(self) -> Dict[str, Any]:
        """
        从 Alpaca 获取官方市场时钟 (Broker Clock - 只读)。
        """
        if not self._client:
            raise RuntimeError("未配置 Alpaca 凭证，无法从 API 获取市场时钟。")

        def _do_get():
            try:
                clk = self._client.get_clock()
                return {
                    "timestamp": clk.timestamp,
                    "is_open": clk.is_open,
                    "next_open": clk.next_open,
                    "next_close": clk.next_close
                }
            except Exception as e:
                raise RuntimeError(f"PAPER_AUTHENTICATION_FAILED: 获取 Alpaca 市场时钟失败: {e}") from e

        return self._execute_read_with_retry(_do_get)

    def submit_order(
        self,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        client_order_id: Optional[str] = None,
        qty: Optional[float] = None,
        notional: Optional[float] = None,
        *args,
        **kwargs
    ) -> Dict[str, Any]:
        """
        提交订单接口 (执行层核心安全把控)。
        
        【核心安全熔断 (Kill-Switch)】：
        当 ORDER_SUBMISSION_ENABLED 为 False 时，无论何种调用或参数，一律强制阻断！
        
        【写操作重试策略】：
        严格禁止通用网络重试装饰器对 POST 进行无脑重复提交！
        若遭遇网络超时、连接重置等未知异常，严格抛出 SubmissionUnknownException，
        驱动上层进入 SUBMISSION_UNKNOWN 状态并通过 get_order_by_client_order_id 查询确认。
        """
        # 1. 第一级：全局 Kill-Switch 硬拦截
        if not ORDER_SUBMISSION_ENABLED:
            raise SecurityException(
                "【安全熔断拦截 (Kill-Switch)】ORDER_SUBMISSION_ENABLED = False！"
                "当前系统处于只读验证阶段，严禁提交任何 Paper 或 Live 订单。"
            )

        if not self._client:
            raise RuntimeError("Alpaca 客户端未认证，无法提交订单。")

        # 2. 第二级：入参安全校验
        side_clean = side.lower().strip()
        if side_clean not in ("buy", "sell"):
            raise ValueError(f"无效的交易方向 side: {side}")

        order_side = OrderSide.BUY if side_clean == "buy" else OrderSide.SELL

        if not client_order_id or not client_order_id.strip():
            raise ValueError("必须指定确定性的 client_order_id，严禁匿名或空 ID 提交订单。")

        if qty is None and notional is None:
            raise ValueError("必须指定 qty 或 notional 之一。")
        if qty is not None and notional is not None:
            raise ValueError("qty 与 notional 不能同时指定。")

        # 3. 构造市价委托请求 (硬编码 Market, Day, Regular Hours Only)
        order_data = {
            "symbol": symbol.upper().strip(),
            "side": order_side,
            "type": OrderType.MARKET,
            "time_in_force": TimeInForce.DAY,
            "extended_hours": False,
            "client_order_id": client_order_id.strip()
        }

        if qty is not None:
            order_data["qty"] = float(qty)
        else:
            order_data["notional"] = round(float(notional), 2)

        req = MarketOrderRequest(**order_data)

        # 4. 执行单次 POST 提交 (无任何盲目自动重试)
        try:
            logger.info(f"正在向 Alpaca Paper 提交市价单: symbol={symbol}, side={side_clean}, client_order_id={client_order_id}")
            resp = self._client.submit_order(req)
            formatted = _format_order_dict(resp)
            logger.info(f"订单提交成功: ID={formatted['id']}, ClientID={formatted['client_order_id']}, Status={formatted['status']}")
            return formatted
        except (TimeoutError, ConnectionError, OSError) as net_err:
            # 网络超时/连接中断 -> 结果未知，严禁重试 POST！
            logger.critical(f"网络异常导致订单提交结果未知 [{client_order_id}]: {net_err}")
            raise SubmissionUnknownException(
                client_order_id=client_order_id,
                message=f"网络请求超时或连接中断: {net_err}",
                original_error=net_err
            ) from net_err
        except APIError as api_err:
            # 若券商返回 422 且提示 client_order_id 已存在，则说明之前已成功接收
            err_msg = str(api_err).lower()
            if api_err.status_code == 422 and ("client_order_id" in err_msg or "already exists" in err_msg):
                logger.warning(f"检测到 client_order_id [{client_order_id}] 已存在于券商端，正在尝试找回原订单...")
                existing = self.get_order_by_client_order_id(client_order_id)
                if existing:
                    return existing
            logger.error(f"Alpaca API 明确拒绝订单 [{client_order_id}]: {api_err}")
            raise RuntimeError(f"BROKER_ORDER_REJECTED: 订单被拒绝: {api_err}") from api_err
        except Exception as e:
            # 其它未知异常
            err_str = str(e).lower()
            if "timeout" in err_str or "timed out" in err_str or "connection" in err_str:
                raise SubmissionUnknownException(
                    client_order_id=client_order_id,
                    message=f"可能发生超时的未知异常: {e}",
                    original_error=e
                ) from e
            raise
