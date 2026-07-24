#!/usr/bin/env python3
import requests
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import time
import sqlite3
from urllib.parse import urlparse
import archive_playlist
import argparse


class DomainErrorTracker:
    """Class to track domain errors in a SQLite database"""

    def __init__(self, db_path="domain_errors.db"):
        self.db_path = db_path
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

    def extract_domain(self, url):
        """Extract domain from URL"""
        try:
            parsed = urlparse(url)
            return parsed.netloc.split(':')[0]
        except:
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

        self.conn.commit()

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

        self.conn.commit()

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
        """Close the database connection"""
        if self.conn:
            self.conn.close()


class M3UFilterConfig:
    """Class to handle JSON configuration for M3U filtering"""

    def __init__(self, config_file="m3u_filter_config.json"):
        # Check if config file exists
        if not Path(config_file).is_file():
            raise FileNotFoundError(f"Configuration file not found: {config_file}")

        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # Load settings
        self.top_fastest_count = config.get('settings', {}).get('top_fastest_count', 0)
        self.domain_error_threshold = config.get('settings', {}).get('domain_error_threshold', 0)

        # Load special group prefix/content filters for AND logic
        self.include_group_prefix = [x.upper() for x in config.get('include', {}).get('group_prefix', [])]
        self.include_group_content = [x.upper() for x in config.get('include', {}).get('group_content', [])]

        # Load exclude filters for groups
        self.exclude_group_exact = [x.upper() for x in config.get('exclude', {}).get('group', {}).get('exact_match', [])]
        self.exclude_group_contains = [x.upper() for x in config.get('exclude', {}).get('group', {}).get('contains', [])]
        self.exclude_group_startswith = [x.upper() for x in config.get('exclude', {}).get('group', {}).get('startswith', [])]
        self.exclude_group_regex = [re.compile(x, re.IGNORECASE) for x in config.get('exclude', {}).get('group', {}).get('regex', [])]

        # Load include filters for groups
        self.include_group_exact = [x.upper() for x in config.get('include', {}).get('group', {}).get('exact_match', [])]
        self.include_group_contains = [x.upper() for x in config.get('include', {}).get('group', {}).get('contains', [])]
        self.include_group_startswith = [x.upper() for x in config.get('include', {}).get('group', {}).get('startswith', [])]
        self.include_group_regex = [re.compile(x, re.IGNORECASE) for x in config.get('include', {}).get('group', {}).get('regex', [])]

        # Load exclude filters for channels
        self.exclude_channel_exact = [x.upper() for x in config.get('exclude', {}).get('channel', {}).get('exact_match', [])]
        self.exclude_channel_contains = [x.upper() for x in config.get('exclude', {}).get('channel', {}).get('contains', [])]
        self.exclude_channel_startswith = [x.upper() for x in config.get('exclude', {}).get('channel', {}).get('startswith', [])]
        self.exclude_channel_regex = [re.compile(x, re.IGNORECASE) for x in config.get('exclude', {}).get('channel', {}).get('regex', [])]

        # Load include filters for channels
        self.include_channel_exact = [x.upper() for x in config.get('include', {}).get('channel', {}).get('exact_match', [])]
        self.include_channel_contains = [x.upper() for x in config.get('include', {}).get('channel', {}).get('contains', [])]
        self.include_channel_startswith = [x.upper() for x in config.get('include', {}).get('channel', {}).get('startswith', [])]
        self.include_channel_regex = [re.compile(x, re.IGNORECASE) for x in config.get('include', {}).get('channel', {}).get('regex', [])]

        # Load exclude filters for tvg-name
        self.exclude_tvg_name_exact = [x.upper() for x in config.get('exclude', {}).get('tvg_name', {}).get('exact_match', [])]
        self.exclude_tvg_name_contains = [x.upper() for x in config.get('exclude', {}).get('tvg_name', {}).get('contains', [])]
        self.exclude_tvg_name_startswith = [x.upper() for x in config.get('exclude', {}).get('tvg_name', {}).get('startswith', [])]
        self.exclude_tvg_name_regex = [re.compile(x, re.IGNORECASE) for x in config.get('exclude', {}).get('tvg_name', {}).get('regex', [])]

        # Load exclude filters for URL domain
        self.exclude_url_domain_contains = [x.lower() for x in config.get('exclude', {}).get('url_domain', {}).get('contains', [])]

        # Load include filters for tvg-name
        self.include_tvg_name_exact = [x.upper() for x in config.get('include', {}).get('tvg_name', {}).get('exact_match', [])]
        self.include_tvg_name_contains = [x.upper() for x in config.get('include', {}).get('tvg_name', {}).get('contains', [])]
        self.include_tvg_name_startswith = [x.upper() for x in config.get('include', {}).get('tvg_name', {}).get('startswith', [])]
        self.include_tvg_name_regex = [re.compile(x, re.IGNORECASE) for x in config.get('include', {}).get('tvg_name', {}).get('regex', [])]

    def should_exclude_by_group(self, group_name):
        """Check if group should be excluded based on filters"""
        group_upper = group_name.upper()

        # Check exact match
        if group_upper in self.exclude_group_exact:
            return True, 'exact-match'

        # Check contains
        for pattern in self.exclude_group_contains:
            if pattern in group_upper:
                return True, f'contains-{pattern}'

        # Check starts with
        for pattern in self.exclude_group_startswith:
            if group_upper.startswith(pattern):
                return True, f'startswith-{pattern}'

        # Check regex
        for pattern in self.exclude_group_regex:
            if pattern.search(group_name):
                return True, f'regex-{pattern.pattern}'

        return False, None

    def should_exclude_by_channel(self, channel_name):
        """Check if channel should be excluded based on filters"""
        channel_upper = channel_name.upper()

        # Check exact match
        if channel_upper in self.exclude_channel_exact:
            return True, 'exact-match'

        # Check contains
        for pattern in self.exclude_channel_contains:
            if pattern in channel_upper:
                return True, f'contains-{pattern}'

        # Check starts with
        for pattern in self.exclude_channel_startswith:
            if channel_upper.startswith(pattern):
                return True, f'startswith-{pattern}'

        # Check regex
        for pattern in self.exclude_channel_regex:
            if pattern.search(channel_name):
                return True, f'regex-{pattern.pattern}'

        return False, None

    def should_exclude_by_tvg_name(self, tvg_name):
        """Check if tvg-name should be excluded based on filters"""
        tvg_name_upper = tvg_name.upper()

        # Check exact match
        if tvg_name_upper in self.exclude_tvg_name_exact:
            return True, 'exact-match'

        # Check contains
        for pattern in self.exclude_tvg_name_contains:
            if pattern in tvg_name_upper:
                return True, f'contains-{pattern}'

        # Check starts with
        for pattern in self.exclude_tvg_name_startswith:
            if tvg_name_upper.startswith(pattern):
                return True, f'startswith-{pattern}'

        # Check regex
        for pattern in self.exclude_tvg_name_regex:
            if pattern.search(tvg_name):
                return True, f'regex-{pattern.pattern}'

        return False, None

    def should_include_by_group(self, group_name):
        """Check if group should be included based on filters"""
        # If no include filters are defined, include everything
        if not self.include_group_exact and not self.include_group_contains and not self.include_group_startswith and not self.include_group_regex:
            return True, None

        group_upper = group_name.upper()

        # Check exact match
        if self.include_group_exact and group_upper in self.include_group_exact:
            return True, 'exact-match'

        # Check contains
        if self.include_group_contains:
            for pattern in self.include_group_contains:
                if pattern in group_upper:
                    return True, f'contains-{pattern}'

        # Check starts with
        if self.include_group_startswith:
            for pattern in self.include_group_startswith:
                if group_upper.startswith(pattern):
                    return True, f'startswith-{pattern}'

        # Check regex
        if self.include_group_regex:
            for pattern in self.include_group_regex:
                if pattern.search(group_name):
                    return True, f'regex-{pattern.pattern}'

        # If include filters exist but nothing matched, exclude
        return False, None

    def should_include_by_channel(self, channel_name):
        """Check if channel should be included based on filters"""
        # If no include filters are defined, include everything
        if not self.include_channel_exact and not self.include_channel_contains and not self.include_channel_startswith and not self.include_channel_regex:
            return True, None

        channel_upper = channel_name.upper()

        # Check exact match
        if self.include_channel_exact and channel_upper in self.include_channel_exact:
            return True, 'exact-match'

        # Check contains
        if self.include_channel_contains:
            for pattern in self.include_channel_contains:
                if pattern in channel_upper:
                    return True, f'contains-{pattern}'

        # Check starts with
        if self.include_channel_startswith:
            for pattern in self.include_channel_startswith:
                if channel_upper.startswith(pattern):
                    return True, f'startswith-{pattern}'

        # Check regex
        if self.include_channel_regex:
            for pattern in self.include_channel_regex:
                if pattern.search(channel_name):
                    return True, f'regex-{pattern.pattern}'

        # If include filters exist but nothing matched, exclude
        return False, None

    def should_include_by_tvg_name(self, tvg_name):
        """Check if tvg-name should be included based on filters"""
        # If no include filters are defined, include everything
        if not self.include_tvg_name_exact and not self.include_tvg_name_contains and not self.include_tvg_name_startswith and not self.include_tvg_name_regex:
            return True, None

        tvg_name_upper = tvg_name.upper()

        # Check exact match
        if self.include_tvg_name_exact and tvg_name_upper in self.include_tvg_name_exact:
            return True, 'exact-match'

        # Check contains
        if self.include_tvg_name_contains:
            for pattern in self.include_tvg_name_contains:
                if pattern in tvg_name_upper:
                    return True, f'contains-{pattern}'

        # Check starts with
        if self.include_tvg_name_startswith:
            for pattern in self.include_tvg_name_startswith:
                if tvg_name_upper.startswith(pattern):
                    return True, f'startswith-{pattern}'

        # Check regex
        if self.include_tvg_name_regex:
            for pattern in self.include_tvg_name_regex:
                if pattern.search(tvg_name):
                    return True, f'regex-{pattern.pattern}'

        # If include filters exist but nothing matched, exclude
        return False, None

    def is_entry_allowed(self, entry):
        """Check if an entry passes all filters (both include and exclude)"""
        group_name = entry.get('group', '')
        channel_name = entry.get('name', '')
        tvg_name = entry.get('tvg_name', '')
        url = entry.get('url', '').lower()

        # STEP 1: Check excludes FIRST (they take priority) - OR logic
        # If excluded by ANY field, reject immediately
        is_excluded, reason = self.should_exclude_by_group(group_name)
        if is_excluded:
            return False, f'excluded-group-{reason}'

        is_excluded, reason = self.should_exclude_by_channel(channel_name)
        if is_excluded:
            return False, f'excluded-channel-{reason}'

        is_excluded, reason = self.should_exclude_by_tvg_name(tvg_name)
        if is_excluded:
            return False, f'excluded-tvg-name-{reason}'

        # Check URL domain exclusions
        for domain in self.exclude_url_domain_contains:
            if domain in url:
                return False, f'excluded-url-domain-{domain}'

        # STEP 2: Check includes - AND logic
        # First check special group prefix/content filters (both must match)
        if self.include_group_prefix and self.include_group_content:
            group_upper = group_name.upper()

            # Check if group starts with any prefix
            has_prefix = any(group_upper.startswith(prefix) for prefix in self.include_group_prefix)

            # Check if group contains any content keyword
            has_content = any(content in group_upper for content in self.include_group_content)

            # Both must match
            if not (has_prefix and has_content):
                return False, 'group-prefix-content-not-matched'

            return True, 'allowed'

        # Fallback to regular include filters with AND logic
        # Entry must match ALL fields that have include filters defined
        has_group_include_filters = (
            self.include_group_exact or self.include_group_contains or self.include_group_startswith or self.include_group_regex
        )
        has_channel_include_filters = (
            self.include_channel_exact or self.include_channel_contains or self.include_channel_startswith or self.include_channel_regex
        )
        has_tvg_name_include_filters = (
            self.include_tvg_name_exact or self.include_tvg_name_contains or self.include_tvg_name_startswith or self.include_tvg_name_regex
        )

        has_any_include_filters = (
            has_group_include_filters or has_channel_include_filters or has_tvg_name_include_filters
        )

        if has_any_include_filters:
            # Check if entry matches ANY field that has include filters defined (OR logic)
            matches = []

            if has_group_include_filters:
                group_included, _ = self.should_include_by_group(group_name)
                matches.append(group_included)

            if has_channel_include_filters:
                channel_included, _ = self.should_include_by_channel(channel_name)
                matches.append(channel_included)

            if has_tvg_name_include_filters:
                tvg_name_included, _ = self.should_include_by_tvg_name(tvg_name)
                matches.append(tvg_name_included)

            # If ANY field matches, allow it
            if any(matches):
                return True, 'allowed'
            else:
                return False, 'not-in-any-include-filter'

        # If no include filters defined, allow all (that weren't excluded)
        return True, 'allowed'


