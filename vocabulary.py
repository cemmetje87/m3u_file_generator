#!/usr/bin/env python3
"""Vocabulary of the group / channel / tvg-name values seen across sources.

The filter UI needs to offer real values as you type, which means knowing
what actually appears in the source playlists. That vocabulary is collected
while m3u_processor parses entries - *before* filtering, so values that the
current rules exclude are still discoverable (you cannot write a rule for a
group you can't see).

Storage is one plain TSV per field, `<count>\\t<value>` sorted by count
descending, so a field can be loaded on its own and searched without parsing
a multi-megabyte JSON document.
"""
import json
import re
import unicodedata
from array import array
from bisect import bisect_right
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

FIELDS = ("group", "channel", "tvg_name")

# Which entry key feeds which vocabulary field. The parser calls the channel
# display name "name"; the filter config calls the same field "channel".
ENTRY_KEYS = {"group": "group", "channel": "name", "tvg_name": "tvg_name"}

DEFAULT_DIR = Path("vocabulary")

# tvg-name and channel name are near-unique across a large corpus (episode
# titles, dated events), so the tail is unbounded. Keep the most frequent
# values - the ones worth writing a rule against - and report the truncation
# rather than silently serving a partial vocabulary.
MAX_VALUES_PER_FIELD = 200_000

_TAB_OR_NEWLINE = re.compile(r"[\t\r\n]+")


def _clean(value):
    """Collapse whitespace that would break the one-value-per-line format."""
    return _TAB_OR_NEWLINE.sub(" ", value).strip()


class VocabularyCollector:
    """Counts distinct values per field while entries stream past."""

    def __init__(self):
        self.counters = {field: Counter() for field in FIELDS}
        self.entries_seen = 0

    def add_entry(self, entry):
        self.entries_seen += 1
        for field, key in ENTRY_KEYS.items():
            value = _clean(entry.get(key, "") or "")
            if value:
                self.counters[field][value] += 1

    def add_entries(self, entries):
        for entry in entries:
            self.add_entry(entry)

    def ranked(self, field):
        """Values sorted by count descending, then alphabetically."""
        return sorted(self.counters[field].items(), key=lambda kv: (-kv[1], kv[0]))

    def save(self, directory=DEFAULT_DIR, source="build"):
        """Write one TSV per field plus a meta.json describing the capture."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        fields_meta = {}
        for field in FIELDS:
            ranked = self.ranked(field)
            kept = ranked[:MAX_VALUES_PER_FIELD]
            lines = "".join(f"{count}\t{value}\n" for value, count in kept)
            (directory / f"{field}.tsv").write_text(lines, encoding="utf-8")
            fields_meta[field] = {
                "total": len(ranked),
                "kept": len(kept),
                "truncated": len(kept) < len(ranked),
            }

        meta = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "entries_seen": self.entries_seen,
            "source": source,
            "fields": fields_meta,
        }
        (directory / "meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return meta


_GROUP_RE = re.compile(r'group-title="([^"]*)"')
_TVG_NAME_RE = re.compile(r'tvg-name="([^"]*)"')


def collect_from_playlist(path):
    """Build a vocabulary by scanning a finished M3U playlist.

    A fallback for when no build has run yet: it costs one pass over the
    master playlist and yields a *post-filter* vocabulary, so it can only
    show values the current rules already let through.
    """
    collector = VocabularyCollector()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("#EXTINF"):
                continue
            group = _GROUP_RE.search(line)
            tvg_name = _TVG_NAME_RE.search(line)
            collector.add_entry({
                "group": group.group(1) if group else "",
                "tvg_name": tvg_name.group(1) if tvg_name else "",
                "name": line.rsplit(",", 1)[-1],
            })
    return collector


def load_meta(directory=DEFAULT_DIR):
    """Return the capture metadata, or None if no vocabulary exists yet."""
    path = Path(directory) / "meta.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fold(text):
    """Casefold and strip accents so "Series" finds "SÉRIES"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


class FieldIndex:
    """Substring search over one field's values.

    The values are joined into a single folded blob and scanned with
    ``str.find``, which runs at C speed and stops as soon as enough matches
    are found. That keeps a 1.1M-value field searchable per keystroke
    without a real search engine.

    Accent folding can change a string's length, so the folded blob is built
    per value and offsets are tracked separately from the display values.
    """

    def __init__(self, values, counts):
        self.values = values
        self.counts = counts
        folded = []
        starts = array("q")
        offset = 0
        for value in values:
            piece = _fold(value)
            folded.append(piece)
            starts.append(offset)
            offset += len(piece) + 1
        self._blob = "\n".join(folded)
        self._starts = starts

    def __len__(self):
        return len(self.values)

    @classmethod
    def load(cls, path):
        values, counts = [], array("i")
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                count, _, value = line.rstrip("\n").partition("\t")
                if not value:
                    continue
                values.append(value)
                counts.append(int(count))
        return cls(values, counts)

    def _index_at(self, offset):
        return bisect_right(self._starts, offset) - 1

    def _scan(self, needle, shift, limit, out, seen):
        """Walk every occurrence of `needle`, appending the values it lands in."""
        pos = 0
        while len(out) < limit:
            found = self._blob.find(needle, pos)
            if found < 0:
                return
            index = self._index_at(found + shift)
            if index not in seen:
                seen.add(index)
                out.append(index)
            # Resume past this value's line so one value yields one hit.
            pos = self._blob.find("\n", found + shift)
            if pos < 0:
                return
            pos += 1

    def search(self, query, limit=25):
        """Matching values as (value, count), best match first.

        Values that *start with* the query rank above values that merely
        contain it; within each group the more frequent value wins, which
        falls out of the values being stored in count order.
        """
        query = _fold(query.strip())
        if not query:
            return [(self.values[i], self.counts[i]) for i in range(min(limit, len(self.values)))]

        seen, out = set(), []
        # Prefix matches: every value except the first is preceded by "\n".
        if self._blob.startswith(query):
            seen.add(0)
            out.append(0)
        self._scan("\n" + query, 1, limit, out, seen)
        # Then anything containing the query anywhere.
        self._scan(query, 0, limit, out, seen)
        return [(self.values[i], self.counts[i]) for i in out]
