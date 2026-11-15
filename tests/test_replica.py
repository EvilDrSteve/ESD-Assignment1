"""Replica naming (A3): container name from reverse DNS -> A, B, C."""

import logging

import pytest

from replica import replica_from_hostname


@pytest.mark.parametrize("name, expected", [
    ("assignment-1-app-1.assignment-1_default", "A"),
    ("assignment-1-app-2.assignment-1_default", "B"),
    ("assignment-1-app-3", "C"),
    ("3f2c9a1b7d4e", None),      # bare container id: no index
    ("assignment-1-app-0", None),
    ("assignment-1-app-27", None),
])
def test_replica_from_hostname(name, expected):
    assert replica_from_hostname(name) == expected


def test_every_log_line_has_replica(client, caplog):
    from logger import logger
    with caplog.at_level(logging.INFO, logger=logger.name):
        client.get("/products")
        logger.info("probe")  # any line, not only request logs
    assert len(caplog.records) >= 2
    assert all(record.replica == "T" for record in caplog.records)
