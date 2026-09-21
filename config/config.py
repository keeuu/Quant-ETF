import os
from pathlib import Path
from dotenv import load_dotenv

# 1. 路径设置
CONFIG_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CONFIG_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
DATA_CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = PROJECT_ROOT / "logs"
REBALANCE_STATE_FILE = LOGS_DIR / "rebalance_state.json"

# 确保必要目录存在
DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# 2. 加载环境变量
ENV_FILE = PROJECT_ROOT / ".env"
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

# 3. 策略固定参数（第一版严禁修改优化）
UNIVERSE = ["SPY", "QQQ", "IWM", "EFA", "EEM", "GLD", "TLT", "IEF"]
BENCHMARK_SYMBOL = "SPY"

# 信号与过滤器参数
MOMENTUM_LOOKBACK_MONTHS = 12   # 12个月回溯
MOMENTUM_SKIP_MONTHS = 1       # 跳过最近1个月
TREND_SMA_MONTHS = 10          # 10个月移动平均趋势过滤
TOP_N = 3                      # 选前 N 只 ETF 等权配置

# 回测与执行参数
TRANSACTION_COST_BPS = 5.0     # 5 bps 交易成本 (0.05%)
CASH_RETURN = 0.0              # 现金收益率严格固定为 0%
CASH_BUFFER_PCT = 0.02         # 2% 现金缓冲，防止市价单滑点超支

# 4. Alpaca 模拟交易配置（强制 Paper-only）
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_PAPER = True            # 强制只允许 Paper Trading
