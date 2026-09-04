import logging
from typing import Any


class KeywordLoggerAdapter:
    """Small structlog-like adapter for environments without structlog."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _render(self, event: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        message = event
        if args:
            try:
                message = event % args
            except Exception:
                message = " ".join([event, *(repr(arg) for arg in args)])
        if kwargs:
            extra = " ".join(f"{key}={value!r}" for key, value in kwargs.items())
            message = f"{message} {extra}"
        return message

    def debug(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._logger.debug(self._render(event, args, kwargs))

    def info(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._logger.info(self._render(event, args, kwargs))

    def warning(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._logger.warning(self._render(event, args, kwargs))

    def error(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._logger.error(self._render(event, args, kwargs))

    def exception(self, event: str, *args: Any, **kwargs: Any) -> None:
        self._logger.exception(self._render(event, args, kwargs))


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        import structlog
    except ModuleNotFoundError:
        return

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str | None = None):
    try:
        import structlog
    except ModuleNotFoundError:
        return KeywordLoggerAdapter(logging.getLogger(name or "agent_factory"))
    return structlog.get_logger(name)