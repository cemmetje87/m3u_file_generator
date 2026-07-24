#!/usr/bin/env python3
"""Configurable include/exclude filtering for M3U entries.

Rules come from m3u_filter_config.json. Semantics (matching the README):
- Excludes win first: an entry matching ANY exclude rule is rejected.
- Includes: if `group_prefix` AND `group_content` are both set, a group must
  satisfy both. Otherwise, if any include rules are defined, an entry must
  match at least one (OR within a field's rule types, OR across fields).
  With no include rules defined, all non-excluded entries pass.
- URL-domain exclusions match on the parsed domain with endswith semantics,
  so "arslan.vip" matches that host and its subdomains, and ".top" matches
  the whole .top TLD - without substring false positives like "laptop.com".
"""
import json
import re
from pathlib import Path
from urllib.parse import urlparse

FIELDS = ("group", "channel", "tvg_name")
RULE_TYPES = ("exact_match", "contains", "startswith", "regex")


def _compile_regexes(patterns, field_name):
    """Compile user-supplied regexes, raising a clear error on bad patterns."""
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error as e:
            raise ValueError(
                f"Invalid regex in config for '{field_name}': {pattern!r} ({e})"
            ) from e
    return compiled


def _domain_of(url):
    """Extract the lowercase domain (no port) from a URL."""
    try:
        return urlparse(url).netloc.split(':')[0].lower()
    except Exception:
        return ""


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

        # Load per-field rule sets: {field: {rule_type: [values]}}
        # String rules are uppercased for case-insensitive comparison;
        # regexes are compiled once with IGNORECASE.
        self.exclude = {}
        self.include = {}
        for field in FIELDS:
            self.exclude[field] = self._load_rules(config, 'exclude', field)
            self.include[field] = self._load_rules(config, 'include', field)

        # URL-domain exclusions (matched on the parsed domain, lowercase)
        self.exclude_url_domain_contains = [
            x.lower() for x in config.get('exclude', {}).get('url_domain', {}).get('contains', [])
        ]

    def _load_rules(self, config, section, field):
        raw = config.get(section, {}).get(field, {})
        return {
            'exact_match': [x.upper() for x in raw.get('exact_match', [])],
            'contains': [x.upper() for x in raw.get('contains', [])],
            'startswith': [x.upper() for x in raw.get('startswith', [])],
            'regex': _compile_regexes(raw.get('regex', []), f"{section}.{field}.regex"),
        }

    # --- Backwards-compatible attribute views (used by summary logging) ---
    def _attr(self, section, field, rule_type):
        return getattr(self, section)[field][rule_type]

    @property
    def exclude_group_exact(self):
        return self.exclude['group']['exact_match']

    @property
    def exclude_group_contains(self):
        return self.exclude['group']['contains']

    @property
    def exclude_group_startswith(self):
        return self.exclude['group']['startswith']

    @property
    def include_group_exact(self):
        return self.include['group']['exact_match']

    @property
    def include_group_contains(self):
        return self.include['group']['contains']

    @property
    def include_group_startswith(self):
        return self.include['group']['startswith']

    # --- Matching helpers ---
    @staticmethod
    def _matches(value, rules):
        """Return a match reason if value matches any rule, else None."""
        value_upper = value.upper()

        if value_upper in rules['exact_match']:
            return 'exact-match'

        for pattern in rules['contains']:
            if pattern in value_upper:
                return f'contains-{pattern}'

        for pattern in rules['startswith']:
            if value_upper.startswith(pattern):
                return f'startswith-{pattern}'

        for pattern in rules['regex']:
            if pattern.search(value):
                return f'regex-{pattern.pattern}'

        return None

    @staticmethod
    def _has_rules(rules):
        return any(rules[rule_type] for rule_type in RULE_TYPES)

    def should_exclude_by_group(self, group_name):
        """Check if group should be excluded based on filters"""
        reason = self._matches(group_name, self.exclude['group'])
        return (True, reason) if reason else (False, None)

    def should_exclude_by_channel(self, channel_name):
        """Check if channel should be excluded based on filters"""
        reason = self._matches(channel_name, self.exclude['channel'])
        return (True, reason) if reason else (False, None)

    def should_exclude_by_tvg_name(self, tvg_name):
        """Check if tvg-name should be excluded based on filters"""
        reason = self._matches(tvg_name, self.exclude['tvg_name'])
        return (True, reason) if reason else (False, None)

    def should_include_by_group(self, group_name):
        """Check if group should be included based on filters"""
        if not self._has_rules(self.include['group']):
            return True, None
        reason = self._matches(group_name, self.include['group'])
        return (True, reason) if reason else (False, None)

    def should_include_by_channel(self, channel_name):
        """Check if channel should be included based on filters"""
        if not self._has_rules(self.include['channel']):
            return True, None
        reason = self._matches(channel_name, self.include['channel'])
        return (True, reason) if reason else (False, None)

    def should_include_by_tvg_name(self, tvg_name):
        """Check if tvg-name should be included based on filters"""
        if not self._has_rules(self.include['tvg_name']):
            return True, None
        reason = self._matches(tvg_name, self.include['tvg_name'])
        return (True, reason) if reason else (False, None)

    def is_url_domain_excluded(self, url):
        """Return the matched exclusion entry if the URL's domain is excluded.

        Matches on the parsed domain with endswith semantics:
        - "arslan.vip" matches "arslan.vip" and "www.arslan.vip"
        - ".top" matches any domain in the .top TLD
        """
        domain = _domain_of(url)
        if not domain:
            return None
        for entry in self.exclude_url_domain_contains:
            if entry.startswith('.'):
                if domain.endswith(entry):
                    return entry
            elif domain == entry or domain.endswith('.' + entry):
                return entry
        return None

    def is_entry_allowed(self, entry):
        """Check if an entry passes all filters (both include and exclude)"""
        group_name = entry.get('group', '')
        channel_name = entry.get('name', '')
        tvg_name = entry.get('tvg_name', '')
        url = entry.get('url', '')

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
        matched_domain = self.is_url_domain_excluded(url)
        if matched_domain:
            return False, f'excluded-url-domain-{matched_domain}'

        # STEP 2: Check includes
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

        # Fallback to regular include filters:
        # entry must match at least one field that has include filters defined
        has_group_include_filters = self._has_rules(self.include['group'])
        has_channel_include_filters = self._has_rules(self.include['channel'])
        has_tvg_name_include_filters = self._has_rules(self.include['tvg_name'])

        has_any_include_filters = (
            has_group_include_filters or has_channel_include_filters or has_tvg_name_include_filters
        )

        if has_any_include_filters:
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
