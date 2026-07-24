#!/usr/bin/env python3
"""Parse M3U playlist files and generate the consolidated master playlist."""
import re


def parse_m3u(file_path):
    """Parse M3U file and extract entries"""
    entries = []

    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Split by #EXTINF
        lines = content.split('#EXTINF:')

        for i in range(1, len(lines)):
            entry_lines = lines[i].strip().split('\n', 1)
            if len(entry_lines) < 2:
                continue

            info_line = entry_lines[0]
            url_line = entry_lines[1].strip()

            # Extract group title
            group_match = re.search(r'group-title="([^"]*)"', info_line)
            group = group_match.group(1) if group_match else ""

            # Extract tvg-name
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', info_line)
            tvg_name = tvg_name_match.group(1) if tvg_name_match else ""

            # Extract channel name (after last comma)
            name_parts = info_line.split(',')
            name = name_parts[-1].strip() if name_parts else ""

            entries.append({
                'info': info_line,
                'url': url_line,
                'name': name,
                'group': group,
                'tvg_name': tvg_name
            })

        return entries
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")
        return []


def create_master_m3u(entries, output_file='master_iptv.m3u'):
    """Create master M3U file from filtered entries.

    The full #EXTINF line (attributes AND the channel display name after the
    comma) is preserved - players read the display name from after the last
    comma, so stripping it would leave every channel unnamed.
    """

    # Remove duplicates by channel name
    seen_names = set()
    unique_entries = []

    for entry in entries:
        name_key = entry['name'].strip().lower()
        if name_key not in seen_names:
            seen_names.add(name_key)
            unique_entries.append(entry)

    # Create M3U content
    m3u_content = "#EXTM3U\n\n"

    for entry in unique_entries:
        m3u_content += f"#EXTINF:{entry['info']}\n"
        m3u_content += f"{entry['url']}\n\n"

    # Save master M3U
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(m3u_content)

    return len(unique_entries)