def download_m3u(url, cache_dir="cache", error_tracker=None):
    """Download M3U file and cache it"""
    Path(cache_dir).mkdir(exist_ok=True)

    # Create a hash of the URL for the filename
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_file = Path(cache_dir) / f"{url_hash}.m3u"

    try:
        print(f"Downloading: {url[:60]}...")
        # Decode HTML entities
        url = url.replace("&amp;", "&")
        response = requests.get(url, timeout=30)
        response.raise_for_status()

        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(response.text)

        print(f"  ✓ Cached to {cache_file}")

        # Record success
        if error_tracker:
            error_tracker.record_success(url)

        return cache_file
    except Exception as e:
        print(f"  ✗ Error: {e}")

        # Record error
        if error_tracker:
            error_type = type(e).__name__
            error_tracker.record_error(url, error_type, str(e))

        return None

def test_url_speed(url, timeout=10, error_tracker=None):
    """Test URL response speed and return response time in seconds"""
    try:
        # Decode HTML entities
        url = url.replace("&amp;", "&")
        start_time = time.time()
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        response_time = time.time() - start_time

        if response.status_code == 200:
            # Record success
            if error_tracker:
                error_tracker.record_success(url)
            return response_time
        else:
            # Record error for non-200 responses
            if error_tracker:
                error_tracker.record_error(url, 'HTTPError', f'Status code: {response.status_code}')
            return float('inf')  # Failed URLs get infinite time
    except Exception as e:
        # Record error
        if error_tracker:
            error_type = type(e).__name__
            error_tracker.record_error(url, error_type, str(e))
        return float('inf')  # Failed URLs get infinite time

