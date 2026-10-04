import json

import pytest

import vocabulary
from vocabulary import FieldIndex, VocabularyCollector, collect_from_playlist, load_meta


def entry(group="", name="", tvg_name=""):
    return {"group": group, "name": name, "tvg_name": tvg_name}


SAMPLE_M3U = """#EXTM3U
#EXTINF:-1 tvg-name="NL1" group-title="NL | LIVE",NL1 HD
http://example.com/nl1.m3u8
#EXTINF:-1 tvg-name="NL2" group-title="NL | LIVE",NL2 HD
http://example.com/nl2.m3u8
#EXTINF:-1 tvg-name="XX1" group-title="XXX",ADULT1
http://example.com/xx1.m3u8
"""


class TestCollector:
    def test_counts_each_field_separately(self):
        c = VocabularyCollector()
        c.add_entries([entry("SPORT", "ESPN", "espn.us"), entry("SPORT", "BEIN", "bein.qa")])
        assert c.entries_seen == 2
        assert c.counters["group"]["SPORT"] == 2
        assert c.counters["channel"]["ESPN"] == 1
        assert c.counters["tvg_name"]["bein.qa"] == 1

    def test_ignores_empty_values(self):
        c = VocabularyCollector()
        c.add_entries([entry("SPORT", "", ""), entry("", "  ", None)])
        assert c.entries_seen == 2
        assert c.counters["channel"] == {}
        assert c.counters["tvg_name"] == {}

    def test_strips_tabs_that_would_break_the_line_format(self):
        c = VocabularyCollector()
        c.add_entry(entry(group="A\tB\nC"))
        assert list(c.counters["group"]) == ["A B C"]

    def test_ranked_by_count_then_alphabetically(self):
        c = VocabularyCollector()
        c.add_entries([entry("B"), entry("B"), entry("Z"), entry("A")])
        assert c.ranked("group") == [("B", 2), ("A", 1), ("Z", 1)]


class TestSaveAndLoad:
    def test_round_trips_values_and_counts(self, tmp_path):
        c = VocabularyCollector()
        c.add_entries([entry("SPORT"), entry("SPORT"), entry("NEWS")])
        c.save(tmp_path)

        index = FieldIndex.load(tmp_path / "group.tsv")
        assert index.search("", 10) == [("SPORT", 2), ("NEWS", 1)]

    def test_meta_reports_the_capture(self, tmp_path):
        c = VocabularyCollector()
        c.add_entries([entry("SPORT", "ESPN")])
        meta = c.save(tmp_path, source="build")

        assert meta == load_meta(tmp_path)
        assert meta["entries_seen"] == 1
        assert meta["source"] == "build"
        assert meta["fields"]["group"] == {"total": 1, "kept": 1, "truncated": False}

    def test_truncation_is_reported_not_hidden(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vocabulary, "MAX_VALUES_PER_FIELD", 2)
        c = VocabularyCollector()
        c.add_entries([entry(g) for g in ("A", "B", "C", "D")])
        meta = c.save(tmp_path)

        assert meta["fields"]["group"] == {"total": 4, "kept": 2, "truncated": True}
        assert len(FieldIndex.load(tmp_path / "group.tsv")) == 2

    def test_load_meta_returns_none_without_a_capture(self, tmp_path):
        assert load_meta(tmp_path) is None

    def test_values_containing_tabs_never_reach_the_file(self, tmp_path):
        c = VocabularyCollector()
        c.add_entry(entry(group="NL\tLIVE"))
        c.save(tmp_path)
        assert (tmp_path / "group.tsv").read_text(encoding="utf-8") == "1\tNL LIVE\n"


