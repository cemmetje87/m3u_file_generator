import socket

import pytest

from netguard import is_public_http_url


class TestIsPublicHttpUrl:
    def test_http_scheme_allowed_when_dns_resolves_publicly(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("93.184.216.34", 0))],
        )
        ok, reason = is_public_http_url("http://example.com/some.m3u")
        assert ok is True
        assert reason == "ok"

    def test_https_scheme_allowed_when_dns_resolves_publicly(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("1.1.1.1", 0))],
        )
        ok, _ = is_public_http_url("https://example.com/some.m3u")
        assert ok is True

    def test_ftp_scheme_rejected(self):
        ok, reason = is_public_http_url("ftp://example.com/file")
        assert ok is False
        assert "scheme" in reason

    def test_file_scheme_rejected(self):
        ok, reason = is_public_http_url("file:///etc/passwd")
        assert ok is False
        assert "scheme" in reason

    def test_no_scheme_rejected(self):
        ok, reason = is_public_http_url("example.com/some.m3u")
        assert ok is False
        assert "scheme" in reason

    def test_missing_host_rejected(self):
        ok, reason = is_public_http_url("http:///path-only")
        assert ok is False
        assert "no host" in reason

    def test_unparseable_url_rejected(self):
        ok, _ = is_public_http_url("not://a::url:::")
        assert ok is False

    def test_localhost_hostname_rejected(self):
        ok, reason = is_public_http_url("http://localhost/admin")
        assert ok is False
        assert "blocked" in reason or "loopback" in reason

    def test_localhost_localdomain_rejected(self):
        ok, reason = is_public_http_url("http://localhost.localdomain/admin")
        assert ok is False

    def test_loopback_ipv4_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("127.0.0.1", 0))],
        )
        ok, reason = is_public_http_url("http://anything.test/x")
        assert ok is False
        assert "loopback" in reason or "non-public" in reason

    def test_private_10_block_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("10.0.0.5", 0))],
        )
        ok, _ = is_public_http_url("http://intranet.local/x")
        assert ok is False

    def test_private_192_168_block_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("192.168.1.10", 0))],
        )
        ok, _ = is_public_http_url("http://router.local/x")
        assert ok is False

    def test_private_172_16_block_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("172.16.0.1", 0))],
        )
        ok, _ = is_public_http_url("http://internal/x")
        assert ok is False

    def test_link_local_metadata_block_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("169.254.169.254", 0))],
        )
        ok, reason = is_public_http_url("http://example.com/latest/meta-data")
        assert ok is False
        assert "non-public" in reason

    def test_ipv6_loopback_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET6, None, None, None, ("::1", 0, 0, 0))],
        )
        ok, _ = is_public_http_url("http://example.com/x")
        assert ok is False

    def test_unspecified_address_rejected(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [(socket.AF_INET, None, None, None, ("0.0.0.0", 0))],
        )
        ok, _ = is_public_http_url("http://example.com/x")
        assert ok is False

    def test_dns_failure_rejected(self, monkeypatch):
        def fake_gaierror(host, port):
            raise socket.gaierror("no such host")

        monkeypatch.setattr("socket.getaddrinfo", fake_gaierror)
        ok, reason = is_public_http_url("http://nonexistent.invalid/x")
        assert ok is False
        assert "DNS" in reason

    def test_one_of_many_addresses_blocked_rejects(self, monkeypatch):
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port: [
                (socket.AF_INET, None, None, None, ("8.8.8.8", 0)),
                (socket.AF_INET, None, None, None, ("127.0.0.1", 0)),
            ],
        )
        ok, _ = is_public_http_url("http://example.com/x")
        assert ok is False