def parse_m3u(file_path):
    """Parse M3U file and extract entries"""
    entries = []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Split by #EXTINF
        lines = content.split('#EXTINF:')

        for i in range(1, len(lines)):
            entry_lines = lines[i].strip().split('\n', 1)
            if len(entry_lines) < 2:
                continue

            info_line = entry_lines[0]
            url_line = entry_lines[1].strip()

            # Extract group title
            group_match = re.search(r'group-title="([^"]*)"', info_line)
            group = group_match.group(1) if group_match else ""

            # Extract tvg-name
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', info_line)
            tvg_name = tvg_name_match.group(1) if tvg_name_match else ""

            # Extract channel name (after last comma)
            name_parts = info_line.split(',')
            name = name_parts[-1].strip() if name_parts else ""

            entries.append({
                'info': info_line,
                'url': url_line,
                'name': name,
                'group': group,
                'tvg_name': tvg_name
            })

        return entries
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []


def create_master_m3u(entries):
    """Create master M3U file from filtered entries"""

    # Remove duplicates by channel name
    seen_names = set()
    unique_entries = []

    for entry in entries:
        name_key = entry['name'].strip().lower()
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_entries.append(entry)

    # Create M3U content
    m3u_content = "#EXTM3U\n\n"

    for entry in unique_entries:
        # Remove everything after the last comma in the info line
        # This removes the channel name part, keeping only the attributes
        info_line = entry['info']
        last_comma_index = info_line.rfind(',')
        if last_comma_index != -1:
            # Keep only the part before the comma (attributes only)
            info_line = info_line[:last_comma_index]

        m3u_content += f"#EXTINF:{info_line}\n"
        m3u_content += f"{entry['url']}\n\n"

    # Save master M3U
    with open('master_iptv.m3u', 'w', encoding='utf-8') as f:
        f.write(m3u_content)

    return len(unique_entries)

def archive_output():
    """Archive master_iptv.m3u to archive/playlist_YYYYMMDD.m3u and rotate backups"""
    print("\n" + "="*60)
    print("Archiving...")
    print("="*60)
    
    source_file = Path("master_iptv.m3u")
    archive_dir = Path("archive")
    max_backups = 10

    if not source_file.exists():
        print(f"Error: Source file '{source_file}' does not exist.")
        return

    # Create archive directory
    archive_dir.mkdir(exist_ok=True)

    # Generate destination filename
    date_str = datetime.now().strftime("%Y%m%d")
    dest_filename = f"playlist_{date_str}.m3u"
    dest_path = archive_dir / dest_filename

    # Copy file
    shutil.copy2(source_file, dest_path)
    print(f"✓ Archived '{source_file}' to '{dest_path}'")

    # Rotation logic (Keep last N)
    # Get all playlist files in archive
    backups = sorted(archive_dir.glob("playlist_*.m3u"))
    
    # If we have more than max_backups
    if len(backups) > max_backups:
        # Calculate how many to remove
        num_to_remove = len(backups) - max_backups
        files_to_remove = backups[:num_to_remove]
        
        print(f"Rotating archives (keeping last {max_backups})...")
        for backup in files_to_remove:
            try:
                backup.unlink()
                print(f"  - Removed old backup: {backup.name}")
            except Exception as e:
                print(f"  ! Failed to remove {backup.name}: {e}")

