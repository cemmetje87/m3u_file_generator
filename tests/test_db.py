import sqlite3
import tempfile
from pathlib import Path

import pytest

from db import DomainErrorTracker


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_domain_errors.db"


def test_record_error_increments_count(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        tracker.record_error("http://example.com/x", error_type="timeout", error_message="slow")
        stats = tracker.get_domain_stats("example.com")
        assert stats is not None
        assert stats["error_count"] == 1
        assert stats["success_count"] == 0
        assert stats["last_error_time"] is not None
    finally:
        tracker.close()


def test_record_error_multiple_times(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        for _ in range(3):
            tracker.record_error("http://example.com/x")
        stats = tracker.get_domain_stats("example.com")
        assert stats["error_count"] == 3
    finally:
        tracker.close()


def test_record_success(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        tracker.record_success("http://example.com/x")
        stats = tracker.get_domain_stats("example.com")
        assert stats["success_count"] == 1
    finally:
        tracker.close()


def test_should_skip_domain_below_threshold(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        for _ in range(2):
            tracker.record_error("http://example.com/x")
        assert tracker.should_skip_domain("http://example.com/x", error_threshold=5) is False
    finally:
        tracker.close()


def test_should_skip_domain_at_or_above_threshold(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        for _ in range(5):
            tracker.record_error("http://example.com/x")
        assert tracker.should_skip_domain("http://example.com/x", error_threshold=5) is True
    finally:
        tracker.close()


def test_should_skip_domain_threshold_zero_disables(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        for _ in range(100):
            tracker.record_error("http://example.com/x")
        assert tracker.should_skip_domain("http://example.com/x", error_threshold=0) is False
    finally:
        tracker.close()


def test_invalid_url_does_not_crash(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        tracker.record_error("not a url")
        stats = tracker.get_domain_stats("not a url")
        assert stats is None
    finally:
        tracker.close()


def test_reset_domain_errors(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        for _ in range(3):
            tracker.record_error("http://example.com/x")
        tracker.reset_domain_errors("example.com")
        stats = tracker.get_domain_stats("example.com")
        assert stats["error_count"] == 0
    finally:
        tracker.close()


def test_get_all_domain_stats(db_path):
    tracker = DomainErrorTracker(str(db_path))
    try:
        tracker.record_error("http://a.example/x")
        tracker.record_error("http://a.example/x")
        tracker.record_error("http://b.example/x")
        all_stats = tracker.get_all_domain_stats()
        assert len(all_stats) == 2
    finally:
        tracker.close()
