import logging
import sys
from pathlib import Path
from config.config import LOGS_DIR


def setup_logger(name: str = "quant_etf", log_file: str = "app.log", level: int = logging.INFO) -> logging.Logger:
    """配置并返回统一日志记录器，同时输出至控制台与日志文件。"""
    _logger = logging.getLogger(name)
    if _logger.handlers:
        return _logger
    _logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    _logger.addHandler(console_handler)
    file_path = LOGS_DIR / log_file
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)
    return _logger


def configure_production_logging(log_file: str = "app.log", level: int = logging.INFO) -> None:
    """显式初始化生产日志（FileHandler 写 logs/app.log）。
    仅供 scripts/run_paper.py 等 production entrypoint 调用。
    单元测试不得调用本函数。
    """
    setup_logger(name="quant_etf", log_file=log_file, level=level)


def configure_test_logging(log_dir: Path, log_file: str = "test_app.log", level: int = logging.INFO) -> None:
    """将日志重定向到测试临时目录，防止污染 logs/app.log。
    供 tests/conftest.py 调用。
    """
    _logger = logging.getLogger("quant_etf")
    # 移除已有的 FileHandler（如有），添加新的 tmp 路径 FileHandler
    for h in list(_logger.handlers):
        if isinstance(h, logging.FileHandler):
            h.close()
            _logger.removeHandler(h)
    if not _logger.handlers:
        _logger.setLevel(level)
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        _logger.addHandler(console_handler)
    file_path = log_dir / log_file
    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] [%(name)s:%(lineno)d]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(file_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)


def _get_null_logger(name: str = "quant_etf") -> logging.Logger:
    """返回只带 NullHandler 的 logger（import 安全，不写任何文件）。"""
    _logger = logging.getLogger(name)
    if not _logger.handlers:
        _logger.addHandler(logging.NullHandler())
        _logger.setLevel(logging.DEBUG)
    return _logger


# 模块级 logger：import 时只带 NullHandler，不写任何文件。
# production 调用 configure_production_logging() 后开始写 logs/app.log。
# 测试调用 configure_test_logging(tmp_path) 后写临时目录。
logger = _get_null_logger()
