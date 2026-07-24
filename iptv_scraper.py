#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import re
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

def fetch_page(url):
    """Fetch a page and return the content"""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.text
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def check_url_alive_with_latency(url):
    """Check if a URL is alive and measure latency"""
    try:
        # Measure latency with HEAD request
        start_time = time.time()
        response = requests.head(url, timeout=10, allow_redirects=True)
        latency = time.time() - start_time
        
        if response.status_code == 200:
            return True, latency
        
        # If HEAD fails, try GET with a small range
        start_time = time.time()
        response = requests.get(url, timeout=10, stream=True)
        latency = time.time() - start_time
        
        if response.status_code == 200:
            return True, latency
        
        return False, None
    except Exception as e:
        return False, None

def validate_urls_with_latency(urls, max_workers=10):
    """Validate multiple URLs concurrently and measure latency"""
    url_latencies = []
    
    print(f"\nValidating {len(urls)} URLs and measuring latency (this may take a while)...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {executor.submit(check_url_alive_with_latency, url): url for url in urls}
        
        completed = 0
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            completed += 1
            
            try:
                is_alive, latency = future.result()
                if is_alive:
                    url_latencies.append({"url": url, "latency": latency})
                    print(f"[{completed}/{len(urls)}] ✓ ALIVE ({latency:.3f}s): {url[:60]}...")
                else:
                    print(f"[{completed}/{len(urls)}] ✗ DEAD: {url[:60]}...")
            except Exception as e:
                print(f"[{completed}/{len(urls)}] ✗ ERROR: {url[:60]}... ({e})")
    
    return url_latencies

def extract_m3u_urls(html_content):
    """Extract M3U URLs from HTML content"""
    # Pattern to match URLs with type=m3u_plus or type=m3u
    pattern = r'http[s]?://[^\s<>"]+(?:type=m3u(?:_plus)?)[^\s<>"]*'
    urls = re.findall(pattern, html_content)
    return urls

def get_post_links(main_page_url):
    """Get all blog post links from the main M3U page"""
    html = fetch_page(main_page_url)
    if not html:
        return []
    
    soup = BeautifulSoup(html, 'html.parser')
    post_links = []
    
    # Find all links to blog posts (they contain /2025/ in the URL)
    for link in soup.find_all('a', href=True):
        href = link['href']
        if '/2025/' in href and 'list-of-' in href and href not in post_links:
            post_links.append(href)
    
    return post_links

import argparse

def main():
    parser = argparse.ArgumentParser(description="IPTV M3U Scraper")
    parser.add_argument("--url", default="https://www.iptvregion.eu.org/search/label/M3U", help="Search URL to scrape")
    args = parser.parse_args()
    
    main_url = args.url
    
    print(f"Fetching main page: {main_url}")
    post_links = get_post_links(main_url)
    print(f"Found {len(post_links)} post links")
    
    all_urls = []
    
    # Fetch each post and extract URLs
    for i, post_url in enumerate(post_links, 1):
        print(f"Processing post {i}/{len(post_links)}: {post_url}")
        html = fetch_page(post_url)
        if html:
            urls = extract_m3u_urls(html)
            all_urls.extend(urls)
            time.sleep(0.5)  # Be nice to the server
    
    # Remove duplicates
    all_urls = list(set(all_urls))
    print(f"\nTotal URLs found: {len(all_urls)}")
    
    # Filter URLs: must contain "type=m3u_plus" and must NOT contain "VOD"
    filtered_urls = [
        url for url in all_urls 
        if "type=m3u_plus" in url and "VOD" not in url.upper()
    ]
    
    print(f"URLs with type=m3u_plus (excluding VOD): {len(filtered_urls)}")
    
    # Validate URLs to check if they're alive and measure latency
    url_latencies = validate_urls_with_latency(filtered_urls)
    
    # Sort by latency (fastest first) and keep only top 25
    url_latencies.sort(key=lambda x: x["latency"])
    #top_25_fastest = url_latencies[:25]
    top_25_fastest = url_latencies
    
    print(f"\n{'='*60}")
    print(f"Validation Summary:")
    print(f"  Total URLs checked: {len(filtered_urls)}")
    print(f"  Alive URLs: {len(url_latencies)}")
    print(f"  Top 25 fastest URLs selected")
    print(f"{'='*60}\n")
    
    # Print top 25 with latency
    print("Top 25 Fastest URLs:")
    for i, item in enumerate(top_25_fastest, 1):
        print(f"  {i}. ({item['latency']:.3f}s) {item['url']}")
    
    # Save only top 25 fastest URLs to JSON (clean, no metadata)
    top_25_urls = [item["url"] for item in top_25_fastest]
    
    output_file = "iptv_m3u_urls.json"
    with open(output_file, 'w') as f:
        json.dump(top_25_urls, f, indent=2)
    
    print(f"\nTop 25 fastest URLs saved to {output_file}")
    
    # Save to text file
    text_file = "iptv_m3u_urls.txt"
    with open(text_file, 'w') as f:
        for url in top_25_urls:
            f.write(url + '\n')
    
    print(f"Top 25 fastest URLs saved to {text_file}")

if __name__ == "__main__":
    main()