class TestFieldIndex:
    @staticmethod
    def build(pairs):
        return FieldIndex([v for v, _ in pairs], [c for _, c in pairs])

    def test_prefix_matches_rank_above_substring_matches(self):
        index = self.build([("MY SPORT", 90), ("SPORT ONE", 5)])
        assert [v for v, _ in index.search("sport")] == ["SPORT ONE", "MY SPORT"]

    def test_first_value_can_match_as_a_prefix(self):
        index = self.build([("SPORT ONE", 5), ("NEWS", 90)])
        assert index.search("sport") == [("SPORT ONE", 5)]

    def test_ties_fall_back_to_stored_frequency_order(self):
        index = self.build([("SPORT A", 90), ("SPORT B", 5)])
        assert [v for v, _ in index.search("sport")] == ["SPORT A", "SPORT B"]

    def test_folds_accents_and_case(self):
        index = self.build([("SÉRIES | Drama", 3)])
        assert index.search("series") == [("SÉRIES | Drama", 3)]
        assert index.search("SÉRIES") == [("SÉRIES | Drama", 3)]

    def test_each_value_is_returned_once(self):
        index = self.build([("SPORT SPORT SPORT", 1)])
        assert index.search("sport") == [("SPORT SPORT SPORT", 1)]

    def test_respects_the_limit(self):
        index = self.build([(f"SPORT {i}", 1) for i in range(20)])
        assert len(index.search("sport", 5)) == 5

    def test_empty_query_returns_the_most_common_values(self):
        index = self.build([("A", 9), ("B", 4), ("C", 1)])
        assert index.search("  ", 2) == [("A", 9), ("B", 4)]

    def test_no_match_returns_nothing(self):
        index = self.build([("SPORT", 1)])
        assert index.search("cooking") == []

    def test_search_does_not_cross_value_boundaries(self):
        # "TA" only exists by joining "...A" to "T..." across the separator.
        index = self.build([("BETA", 1), ("TANGO", 1)])
        assert index.search("at") == []


class TestCollectFromPlaylist:
    def test_reads_group_tvg_name_and_display_name(self, tmp_path):
        path = tmp_path / "master.m3u"
        path.write_text(SAMPLE_M3U, encoding="utf-8")
        c = collect_from_playlist(path)

        assert c.entries_seen == 3
        assert c.counters["group"]["NL | LIVE"] == 2
        assert c.counters["tvg_name"]["NL1"] == 1
        assert c.counters["channel"]["NL2 HD"] == 1

    def test_sees_values_the_filters_would_reject(self, tmp_path):
        """The lookup must offer values you might want to exclude."""
        path = tmp_path / "master.m3u"
        path.write_text(SAMPLE_M3U, encoding="utf-8")
        assert "XXX" in collect_from_playlist(path).counters["group"]


class TestProcessorIntegration:
    """The pipeline must capture values *before* filtering drops them."""

    def _run(self, tmp_path, monkeypatch, config):
        import m3u_processor

        monkeypatch.chdir(tmp_path)
        (tmp_path / "m3u_filter_config.json").write_text(json.dumps(config), encoding="utf-8")
        (tmp_path / "urls.txt").write_text("https://example.com/list.m3u\n", encoding="utf-8")

        def fake_download(url, **kwargs):
            cached = tmp_path / "downloaded.m3u"
            cached.write_text(SAMPLE_M3U, encoding="utf-8")
            return str(cached)

        monkeypatch.setattr(m3u_processor, "download_m3u", fake_download)
        monkeypatch.setattr("sys.argv", ["m3u_processor.py", "--input", "urls.txt"])
        m3u_processor.main()

    def test_excluded_values_stay_discoverable(self, tmp_path, monkeypatch, capsys):
        self._run(tmp_path, monkeypatch, {
            "settings": {"top_fastest_count": 0, "domain_error_threshold": 0},
            "exclude": {"group": {"contains": ["XXX"]}},
            "include": {},
        })
        capsys.readouterr()

        # The XXX group was filtered out of the playlist...
        assert "XXX" not in (tmp_path / "master_iptv.m3u").read_text(encoding="utf-8")
        # ...but you still need to see it to know the rule is doing its job.
        index = FieldIndex.load(tmp_path / "vocabulary" / "group.tsv")
        assert index.search("xxx") == [("XXX", 1)]
        assert load_meta(tmp_path / "vocabulary")["entries_seen"] == 3

    def test_a_run_with_no_entries_keeps_the_previous_index(self, tmp_path, monkeypatch, capsys):
        import m3u_processor

        monkeypatch.chdir(tmp_path)
        collector = VocabularyCollector()
        collector.add_entry(entry("KEEP ME"))
        collector.save(tmp_path / "vocabulary")

        (tmp_path / "m3u_filter_config.json").write_text(json.dumps({
            "settings": {"top_fastest_count": 0, "domain_error_threshold": 0},
        }), encoding="utf-8")
        (tmp_path / "urls.txt").write_text("https://example.com/list.m3u\n", encoding="utf-8")
        monkeypatch.setattr(m3u_processor, "download_m3u", lambda url, **kw: None)
        monkeypatch.setattr("sys.argv", ["m3u_processor.py", "--input", "urls.txt"])
        m3u_processor.main()
        capsys.readouterr()

        index = FieldIndex.load(tmp_path / "vocabulary" / "group.tsv")
        assert index.search("keep") == [("KEEP ME", 1)]
