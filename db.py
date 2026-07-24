#!/usr/bin/env python3
"""SQLite-backed tracking of per-domain download errors.

Domains that repeatedly fail are skipped in future runs so processing time
isn't wasted on unresponsive sources. Error/success counts are persisted in
domain_errors.db (gitignored - local runtime state only).
"""
import sqlite3
from datetime import datetime
from urllib.parse import urlparse


class DomainErrorTracker:
    """Track domain errors in a SQLite database.

    Commits are batched (every `commit_every` writes and on close()) instead
    of per-record to avoid write amplification during large runs.
    """

    def __init__(self, db_path="domain_errors.db", commit_every=50):
        self.db_path = db_path
        self.commit_every = commit_every
        self._pending_writes = 0
        self.conn = None
        self._init_database()

    def _init_database(self):
        """Initialize the SQLite database with the required schema"""
        self.conn = sqlite3.connect(self.db_path)
        cursor = self.conn.cursor()

        # Create domain_errors table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS domain_errors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL UNIQUE,
                error_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                last_error_time TEXT,
                last_success_time TEXT,
                first_seen TEXT,
                last_checked TEXT
            )
        ''')

        # Create error_log table for detailed history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domain TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                error_type TEXT,
                error_message TEXT,
                FOREIGN KEY (domain) REFERENCES domain_errors(domain)
            )
        ''')

        self.conn.commit()

    def _maybe_commit(self):
        """Commit only after a batch of writes to reduce write amplification."""
        self._pending_writes += 1
        if self._pending_writes >= self.commit_every:
            self.conn.commit()
            self._pending_writes = 0

    def extract_domain(self, url):
        """Extract domain from URL"""
        try:
            parsed = urlparse(url)
            return parsed.netloc.split(':')[0]
        except Exception:
            return None

    def record_error(self, url, error_type=None, error_message=None):
        """Record an error for a domain"""
        domain = self.extract_domain(url)
        if not domain:
            return

        cursor = self.conn.cursor()
        timestamp = datetime.now().isoformat()

        # Update or insert domain_errors
        cursor.execute('''
            INSERT INTO domain_errors (domain, error_count, last_error_time, first_seen, last_checked)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                error_count = error_count + 1,
                last_error_time = ?,
                last_checked = ?
        ''', (domain, timestamp, timestamp, timestamp, timestamp, timestamp))

        # Log the error
        cursor.execute('''
            INSERT INTO error_log (domain, timestamp, error_type, error_message)
            VALUES (?, ?, ?, ?)
        ''', (domain, timestamp, error_type, str(error_message)[:500] if error_message else None))

        self._maybe_commit()

    def record_success(self, url):
        """Record a successful response for a domain"""
        domain = self.extract_domain(url)
        if not domain:
            return

        cursor = self.conn.cursor()
        timestamp = datetime.now().isoformat()

        # Update or insert domain_errors
        cursor.execute('''
            INSERT INTO domain_errors (domain, success_count, last_success_time, first_seen, last_checked)
            VALUES (?, 1, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                success_count = success_count + 1,
                last_success_time = ?,
                last_checked = ?
        ''', (domain, timestamp, timestamp, timestamp, timestamp, timestamp))

        self._maybe_commit()

    def get_domain_stats(self, domain):
        """Get error statistics for a domain"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT error_count, success_count, last_error_time, last_success_time
            FROM domain_errors
            WHERE domain = ?
        ''', (domain,))

        result = cursor.fetchone()
        if result:
            return {
                'error_count': result[0],
                'success_count': result[1],
                'last_error_time': result[2],
                'last_success_time': result[3]
            }
        return None

    def should_skip_domain(self, url, error_threshold):
        """Check if a domain should be skipped based on error threshold"""
        domain = self.extract_domain(url)
        if not domain or error_threshold <= 0:
            return False

        stats = self.get_domain_stats(domain)
        if stats and stats['error_count'] >= error_threshold:
            return True

        return False

    def get_all_domain_stats(self):
        """Get statistics for all domains"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT domain, error_count, success_count, last_error_time, last_success_time, last_checked
            FROM domain_errors
            ORDER BY error_count DESC
        ''')

        results = cursor.fetchall()
        return [{
            'domain': row[0],
            'error_count': row[1],
            'success_count': row[2],
            'last_error_time': row[3],
            'last_success_time': row[4],
            'last_checked': row[5]
        } for row in results]

    def reset_domain_errors(self, domain):
        """Reset error count for a specific domain"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE domain_errors
            SET error_count = 0, last_error_time = NULL
            WHERE domain = ?
        ''', (domain,))
        self.conn.commit()

    def close(self):
        """Flush pending writes and close the database connection"""
        if self.conn:
            self.conn.commit()
            self.conn.close()
