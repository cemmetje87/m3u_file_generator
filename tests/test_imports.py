import importlib

import pytest


MODULES = [
    "netguard",
    "db",
    "filters",
    "downloader",
    "parser",
    "m3u_processor",
    "iptv_scraper",
    "main",
]


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    """Every top-level module should import without side effects (beyond
    their normal definition-time work)."""
    importlib.import_module(module_name)
