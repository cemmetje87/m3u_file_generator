# IPTV Scraper and Processor

Scrapes M3U playlist URLs from configured web sources, downloads them,
applies user-defined include/exclude filters, validates the streams for
aliveness and latency, and writes a deduplicated `master_iptv.m3u`. A small
FastAPI web UI lets you trigger jobs and watch logs in real time.

<img width="1199" height="795" alt="image" src="https://github.com/user-attachments/assets/0475b3dc-ec23-4316-ba60-64165f26837f" />

## Features

- **Web interface** (`static/index.html`) laid out as a three-stage signal
  chain — acquire, filter, master — with live stage lamps, on-panel
  readouts (feeds found, active rules, channel count, output size) and a
  WebSocket-pushed log console.
- **Filter rule builder** in the web UI: add and remove rules from
  dropdowns instead of hand-editing JSON. A JSON tab is still there for
  bulk edits, and the two views stay in sync.
- **Value lookup** while you type a rule: group titles, channel names and
  tvg-names seen in the sources are captured during a build and offered as
  suggestions, ranked by how many channels carry them.
- **M3U URL scraping** discovers and extracts M3U playlist URLs from
  configured pages.
- **URL validation & latency testing** uses a `ThreadPoolExecutor` to probe
  every URL concurrently; the N fastest go on to processing.
- **M3U filtering** is configured in `m3u_filter_config.json` and supports
  exact-match, contains, startswith, and regex rules on `group-title`,
  channel name, `tvg-name`, plus URL-domain exclusions with proper
  endswith semantics (no false positives like "laptop.com" matching
  "laptop.arslan.vip" incorrectly).
- **Domain error tracking** persists per-domain failure counts in
  `domain_errors.db` so unresponsive sources are skipped on subsequent
  runs.
- **Deduplication** keeps the first occurrence of each channel name
  (case-insensitive) in the master playlist.
- **SSRF guard** (`netguard.is_public_http_url`) blocks non-HTTP schemes
  and any host resolving to a private, loopback, link-local, multicast,
  reserved, or unspecified address before the server fetches anything.
