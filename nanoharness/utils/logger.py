import logging


_DEFAULT_FORMAT = "[%(asctime)s] %(name)s %(levelname)s - %(message)s"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Get a logger with the project's standard format."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger
