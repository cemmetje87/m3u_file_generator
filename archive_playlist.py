#!/usr/bin/env python3
import shutil
from datetime import datetime
from pathlib import Path

def archive_playlist():
    """
    Archives the current 'master_iptv.m3u' to the 'archive' directory with a timestamp.
    Maintains only the last 10 backup files.
    """
    source_file = Path("master_iptv.m3u")
    archive_dir = Path("archive")
    max_backups = 10

    if not source_file.exists():
        print(f"  ℹ No existing master playlist found to archive.")
        return

    print("\n" + "="*60)
    print("Archiving previous master playlist...")
    
    # Create archive directory
    archive_dir.mkdir(exist_ok=True)

    # Generate destination filename
    date_str = datetime.now().strftime("%Y%m%d")
    # Handle potential duplicate names in same day by appending time if needed, 
    # though requirements said playlist_yyyyMMdd.m3u. 
    # If multiple runs per day, this simple naming overwrites the day's backup which fits "playlist_{{yyyyMMdd}}.m3u".
    dest_filename = f"playlist_{date_str}.m3u"
    dest_path = archive_dir / dest_filename

    try:
        # Copy file
        shutil.copy2(source_file, dest_path)
        print(f"  ✓ Archived to: {dest_path}")
    except Exception as e:
        print(f"  ✗ Failed to archive: {e}")
        return

    # Rotation logic (Keep last N)
    # Get all playlist files in archive matching pattern
    backups = sorted(archive_dir.glob("playlist_*.m3u"))
    
    # If we have more than max_backups
    if len(backups) > max_backups:
        print(f"  Rotating archives (keeping last {max_backups})...")
        # Calculate how many to remove
        num_to_remove = len(backups) - max_backups
        files_to_remove = backups[:num_to_remove]
        
        for backup in files_to_remove:
            try:
                backup.unlink()
                print(f"    - Removed old backup: {backup.name}")
            except Exception as e:
                print(f"    ! Failed to remove {backup.name}: {e}")
    
    print("="*60 + "\n")

if __name__ == "__main__":
    archive_playlist()
