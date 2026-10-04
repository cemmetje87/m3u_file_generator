#!/usr/bin/env python3
"""Orchestrator: download M3U playlists, filter entries, build master_iptv.m3u.

Pipeline: load config -> load URLs -> skip excluded/erroring domains ->
(optionally) speed-test and keep the fastest -> download + parse + filter ->
archive previous master -> write new master -> report domain stats.
"""
import argparse
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import archive_playlist
import vocabulary
from db import DomainErrorTracker
from downloader import download_m3u, test_url_speed
from filters import M3UFilterConfig
from parser import create_master_m3u, parse_m3u


def cleanup_cache(cache_dir="cache"):
    """Delete all files in the cache directory"""
    print(f"\nCleaning up cache directory: {cache_dir}...")
    cache_path = Path(cache_dir)

    if not cache_path.exists():
        print(f"  Cache directory does not exist.")
        return

    try:
        shutil.rmtree(cache_path)
        # Recreate empty directory
        cache_path.mkdir(exist_ok=True)
        print(f"  ✓ Cache cleaned.")
    except Exception as e:
        print(f"  ✗ Failed to clean cache: {e}")


def select_fastest_urls(urls, top_count, error_tracker, max_workers=10):
    """Speed-test URLs concurrently and return the top_count fastest."""
    print(f"\nTesting URL speeds to select top {top_count} fastest...")
    url_speeds = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(test_url_speed, url, 10, error_tracker): url
            for url in urls
        }
        completed = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            completed += 1
            speed = future.result()
            status = "✗ Failed" if speed == float('inf') else f"✓ {speed:.2f}s"
            print(f"  [{completed}/{len(urls)}] {status} {url[:60]}...")
            url_speeds.append((url, speed))

    # Sort by speed and take top N
    url_speeds.sort(key=lambda x: x[1])
    fastest = [url for url, speed in url_speeds[:top_count]]
    print(f"\nSelected top {len(fastest)} fastest URLs\n")
    return fastest


