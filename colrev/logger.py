#! /usr/bin/env python
"""Loggers for CoLRev"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import colrev.review_manager


def setup_logger(
    *, review_manager: colrev.review_manager.ReviewManager, level=logging.INFO
) -> logging.Logger:
    # for logger debugging:
    # from logging_tree import printout
    # printout()
    logger = logging.getLogger(f"colrev{str(review_manager.path).replace('/', '_')}")
    logger.setLevel(level)

    if logger.handlers:
        for handler in logger.handlers:
            logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(level)

    logger.addHandler(handler)
    logger.propagate = False

    return logger


def setup_report_logger(
    *, review_manager: colrev.review_manager.ReviewManager, level=logging.INFO
) -> logging.Logger:
    report_logger = logging.getLogger(
        f"colrev_report{str(review_manager.path).replace('/', '_')}"
    )

    if report_logger.handlers:
        for handler in report_logger.handlers:
            report_logger.removeHandler(handler)

    report_logger.setLevel(level)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    report_file_handler = logging.FileHandler(review_manager.report_path, mode="a")
    report_file_handler.setFormatter(formatter)

    report_logger.addHandler(report_file_handler)

    if logging.DEBUG == level:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        report_logger.addHandler(handler)
    report_logger.propagate = False

    return report_logger


def reset_log(*, review_manager: colrev.review_manager.ReviewManager) -> None:
    review_manager.report_logger.handlers[0].stream.close()  # type: ignore
    review_manager.report_logger.removeHandler(review_manager.report_logger.handlers[0])

    with open(review_manager.report_path, "r+", encoding="utf8") as file:
        file.truncate(0)

    file_handler = logging.FileHandler(review_manager.report_path)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    review_manager.report_logger.addHandler(file_handler)


if __name__ == "__main__":
    pass