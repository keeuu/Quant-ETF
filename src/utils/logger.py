import logging
import sys
from pathlib import Path
from config.config import LOGS_DIR

def setup_logger(name: str = "quant_etf", log_file: str = "app.log", level: int = logging.INFO) -> logging.Logger:
    """
    配置并返回统一日志记录器，同时输出至控制台与日志文件。
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台 Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件 Handler
    file_path = LOGS_DIR / log_file
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

# 默认全局 logger
logger = setup_logger()