def main():
    # Parse arguments
    parser = argparse.ArgumentParser(description="Process IPTV M3U files")
    parser.add_argument("--input", default="urls.txt", help="Input file with URLs")
    parser.add_argument("--config", default="m3u_filter_config.json", help="Filter config file")
    args = parser.parse_args()

    # Clean cache at startup
    cleanup_cache()

    # Initialize domain error tracker
    error_tracker = DomainErrorTracker()

    # Load filter configuration
    config_file = args.config
    print(f"Loading filter configuration from {config_file}...")
    try:
        filter_config = M3UFilterConfig(config_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Processing stopped.")
        error_tracker.close()
        return
    except ValueError as e:
        # Invalid regex or other config validation error
        print(f"Error: {e}")
        print("Processing stopped.")
        error_tracker.close()
        return

    print(f"  Exclude Group Patterns: exact={len(filter_config.exclude_group_exact)}, "
          f"contains={len(filter_config.exclude_group_contains)}, "
          f"startswith={len(filter_config.exclude_group_startswith)}")
    print(f"  Include Group Patterns: exact={len(filter_config.include_group_exact)}, "
          f"contains={len(filter_config.include_group_contains)}, "
          f"startswith={len(filter_config.include_group_startswith)}")
    print(f"  Top Fastest URLs: {filter_config.top_fastest_count if filter_config.top_fastest_count > 0 else 'all'}")
    print(f"  Domain Error Threshold: {filter_config.domain_error_threshold if filter_config.domain_error_threshold > 0 else 'disabled'}")

    # Load URLs from text file (all alive URLs)
    input_file = args.input
    print(f"\nLoading URLs from {input_file}...")
    try:
        with open(input_file, 'r') as f:
            all_urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: Input file {input_file} not found.")
        error_tracker.close()
        return

    print(f"Found {len(all_urls)} URLs")

    # Filter out URLs containing excluded domains
    urls = []
    excluded_url_count = 0
    skipped_error_threshold_count = 0
    for url in all_urls:
        matched_domain = filter_config.is_url_domain_excluded(url)
        if matched_domain:
            excluded_url_count += 1
            continue

        # Check if domain should be skipped based on error threshold
        if error_tracker.should_skip_domain(url, filter_config.domain_error_threshold):
            domain = error_tracker.extract_domain(url)
            stats = error_tracker.get_domain_stats(domain)
            print(f"  Skipping domain {domain} (errors: {stats['error_count']}, threshold: {filter_config.domain_error_threshold})")
            skipped_error_threshold_count += 1
            continue

        urls.append(url)

    if excluded_url_count > 0:
        print(f"Excluded {excluded_url_count} URLs based on domain filters")
    if skipped_error_threshold_count > 0:
        print(f"Skipped {skipped_error_threshold_count} URLs based on error threshold")
    print(f"Processing {len(urls)} URLs")

    # Test URL speeds if top_fastest_count is set
    if filter_config.top_fastest_count > 0 and len(urls) > filter_config.top_fastest_count:
        urls = select_fastest_urls(urls, filter_config.top_fastest_count, error_tracker)
    else:
        print(f"Using all {len(urls)} URLs\n")

    all_entries = []
    # Collected before filtering: the UI offers these as lookup values when
    # you write a rule, and you cannot write a rule for a group you can't see.
    vocab = vocabulary.VocabularyCollector()

    # Download and process each M3U
    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{len(urls)}] Processing M3U...")

        # Download M3U
        cache_file = download_m3u(url, error_tracker=error_tracker)
        if not cache_file:
            continue

        # Parse M3U
        entries = parse_m3u(cache_file)
        print(f"  Found {len(entries)} total entries")

        # Filter entries
        allowed_count = 0
        excluded_count = 0

        for entry in entries:
            vocab.add_entry(entry)

            # Apply filter configuration
            is_allowed, reason = filter_config.is_entry_allowed(entry)

            if not is_allowed:
                excluded_count += 1
                continue

            all_entries.append(entry)
            allowed_count += 1

        print(f"  - Allowed entries: {allowed_count}")
        print(f"  - Excluded by filters: {excluded_count}")

    # Archive the current master playlist (if it exists) before creating a new one
    archive_playlist.archive_playlist()

    # Create master M3U
    print("\n" + "="*60)
    print("Creating master M3U file...")
    total_entries = create_master_m3u(all_entries)

    print(f"\nMaster M3U created with:")
    print(f"  - Total unique entries: {total_entries}")

    # Save the value vocabulary for the filter builder's lookups. A run where
    # every download failed has nothing to say, and saving it would wipe a
    # good index from an earlier run.
    if vocab.entries_seen == 0:
        print("\nNo entries parsed - keeping the existing filter vocabulary.")
    else:
        try:
            vocab_meta = vocab.save()
            print(f"\nFilter vocabulary captured from {vocab_meta['entries_seen']} entries:")
            for field, info in vocab_meta["fields"].items():
                note = f" (kept top {info['kept']})" if info["truncated"] else ""
                print(f"  - {field}: {info['total']} distinct values{note}")
        except OSError as e:
            print(f"  Warning: could not save filter vocabulary: {e}")

    print("\nFiles created:")
    print("  - master_iptv.m3u (filtered M3U file)")
    print("  - cache/ directory (downloaded M3U files)")

    # Display domain statistics
    print("\n" + "="*60)
    print("Domain Error Statistics:")
    print("="*60)

    all_stats = error_tracker.get_all_domain_stats()
    if all_stats:
        print(f"\n{'Domain':<40} {'Errors':<8} {'Success':<8} {'Last Error':<20}")
        print("-" * 80)
        for stats in all_stats[:20]:  # Show top 20 domains with most errors
            last_error = stats['last_error_time'][:19] if stats['last_error_time'] else 'Never'
            print(f"{stats['domain']:<40} {stats['error_count']:<8} {stats['success_count']:<8} {last_error:<20}")

        if len(all_stats) > 20:
            print(f"\n... and {len(all_stats) - 20} more domains")
    else:
        print("No domain statistics available yet.")

    # Close database connection
    error_tracker.close()

    # Clean cache at the end
    cleanup_cache()


if __name__ == "__main__":
    main()
