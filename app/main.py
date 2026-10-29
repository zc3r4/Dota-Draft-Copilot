"""Application entry point: ``python -m app.main``."""
from __future__ import annotations

import argparse
import logging
import os
import sys
import traceback
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from . import __version__
from .config import LOG_DIR, Config, ensure_dirs
from .theme import QSS
from .ui import MainWindow

LOGGER = logging.getLogger("dota_draft_copilot")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dota-draft-copilot",
        description="Помощник драфта Dota 2: контрпики, предметы и нейтральные предметы.",
    )
    parser.add_argument("--offline", action="store_true", help="не обращаться к сети (только кэш)")
    parser.add_argument("--no-gsi", action="store_true", help="не запускать локальный GSI-сервер")
    parser.add_argument("--port", type=int, default=None, help="порт GSI (по умолчанию из config.json)")
    parser.add_argument("--debug", action="store_true", help="подробный лог")
    return parser.parse_args(argv)


def _setup_logging(debug: bool) -> None:
    ensure_dirs()
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        file_handler = RotatingFileHandler(
            LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        file_handler = None
    if file_handler is not None:
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        root.addHandler(file_handler)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    stream.setLevel(level)
    root.addHandler(stream)


def _install_exception_hook() -> None:
    def hook(exc_type, exc_value, exc_tb):  # noqa: ANN001 - sys.excepthook signature
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        LOGGER.error("Необработанная ошибка:\n%s", text)
        app = QApplication.instance()
        if app is not None:
            QMessageBox.critical(
                None,
                "Dota Draft Copilot",
                "Произошла непредвиденная ошибка. Подробности записаны в data/logs/app.log.\n\n"
                f"{exc_value}",
            )

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.debug)
    _install_exception_hook()

    if args.offline:
        os.environ["DDC_OFFLINE"] = "1"
    if args.no_gsi:
        os.environ["DDC_NO_GSI"] = "1"

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except (AttributeError, RuntimeError):
        pass

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Dota Draft Copilot")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("DotaDraftCopilot")
    app.setStyleSheet(QSS)

    config = Config.load()
    if args.port:
        config.gsi_port = int(args.port)
    LOGGER.info("Запуск %s, офлайн=%s, GSI=%s", __version__, args.offline, not args.no_gsi)

    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
