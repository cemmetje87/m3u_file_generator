from parser import parse_m3u, create_master_m3u


SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 tvg-id="nl1.nl" tvg-name="NL1" tvg-logo="https://example.com/nl1.png" group-title="NL | LIVE",NL1 HD
http://example.com/nl1.m3u8
#EXTINF:-1 tvg-id="tr1.tr" tvg-name="TR1" tvg-logo="https://example.com/tr1.png" group-title="TR | LIVE",TR1 HD
http://example.com/tr1.m3u8
#EXTINF:-1 tvg-id="xx1.xx" tvg-name="XX1" tvg-logo="https://example.com/xx1.png" group-title="XXX",ADULT1
http://example.com/xx1.m3u8
"""


def test_parse_extracts_all_entries(tmp_path):
    path = tmp_path / "in.m3u"
    path.write_text(SAMPLE_M3U, encoding="utf-8")
    entries = parse_m3u(str(path))
    assert len(entries) == 3


def test_parse_extracts_metadata_fields(tmp_path):
    path = tmp_path / "in.m3u"
    path.write_text(SAMPLE_M3U, encoding="utf-8")
    [entry] = [e for e in parse_m3u(str(path)) if "NL1" in e["name"]]
    assert entry["name"] == "NL1 HD"
    assert entry["group"] == "NL | LIVE"
    assert entry["tvg_name"] == "NL1"
    assert entry["url"] == "http://example.com/nl1.m3u8"


def test_parse_preserves_full_extinf_info(tmp_path):
    """G2 regression: the info line MUST include everything after #EXTINF:
    including the channel display name (the part after the last comma).
    Players read the name from after the comma, so stripping it would
    leave every channel unnamed.
    """
    path = tmp_path / "in.m3u"
    path.write_text(SAMPLE_M3U, encoding="utf-8")
    [entry] = [e for e in parse_m3u(str(path)) if "NL1" in e["name"]]
    assert entry["info"].startswith("-1 tvg-id=")
    assert "NL1 HD" in entry["info"]


def test_parse_handles_empty_file(tmp_path):
    path = tmp_path / "empty.m3u"
    path.write_text("", encoding="utf-8")
    assert parse_m3u(str(path)) == []


def test_parse_handles_missing_file(tmp_path, capsys):
    result = parse_m3u(str(tmp_path / "no_such.m3u"))
    assert result == []
    captured = capsys.readouterr()
    assert "Error parsing" in captured.out


def test_create_master_m3u_writes_header_and_entries(tmp_path):
    out = tmp_path / "master.m3u"
    entries = [
        {
            "info": '-1 tvg-id="a" tvg-name="A" group-title="NL | MOVIES",Movie A',
            "url": "http://a.example/1.m3u8",
            "name": "Movie A",
            "group": "NL | MOVIES",
            "tvg_name": "A",
        },
        {
            "info": '-1 tvg-id="b" tvg-name="B" group-title="NL | MOVIES",Movie B',
            "url": "http://b.example/2.m3u8",
            "name": "Movie B",
            "group": "NL | MOVIES",
            "tvg_name": "B",
        },
    ]
    n = create_master_m3u(entries, str(out))
    assert n == 2
    content = out.read_text(encoding="utf-8")
    assert content.startswith("#EXTM3U")
    assert "Movie A" in content
    assert "Movie B" in content
    assert "http://a.example/1.m3u8" in content


def test_create_master_m3u_dedupes_by_name_case_insensitive(tmp_path):
    out = tmp_path / "master.m3u"
    entries = [
        {
            "info": '-1 tvg-name="X" group-title="NL | MOVIES",Movie One',
            "url": "http://a.example/1.m3u8",
            "name": "Movie One",
            "group": "NL | MOVIES",
            "tvg_name": "X",
        },
        {
            "info": '-1 tvg-name="Y" group-title="NL | MOVIES",movie one',
            "url": "http://b.example/2.m3u8",
            "name": "movie one",
            "group": "NL | MOVIES",
            "tvg_name": "Y",
        },
    ]
    n = create_master_m3u(entries, str(out))
    assert n == 1
    content = out.read_text(encoding="utf-8")
    assert "http://a.example/1.m3u8" in content
    assert "http://b.example/2.m3u8" not in content


def test_create_master_m3u_preserves_display_name_in_extinf(tmp_path):
    """G2 regression for write side: the part after the comma in the info
    line must be preserved, not stripped.
    """
    out = tmp_path / "master.m3u"
    entries = [
        {
            "info": '-1 tvg-name="X" group-title="NL | MOVIES",My Display Name',
            "url": "http://a.example/1.m3u8",
            "name": "My Display Name",
            "group": "NL | MOVIES",
            "tvg_name": "X",
        },
    ]
    create_master_m3u(entries, str(out))
    content = out.read_text(encoding="utf-8")
    assert "My Display Name" in content