def cleanup_cache(cache_dir="cache"):
    """Delete all files in the cache directory"""
    print(f"\nCleaning up cache directory: {cache_dir}...")
    cache_path = Path(cache_dir)
    
    if not cache_path.exists():
        print(f"  Cache directory does not exist.")
        return

    try:
        shutil.rmtree(cache_path)
        # Recreate empty directory
        cache_path.mkdir(exist_ok=True)
        print(f"  ✓ Cache cleaned.")
    except Exception as e:
        print(f"  ✗ Failed to clean cache: {e}")

def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Process IPTV M3U files")
    parser.add_argument("--input", default="urls.txt", help="Input file with URLs")
    parser.add_argument("--config", default="m3u_filter_config.json", help="Filter config file")
    args = parser.parse_args()

    # Clean cache at startup
    cleanup_cache()

    # Initialize domain error tracker
    error_tracker = DomainErrorTracker()

    # Load filter configuration
    config_file = args.config
    print(f"Loading filter configuration from {config_file}...")
    try:
        filter_config = M3UFilterConfig(config_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Processing stopped.")
        error_tracker.close()
        return

    print(f"  Exclude Group Patterns: exact={len(filter_config.exclude_group_exact)}, "
          f"contains={len(filter_config.exclude_group_contains)}, "
          f"startswith={len(filter_config.exclude_group_startswith)}")
    print(f"  Include Group Patterns: exact={len(filter_config.include_group_exact)}, "
          f"contains={len(filter_config.include_group_contains)}, "
          f"startswith={len(filter_config.include_group_startswith)}")
    print(f"  Top Fastest URLs: {filter_config.top_fastest_count if filter_config.top_fastest_count > 0 else 'all'}")
    print(f"  Domain Error Threshold: {filter_config.domain_error_threshold if filter_config.domain_error_threshold > 0 else 'disabled'}")

    # Load URLs from text file (all alive URLs)
    input_file = args.input
    print(f"\nLoading URLs from {input_file}...")
    try:
        with open(input_file, 'r') as f:
            all_urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: Input file {input_file} not found.")
        error_tracker.close()
        return

    print(f"Found {len(all_urls)} URLs")

    # Filter out URLs containing excluded domains
    urls = []
    excluded_url_count = 0
    skipped_error_threshold_count = 0
    for url in all_urls:
        url_lower = url.lower()
        is_excluded = any(domain in url_lower for domain in filter_config.exclude_url_domain_contains)
        if is_excluded:
            excluded_url_count += 1
            continue

        # Check if domain should be skipped based on error threshold
        if error_tracker.should_skip_domain(url, filter_config.domain_error_threshold):
            domain = error_tracker.extract_domain(url)
            stats = error_tracker.get_domain_stats(domain)
            print(f"  Skipping domain {domain} (errors: {stats['error_count']}, threshold: {filter_config.domain_error_threshold})")
            skipped_error_threshold_count += 1
            continue

        urls.append(url)

    if excluded_url_count > 0:
        print(f"Excluded {excluded_url_count} URLs based on domain filters")
    if skipped_error_threshold_count > 0:
        print(f"Skipped {skipped_error_threshold_count} URLs based on error threshold")
    print(f"Processing {len(urls)} URLs")

    # Test URL speeds if top_fastest_count is set
    if filter_config.top_fastest_count > 0 and len(urls) > filter_config.top_fastest_count:
        print(f"\nTesting URL speeds to select top {filter_config.top_fastest_count} fastest...")
        url_speeds = []

        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{len(urls)}] Testing {url[:60]}...", end=' ')
            speed = test_url_speed(url, error_tracker=error_tracker)
            if speed == float('inf'):
                print("✗ Failed")
            else:
                print(f"✓ {speed:.2f}s")
            url_speeds.append((url, speed))

        # Sort by speed and take top N
        url_speeds.sort(key=lambda x: x[1])
        urls = [url for url, speed in url_speeds[:filter_config.top_fastest_count]]
        print(f"\nSelected top {len(urls)} fastest URLs\n")
    else:
        print(f"Using all {len(urls)} URLs\n")

    all_entries = []

    # Download and process each M3U
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] Processing M3U...")

        # Download M3U
        cache_file = download_m3u(url, error_tracker=error_tracker)
        if not cache_file:
            continue

        # Parse M3U
        entries = parse_m3u(cache_file)
        print(f"  Found {len(entries)} total entries")

        # Filter entries
        allowed_count = 0
        excluded_count = 0

        for entry in entries:
            # Apply filter configuration
            is_allowed, reason = filter_config.is_entry_allowed(entry)

            if not is_allowed:
                excluded_count += 1
                continue

            all_entries.append(entry)
            allowed_count += 1

        print(f"  - Allowed entries: {allowed_count}")
        print(f"  - Excluded by filters: {excluded_count}")

    # Archive the current master playlist (if it exists) before creating a new one
    archive_playlist.archive_playlist()

    # Create master M3U
    print("\n" + "="*60)
    print("Creating master M3U file...")
    total_entries = create_master_m3u(all_entries)

    print(f"\nMaster M3U created with:")
    print(f"  - Total unique entries: {total_entries}")

    print("\nFiles created:")
    print("  - master_iptv.m3u (filtered M3U file)")
    print("  - cache/ directory (downloaded M3U files)")

    # Display domain statistics
    print("\n" + "="*60)
    print("Domain Error Statistics:")
    print("="*60)

    all_stats = error_tracker.get_all_domain_stats()
    if all_stats:
        print(f"\n{'Domain':<40} {'Errors':<8} {'Success':<8} {'Last Error':<20}")
        print("-" * 80)
        for stats in all_stats[:20]:  # Show top 20 domains with most errors
            last_error = stats['last_error_time'][:19] if stats['last_error_time'] else 'Never'
            print(f"{stats['domain']:<40} {stats['error_count']:<8} {stats['success_count']:<8} {last_error:<20}")

        if len(all_stats) > 20:
            print(f"\n... and {len(all_stats) - 20} more domains")
    else:
        print("No domain statistics available yet.")

    # Close database connection
    error_tracker.close()

    # Clean cache at the end
    cleanup_cache()

if __name__ == "__main__":
    main()