- **API key auth** for the `/api/*` endpoints and the WebSocket, with the
  key stored per-tab in `sessionStorage` in the frontend.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`

## Installation

```bash
git clone https://github.com/cemmetje87/m3u_file_generator.git
cd m3u_file_generator
uv sync
```

If you prefer plain `pip`:

```bash
pip install "fastapi>=0.124" "uvicorn>=0.38" "requests>=2.32" "websockets>=15" "beautifulsoup4>=4.14" pytest
```

## Configuration

### Environment variables (all optional)

| Variable          | Default       | Notes                                                                  |
| ----------------- | ------------- | ---------------------------------------------------------------------- |
| `IPTV_API_KEY`    | (unset)       | If set, all `/api/*` endpoints and the WebSocket require this key.     |
| `IPTV_HOST`       | `127.0.0.1`   | Bind address. **Must be a loopback address** unless `IPTV_API_KEY` is set; the server refuses to start otherwise. |
| `IPTV_PORT`       | `8000`        | TCP port.                                                              |

### `m3u_filter_config.json`

Drives all filtering. See the file for the full schema; the highlights:

```json
{
  "settings": {
    "top_fastest_count": 100,
    "domain_error_threshold": 5
  },
  "exclude": {
    "group":    { "exact_match": [], "contains": [], "startswith": [], "regex": [] },
    "channel":  { "exact_match": [], "contains": [], "startswith": [], "regex": [] },
    "tvg_name": { "exact_match": [], "contains": [], "startswith": [], "regex": [] },
    "url_domain": { "contains": ["arslan.vip", ".top"] }
  },
  "include": {
    "group_prefix": ["NL", "TR"],
    "group_content": ["MOVIES", "SERIES"],
    "group":    { "exact_match": [], "contains": [], "startswith": [], "regex": [] },
    "channel":  { "exact_match": [], "contains": [], "startswith": [], "regex": [] },
    "tvg_name": { "exact_match": [], "contains": [], "startswith": [], "regex": [] }
  }
}
```

Semantics:

- **Excludes win first** — an entry matching *any* exclude rule is
  rejected before includes are even considered.
- **URL-domain exclusions** match on the parsed domain with endswith
  semantics: `arslan.vip` matches `arslan.vip` and `cdn.arslan.vip`;
  `.top` matches any host in the `.top` TLD.
- **Includes**: if both `group_prefix` and `group_content` are set, a
  group must satisfy both (AND). Otherwise, if any include rules are
  defined, the entry must match at least one (OR across fields). With no
  include rules, all non-excluded entries pass.

## Usage

### Web UI (recommended)

```bash
uv run python main.py
# open http://localhost:8000
```

If the server is started with `IPTV_API_KEY` set, the UI will prompt for
the key the first time you load a page; the key is kept in
`sessionStorage` for the lifetime of the tab.

**Editing filters.** *Edit rules* on the Filter stage opens the rule
builder. Each row reads as one sentence — exclude / include, which field,
which test, and the value — and maps to exactly one entry in
`m3u_filter_config.json`. URL-domain rules are exclude-only and match on
host-or-subdomain, so the builder fixes those two columns for you. The
paired group match (`group_prefix` + `group_content`) has its own block,
and warns when only one of the two lists is filled, since a half-filled
pair is ignored by the filter engine. The JSON tab edits the same
document; switching tabs carries changes across.

**Value lookup.** Typing in a rule's value box (or in either paired-group
list) suggests values that actually occur in the sources, with the number
of channels carrying each one. Matching ignores case and accents, so
`series` finds `SÉRIES | Drama`, and values starting with what you typed
rank above values that merely contain it. Regex rules get no suggestions,
since a literal group name like `SÉRIES | Drama` is not the pattern that
matches it.

The vocabulary is written to `vocabulary/` (gitignored) at the end of a
processing run, collected *before* filtering — you cannot write a rule
against a group you cannot see, so excluded values stay discoverable. If
no run has happened yet, *Scan playlist* builds the same index from an
existing `master_iptv.m3u` in a few seconds; that index is post-filter, so
it only offers values your current rules already keep, and the builder
says so. Group titles are indexed in full; channel names and tvg-names run
to a million distinct values on a large corpus, so the 200,000 most common
of each are kept and the builder reports the cap.

### CLI

Scrape M3U URLs from a configured page:

```bash
uv run python iptv_scraper.py --url "https://www.example.com/your-iptv-source"
```

Process the scraped URLs against the filter config:

```bash
uv run python m3u_processor.py --input iptv_m3u_urls.txt --config m3u_filter_config.json
```

## Development

```bash
uv sync                       # install runtime + dev dependencies
uv run pytest                 # run the test suite (71 tests)
```

Add new modules? Drop them in the project root, mirror the import
patterns in `db.py` / `filters.py` / `parser.py`, and add a test file
under `tests/` with matching `test_<module>.py` name.

## Project Structure

```
.
├── main.py                 # FastAPI application and web server
├── iptv_scraper.py         # Script to scrape M3U URLs from web sources
├── m3u_processor.py        # Orchestrates: download → filter → speed-test → write master
├── archive_playlist.py     # Utility for archiving master_iptv.m3u
├── netguard.py             # SSRF guard: is_public_http_url()
├── downloader.py           # M3U fetcher with SSRF guard + latency probing
├── filters.py              # M3UFilterConfig: include/exclude rule engine
├── parser.py               # parse_m3u / create_master_m3u
├── vocabulary.py           # Captures + searches the values the rule builder suggests
├── db.py                   # DomainErrorTracker (SQLite)
├── remove_ip_urls.sh       # Strip raw-IP URLs from a file (project-scoped)
├── m3u_filter_config.json  # Filtering rules
├── domain_errors.db        # (gitignored) runtime error history
├── master_iptv.m3u         # (gitignored) generated playlist
├── urls.txt                # (gitignored) example input
├── tests/                  # pytest suite
│   ├── conftest.py
│   ├── test_netguard.py
│   ├── test_filters.py
│   ├── test_parser.py
│   ├── test_vocabulary.py
│   ├── test_db.py
│   └── test_imports.py
├── static/                 # Web UI assets
│   ├── index.html
│   ├── script.js
│   └── style.css
├── vocabulary/             # (gitignored) value index for the rule builder's lookups
├── cache/                  # (gitignored) temporary downloaded M3U files
└── archive/                # (gitignored) archived master_iptv.m3u files
```

## Security notes

- The server binds to `127.0.0.1` by default. It will refuse to bind to
  a non-loopback address unless `IPTV_API_KEY` is set.
- All `/api/*` endpoints and the WebSocket require the key when set.
  The frontend stores the key in `sessionStorage` (per-tab) and sends it
  as `X-API-Key` (or `?api_key=` for the WebSocket).
- Server-side log broadcasts redact `user:pass@` URL credentials before
  sending messages to clients.
- Configuration writes use a temp file + `os.replace()` for atomicity.

## TODO

- Containerize (Docker).

## License

MIT — see [LICENSE](LICENSE).
