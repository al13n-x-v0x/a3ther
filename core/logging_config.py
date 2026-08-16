import logging
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Configure simple structured logging for the application."""
    fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    logging.basicConfig(stream=sys.stdout, level=level, format=fmt)


def get_logger(name: str):
    return logging.getLogger(name)


# Auto-configure when imported from main entrypoints
configure_logging()
