import json
from pathlib import Path

import pytest

from filters import M3UFilterConfig, _compile_regexes, _domain_of


@pytest.fixture
def config_file(tmp_path):
    payload = {
        "settings": {
            "top_fastest_count": 100,
            "domain_error_threshold": 5,
        },
        "exclude": {
            "group": {
                "exact_match": ["XXX", "ADULT"],
                "contains": ["XXX", "ADULT", "PORN", "18+"],
                "startswith": ["XXX", "ADULT"],
                "regex": [],
            },
            "channel": {
                "exact_match": [],
                "contains": ["XXX", "ADULT"],
                "startswith": [],
                "regex": [],
            },
            "tvg_name": {
                "exact_match": [],
                "contains": [],
                "startswith": [],
                "regex": [],
            },
            "url_domain": {
                "contains": [
                    "arslan.vip",
                    ".top",
                    "iptv.darktv.eu",
                ],
            },
        },
        "include": {
            "group_prefix": ["NL", "TR"],
            "group_content": ["MOVIES", "SERIES"],
            "group": {
                "exact_match": [],
                "contains": ["Netherlands", "Turkish"],
                "startswith": ["NL", "TR"],
                "regex": [],
            },
            "channel": {
                "exact_match": [],
                "contains": [],
                "startswith": [],
                "regex": [],
            },
            "tvg_name": {
                "exact_match": [],
                "contains": [],
                "startswith": [],
                "regex": [],
            },
        },
    }
    path = tmp_path / "m3u_filter_config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def config(config_file):
    return M3UFilterConfig(str(config_file))


class TestCompileRegexes:
    def test_valid_patterns(self):
        compiled = _compile_regexes(["^TR.*", "MOVIES"], "test.field")
        assert len(compiled) == 2
        assert compiled[0].search("TR | VIP")
        assert compiled[1].search("English Movies")

    def test_invalid_pattern_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            _compile_regexes(["[unclosed"], "test.field")

    def test_empty_returns_empty_list(self):
        assert _compile_regexes([], "test.field") == []


class TestDomainOf:
    def test_strips_port_and_lowercases(self):
        assert _domain_of("http://Example.com:8080/path") == "example.com"

    def test_returns_empty_for_invalid(self):
        assert _domain_of("not a url") == ""

    def test_empty_string(self):
        assert _domain_of("") == ""


class TestExclusion:
    def test_exact_match_excludes(self, config):
        excluded, reason = config.should_exclude_by_group("XXX")
        assert excluded is True
        assert "exact-match" in reason

    def test_contains_excludes(self, config):
        excluded, reason = config.should_exclude_by_group("US | ADULT 18+")
        assert excluded is True
        assert "contains-ADULT" in reason

    def test_case_insensitive(self, config):
        excluded, _ = config.should_exclude_by_group("xxx lower")
        assert excluded is True

    def test_no_match_returns_false(self, config):
        excluded, reason = config.should_exclude_by_group("Netherlands | Movies")
        assert excluded is False
        assert reason is None

    def test_startswith_excludes(self, config):
        excluded, _ = config.should_exclude_by_group("ADULT channel")
        assert excluded is True

    def test_channel_exclude_contains(self, config):
        excluded, _ = config.should_exclude_by_channel("Playboy TV ADULT")
        assert excluded is True


class TestUrlDomainExclusion:
    def test_exact_domain_match(self, config):
        matched = config.is_url_domain_excluded("http://arslan.vip/stream.m3u8")
        assert matched == "arslan.vip"

    def test_subdomain_match(self, config):
        matched = config.is_url_domain_excluded("http://cdn.arslan.vip/stream.m3u8")
        assert matched == "arslan.vip"

    def test_tld_dot_match(self, config):
        matched = config.is_url_domain_excluded("http://something.top/stream.m3u8")
        assert matched == ".top"

    def test_unrelated_domain_allowed(self, config):
        matched = config.is_url_domain_excluded("http://laptop.com/stream.m3u8")
        assert matched is None

    def test_legit_substring_not_matched_as_domain(self, config):
        matched = config.is_url_domain_excluded("http://laptop.arslan.vip/stream.m3u8")
        assert matched == "arslan.vip"

    def test_port_stripped(self, config):
        matched = config.is_url_domain_excluded("http://arslan.vip:8080/stream.m3u8")
        assert matched == "arslan.vip"

    def test_case_insensitive(self, config):
        matched = config.is_url_domain_excluded("http://ARSLAN.VIP/stream.m3u8")
        assert matched == "arslan.vip"


class TestInclusion:
    def test_include_group_prefix_content_and(self, config):
        allowed, _ = config.is_entry_allowed({
            "group": "NL | MOVIES",
            "name": "Some Channel",
            "tvg_name": "",
            "url": "http://example.com/s",
        })
        assert allowed is True

    def test_include_prefix_only_fails(self, config):
        allowed, reason = config.is_entry_allowed({
            "group": "NL | News",
            "name": "RTL",
            "tvg_name": "",
            "url": "http://example.com/s",
        })
        assert allowed is False
        assert "group-prefix-content-not-matched" in reason

    def test_include_content_only_fails(self, config):
        allowed, reason = config.is_entry_allowed({
            "group": "FR | MOVIES",
            "name": "Some FR Movie",
            "tvg_name": "",
            "url": "http://example.com/s",
        })
        assert allowed is False
        assert "group-prefix-content-not-matched" in reason

    def test_exclude_wins_over_include(self, config):
        allowed, reason = config.is_entry_allowed({
            "group": "NL | MOVIES ADULT",
            "name": "Some Channel",
            "tvg_name": "",
            "url": "http://example.com/s",
        })
        assert allowed is False
        assert "excluded-group" in reason

    def test_url_domain_excluded_overrides_include(self, config):
        allowed, reason = config.is_entry_allowed({
            "group": "NL | MOVIES",
            "name": "Some Channel",
            "tvg_name": "",
            "url": "http://arslan.vip/stream",
        })
        assert allowed is False
        assert "excluded-url-domain" in reason

    def test_no_include_rules_allows_non_excluded(self, tmp_path):
        cfg = {
            "settings": {"top_fastest_count": 0, "domain_error_threshold": 0},
            "exclude": {
                "group": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
                "channel": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
                "tvg_name": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
                "url_domain": {"contains": []},
            },
            "include": {
                "group_prefix": [],
                "group_content": [],
                "group": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
                "channel": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
                "tvg_name": {"exact_match": [], "contains": [], "startswith": [], "regex": []},
            },
        }
        path = tmp_path / "loose.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        c = M3UFilterConfig(str(path))
        allowed, _ = c.is_entry_allowed({
            "group": "Anything",
            "name": "Any Channel",
            "tvg_name": "",
            "url": "http://example.com/s",
        })
        assert allowed is True


class TestBackwardsCompatProperties:
    def test_exclude_group_properties(self, config):
        assert config.exclude_group_exact == ["XXX", "ADULT"]
        assert config.exclude_group_contains == ["XXX", "ADULT", "PORN", "18+"]
        assert config.exclude_group_startswith == ["XXX", "ADULT"]

    def test_include_group_properties(self, config):
        assert config.include_group_exact == []
        assert "NETHERLANDS" in config.include_group_contains
        assert "NL" in config.include_group_startswith


class TestMissingFile:
    def test_missing_config_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            M3UFilterConfig(str(tmp_path / "does_not_exist.json"))
