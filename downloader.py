#!/usr/bin/env python3
"""Download and speed-test M3U playlist URLs.

Every URL is validated against netguard before fetching to block SSRF to
internal/metadata addresses. Credentials embedded in get.php URLs are
redacted from log output.
"""
import hashlib
import re
import time
from html import unescape
from pathlib import Path

import requests

from netguard import is_public_http_url

USER_AGENT = "m3u-file-generator/0.1 (+https://github.com/cemmetje87/m3u_file_generator)"
DEFAULT_HEADERS = {"User-Agent": USER_AGENT}

_CRED_RE = re.compile(r'(username=)[^&\s]+(&(?:password=)[^&\s]+)', re.IGNORECASE)


def redact_url(url: str) -> str:
    """Mask username/password query params for safe logging."""
    return _CRED_RE.sub(r'\1***\2***', url)


def download_m3u(url, cache_dir="cache", error_tracker=None):
    """Download M3U file and cache it"""
    Path(cache_dir).mkdir(exist_ok=True)

    # Create a hash of the URL for the filename
    url_hash = hashlib.md5(url.encode()).hexdigest()
    cache_file = Path(cache_dir) / f"{url_hash}.m3u"

    try:
        safe_url = unescape(url)

        is_safe, reason = is_public_http_url(safe_url)
        if not is_safe:
            print(f"  ✗ Blocked unsafe URL ({reason}): {redact_url(safe_url)}")
            if error_tracker:
                error_tracker.record_error(safe_url, 'SSRFBlocked', reason)
            return None

        print(f"Downloading: {redact_url(safe_url[:80])}...")
        response = requests.get(safe_url, timeout=30, headers=DEFAULT_HEADERS)
        response.raise_for_status()

        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(response.text)

        print(f"  ✓ Cached to {cache_file}")

        # Record success
        if error_tracker:
            error_tracker.record_success(safe_url)

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
        safe_url = unescape(url)

        is_safe, reason = is_public_http_url(safe_url)
        if not is_safe:
            if error_tracker:
                error_tracker.record_error(safe_url, 'SSRFBlocked', reason)
            return float('inf')

        start_time = time.time()
        response = requests.head(safe_url, timeout=timeout, allow_redirects=True, headers=DEFAULT_HEADERS)
        response_time = time.time() - start_time

        if response.status_code == 200:
            # Record success
            if error_tracker:
                error_tracker.record_success(safe_url)
            return response_time
        else:
            # Record error for non-200 responses
            if error_tracker:
                error_tracker.record_error(safe_url, 'HTTPError', f'Status code: {response.status_code}')
            return float('inf')  # Failed URLs get infinite time
    except Exception as e:
        # Record error
        if error_tracker:
            error_type = type(e).__name__
            error_tracker.record_error(url, error_type, str(e))
        return float('inf')  # Failed URLs get infinite time
