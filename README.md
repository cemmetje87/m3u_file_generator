# IPTV Scraper and Processor

This project provides a robust system for scraping IPTV M3U playlists from various online sources, applying extensive filtering based on user-defined criteria, validating stream URLs for aliveness and latency, and generating a consolidated, optimized `master_iptv.m3u` playlist. It also features a web-based interface for easy management and real-time monitoring of the scraping and processing tasks.

<img width="1199" height="795" alt="image" src="https://github.com/user-attachments/assets/0475b3dc-ec23-4316-ba60-64165f26837f" />

## Features

-   **Web Interface:** A user-friendly web interface (`static/index.html`) for initiating scraping/processing and viewing real-time logs via WebSockets.
-   **M3U URL Scraping:** Automatically discovers and extracts M3U playlist URLs from specified web pages.
-   **URL Validation & Latency Testing:** Checks the aliveness of scraped URLs and measures their response times to prioritize faster streams.
-   **Advanced M3U Filtering:**
    -   **Configurable Rules:** Utilizes `m3u_filter_config.json` for highly customizable filtering criteria.
    -   **Exclude/Include Logic:** Filter by `group-title`, channel name, `tvg-name`, and URL domain using exact matches, "contains" keywords, "starts with" prefixes, and regular expressions.
    -   **Domain Error Tracking:** Integrates with `domain_errors.db` to keep a history of problematic domains, preventing repeated attempts to use unreliable sources.
-   **M3U Playlist Generation:** Creates a `master_iptv.m3u` file containing only the desired, validated, and fastest IPTV streams.
-   **Duplicate Removal:** Ensures uniqueness of channels in the final `master_iptv.m3u`.
-   **Archiving & Rotation:** Automatically archives generated `master_iptv.m3u` files with a configurable backup rotation.
-   **Cache Management:** Cleans up temporary downloaded M3U files to maintain system hygiene.
-   **Real-time Logging:** Provides live updates on processing status and errors via WebSockets to the connected web interface.

## TODO
**Containerize:** Once proven stable and functioning, I'll work to make it work within a Docker container.

## Installation

### Prerequisites

-   Python 3.8+
-   `uv` (recommended for dependency management) or `pip`

### Steps

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/yourusername/iptv-scraper-processor.git
    cd iptv-scraper-processor
    ```

2.  **Install dependencies:**
    Using `uv` (recommended):
    ```bash
    uv pip install -r requirements.txt
    ```
    Using `pip`:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Prepare configuration:**
    Ensure `m3u_filter_config.json` is set up according to your preferences. A default configuration is usually provided.

## Usage

The application can be used via its web interface or by running the individual Python scripts.

### Running the Web Interface (Recommended)

1.  **Start the FastAPI server:**
    ```bash
    python main.py
    ```
    The server will typically run on `http://0.0.0.0:8000`.

2.  **Access the Web Interface:**
    Open your web browser and navigate to `http://localhost:8000`.

    From the web interface, you can:
    -   Trigger the IPTV scraper.
    -   Trigger the M3U processor.
    -   View real-time logs of the tasks.
    -   Download the generated `master_iptv.m3u`.
    -   View and edit `m3u_filter_config.json`.

### Running Scripts Manually (CLI)

You can also run the core scripts directly from the command line if you prefer.

1.  **Scrape M3U URLs:**
    ```bash
    python iptv_scraper.py --url "https://www.example.com/your-iptv-source"
    ```
    This will output `iptv_m3u_urls.txt` and `iptv_m3u_urls.json` with the discovered and validated URLs.

2.  **Process M3U URLs:**
    ```bash
    python m3u_processor.py --input iptv_m3u_urls.txt --config m3u_filter_config.json
    ```
    This will generate `master_iptv.m3u` based on the input URLs and your filtering configuration.

## Configuration

The primary configuration file is `m3u_filter_config.json`. This JSON file allows you to define detailed rules for how M3U entries are filtered.

### `m3u_filter_config.json` Structure

```json
{
  "settings": {
    "top_fastest_count": 100,         // Number of fastest URLs to keep after speed testing (0 for all)
    "domain_error_threshold": 5       // Number of errors before a domain is temporarily skipped (0 to disable)
  },
  "exclude": {
    "group": {                        // Exclusion rules for group-title
      "exact_match": ["XXX", "ADULT"],
      "contains": ["XXX", "ADULT", "PORN"],
      "startswith": [],
      "regex": []
    },
    "channel": {                      // Exclusion rules for channel name
      "exact_match": [],
      "contains": [],
      "startswith": [],
      "regex": []
    },
    "tvg_name": {                     // Exclusion rules for tvg-name
      "exact_match": [],
      "contains": [],
      "startswith": [],
      "regex": []
    },
    "url_domain": {                   // Exclusion rules for URL domain (e.g., problematic hosts)
      "contains": ["bad-domain.com"]
    }
  },
  "include": {
    "group_prefix": ["NL", "TR"],     // Special AND logic: group must start with one of these prefixes
    "group_content": ["MOVIES", "SERIES"], // AND logic: group must contain one of these keywords
    "group": {                        // Inclusion rules for group-title (OR logic for individual rules, AND for overall fields if defined)
      "exact_match": [],
      "contains": ["Sports", "News"],
      "startswith": [],
      "regex": []
    },
    "channel": {                      // Inclusion rules for channel name
      "exact_match": [],
      "contains": [],
      "startswith": [],
      "regex": []
    },
    "tvg_name": {                     // Inclusion rules for tvg-name
      "exact_match": [],
      "contains": [],
      "startswith": [],
      "regex": []
    }
  }
}
```

-   **`settings`**:
    -   `top_fastest_count`: If greater than 0, `m3u_processor.py` will test the speed of all M3U URLs and only process the specified number of fastest ones.
    -   `domain_error_threshold`: If greater than 0, `m3u_processor.py` will track errors per domain. If a domain accumulates this many errors, it will be temporarily skipped in future processing runs to avoid wasting time on unresponsive sources.
-   **`exclude`**: Defines rules to *remove* M3U entries. If an entry matches *any* exclusion rule, it will be removed.
-   **`include`**: Defines rules to *keep* M3U entries.
    -   `group_prefix` and `group_content` work together with an **AND** logic. If both are specified, a group must match *both* a prefix and a content keyword to be considered.
    -   Other `group`, `channel`, `tvg_name` `include` rules work with **OR** logic within their respective sections (e.g., `group` can contain "Sports" OR "News"). If any `include` fields are defined, an entry must match at least one `include` rule (after passing all `exclude` rules) to be kept. If no `include` rules are defined, all non-excluded entries are kept.

## Project Structure

```
.
├───main.py                 # FastAPI application and web server
├───iptv_scraper.py         # Script to scrape M3U URLs from web sources
├───m3u_processor.py        # Script to process, filter, and consolidate M3U files
├───archive_playlist.py     # Utility for archiving master_iptv.m3u
├───m3u_filter_config.json  # Configuration for M3U filtering rules
├───domain_errors.db        # SQLite database for tracking domain errors
├───master_iptv.m3u         # Generated master IPTV playlist
├───urls.txt                # Example input for m3u_processor.py (list of M3U source URLs)
├───static/                 # Frontend web assets (HTML, CSS, JS)
│   ├───index.html
│   ├───script.js
│   └───style.css
├───cache/                  # Directory for temporary downloaded M3U files
└───archive/                # Directory for archived master_iptv.m3u files
```

## Contributing

Contributions are welcome! Please feel free to open issues or submit pull requests.

## License

This project is open-source and available under the MIT License.
