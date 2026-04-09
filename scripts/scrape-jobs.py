#!/usr/bin/env python3
"""
Phase 2 job scraper: scrapes enabled sources and appends new rows to jobs.csv.
Safe to re-run — deduplicates by URL against existing rows.

Sources:
  remoteok        — public JSON API, no auth
  weworkremotely  — RSS feed, no auth
  hn_who_is_hiring — static HTML thread, no auth
  anthropic        — JS-rendered careers page via Playwright

LinkedIn, Indeed, Wellfound: skipped (bot detection / auth wall).
"""

import argparse
import csv
import logging
import re
import subprocess
import sys
import warnings
from datetime import date
from pathlib import Path
from typing import Optional
import requests
import yaml
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

log = logging.getLogger(__name__)

# Keywords extracted from the 21 configured roles for fuzzy title matching.
# Two-tier: domain keyword + seniority keyword → match.
_DOMAIN_KEYWORDS = {
    "sre", "site reliability", "platform engineer", "platform engineering",
    "devops", "infrastructure", "cloud engineer", "cloud engineering",
    "ci/cd", "build engineer", "release engineer", "production engineer",
}
_SENIORITY_KEYWORDS = {"senior", "staff", "principal", "lead", "sr.", "sr "}


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_output_dir(config: dict) -> Path:
    raw = config.get("output_dir", "~/git/docs-local/daily-driver")
    expanded = Path(raw).expanduser()
    return expanded


def scraper_cfg(config: dict) -> dict:
    return config.get("job_search", {}).get("scraper", {})


def roles_list(config: dict) -> list[str]:
    return config.get("job_search", {}).get("roles", [])


def user_agent(config: dict) -> str:
    return scraper_cfg(config).get(
        "user_agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    )


def timeout_seconds(config: dict) -> int:
    return int(scraper_cfg(config).get("timeout", 15))


# ── CSV helpers ───────────────────────────────────────────────────────────────

def load_existing_urls(csv_path: Path) -> tuple[set[str], list[str], int]:
    """Return (known_urls, header_columns, next_row_number)."""
    if not csv_path.exists():
        return set(), [], 1
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return set(), [], 1
        link_idx = header.index("Link") if "Link" in header else None
        known_urls: set[str] = set()
        max_num = 0
        for row in reader:
            if not row:
                continue
            try:
                num = int(row[0])
                max_num = max(max_num, num)
            except (ValueError, IndexError):
                pass
            if link_idx is not None and link_idx < len(row) and row[link_idx]:
                known_urls.add(row[link_idx].strip())
    return known_urls, header, max_num + 1


def append_jobs(csv_path: Path, jobs: list[dict], header: list[str], next_num: int) -> int:
    """Append new jobs to CSV. Returns count of rows written."""
    if not jobs:
        return 0

    def col(name: str) -> Optional[int]:
        return header.index(name) if name in header else None

    num_idx = col("#")
    company_idx = col("Company")
    product_idx = col("Product/Purpose")
    role_idx = col("Role")
    location_idx = col("Location")
    source_idx = col("Source")
    date_idx = col("Date Found")
    status_idx = col("Status")
    link_idx = col("Link")

    written = 0
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        for job in jobs:
            row = [""] * len(header)
            if num_idx is not None:
                row[num_idx] = str(next_num)
            if company_idx is not None:
                row[company_idx] = job.get("company", "")
            if product_idx is not None:
                # Scrapers that provide product context can pass it through; default flags rows needing manual fill
                row[product_idx] = job.get("product", "(auto-scraped -- needs fill)")
            if role_idx is not None:
                row[role_idx] = job.get("role", "")
            if location_idx is not None:
                row[location_idx] = job.get("location", "")
            if source_idx is not None:
                row[source_idx] = job.get("source", "")
            if date_idx is not None:
                row[date_idx] = job.get("date_found", date.today().isoformat())
            if status_idx is not None:
                row[status_idx] = "found"
            if link_idx is not None:
                row[link_idx] = job.get("url", "")
            writer.writerow(row)
            next_num += 1
            written += 1
    return written


# ── Role matching ─────────────────────────────────────────────────────────────

def matches_roles(title: str, roles: list[str]) -> bool:
    """
    True if the job title is relevant based on configured roles.
    Tier 1: exact substring match against any configured role.
    Tier 2: domain keyword + seniority keyword present in title.
    """
    title_lower = title.lower()

    # Tier 1 — configured role is a substring of the job title
    for role in roles:
        if role.lower() in title_lower:
            return True

    # Tier 2 — domain + seniority keyword both present
    has_domain = any(kw in title_lower for kw in _DOMAIN_KEYWORDS)
    has_seniority = any(kw in title_lower for kw in _SENIORITY_KEYWORDS)
    if has_domain and has_seniority:
        return True

    # Tier 2b — standalone domain keywords that don't need seniority (e.g. "SRE")
    standalone = {"sre", "platform engineer"}
    if any(kw in title_lower for kw in standalone):
        return True

    return False


# ── Scrapers ──────────────────────────────────────────────────────────────────

def scrape_remoteok(config: dict) -> list[dict]:
    """
    Public JSON API — no auth, no JavaScript required.
    First element is always a metadata dict (has 'legal' key); skip it.
    """
    max_jobs = scraper_cfg(config).get("remoteok_max_jobs", 150)
    roles = roles_list(config)
    headers = {"User-Agent": user_agent(config)}
    timeout = timeout_seconds(config)

    resp = requests.get("https://remoteok.com/api", headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for item in data[1:]:  # skip metadata element
        if not isinstance(item, dict):
            continue
        title = item.get("position", "")
        if not matches_roles(title, roles):
            continue
        url = item.get("url", "")
        if not url and item.get("id"):
            url = f"https://remoteok.io/remote-jobs/{item['id']}"
        jobs.append({
            "company": item.get("company", ""),
            "role": title,
            "location": item.get("location", "Remote") or "Remote",
            "url": url,
            "source": "RemoteOK",
            "date_found": date.today().isoformat(),
        })
        if len(jobs) >= max_jobs:
            break

    log.info("[remoteok] %d jobs matched", len(jobs))
    return jobs


def scrape_weworkremotely(config: dict) -> list[dict]:
    """
    RSS feed — no auth. Parses XML with BeautifulSoup.
    Title format: "COMPANY: ROLE TITLE"
    Custom <region> tag holds location data.
    """
    roles = roles_list(config)
    headers = {"User-Agent": user_agent(config)}
    timeout = timeout_seconds(config)

    resp = requests.get(
        "https://weworkremotely.com/remote-jobs.rss",
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()

    # Try lxml XML parser first; fall back to html.parser which lowercases tags
    try:
        import lxml  # noqa: F401
        soup = BeautifulSoup(resp.content, "xml")
        items = soup.find_all("item")
    except ImportError:
        soup = BeautifulSoup(resp.content, "html.parser")
        items = soup.find_all("item")

    jobs = []
    for item in items:
        title_tag = item.find("title")
        if not title_tag:
            continue
        title_text = title_tag.get_text(strip=True)

        # Skip category header entries (WWR includes them as empty items)
        if not title_text or title_text.lower() == "jobs":
            continue

        # Company: Role — split on ": "
        if ": " in title_text:
            company, role = title_text.split(": ", 1)
        else:
            company, role = "", title_text

        if not matches_roles(role, roles):
            continue

        # URL via <guid> (reliable) or <link>
        guid_tag = item.find("guid")
        link_tag = item.find("link")
        url = ""
        if guid_tag:
            url = guid_tag.get_text(strip=True)
        elif link_tag:
            url = link_tag.get_text(strip=True)

        region_tag = item.find("region")
        location = region_tag.get_text(strip=True) if region_tag else "Remote"

        jobs.append({
            "company": company.strip(),
            "role": role.strip(),
            "location": location,
            "url": url,
            "source": "We Work Remotely",
            "date_found": date.today().isoformat(),
        })

    log.info("[weworkremotely] %d jobs matched", len(jobs))
    return jobs


def scrape_hn_who_is_hiring(config: dict) -> list[dict]:
    """
    Finds the current month's Who's Hiring thread and parses top-level comments.
    Comment headline format (convention): "Company | Role | Location | ..."
    """
    max_posts = scraper_cfg(config).get("hn_max_posts", 100)
    roles = roles_list(config)
    headers = {"User-Agent": user_agent(config)}
    timeout = timeout_seconds(config)
    current_month_str = date.today().strftime("%B %Y")  # e.g. "April 2026"

    # Step 1: find the thread for the current month
    index_resp = requests.get(
        "https://news.ycombinator.com/submitted?id=whoishiring",
        headers=headers,
        timeout=timeout,
    )
    index_resp.raise_for_status()
    index_soup = BeautifulSoup(index_resp.text, "html.parser")

    thread_url = None
    for a_tag in index_soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        if "Who is hiring?" in text and current_month_str in text:
            href = a_tag.get("href", "")
            if href.startswith("item?id="):
                thread_url = f"https://news.ycombinator.com/{href}"
            elif "news.ycombinator.com" in href:
                thread_url = href
            break

    if not thread_url:
        log.warning("[hn_who_is_hiring] could not find thread for %s", current_month_str)
        return []

    # Step 2: fetch the thread
    thread_resp = requests.get(thread_url, headers=headers, timeout=timeout)
    thread_resp.raise_for_status()
    thread_soup = BeautifulSoup(thread_resp.text, "html.parser")

    jobs = []
    comment_rows = thread_soup.find_all("tr", class_="athing comtr")

    for row in comment_rows:
        if len(jobs) >= max_posts:
            break

        # Top-level comments have indent image width=0
        ind = row.find("td", class_="ind")
        if ind:
            img = ind.find("img")
            if img and img.get("width", "0") != "0":
                continue  # nested comment, skip

        comment_id = row.get("id", "")
        comment_div = row.find("div", class_="comment")
        if not comment_div:
            continue

        commtext = comment_div.find("div", class_="commtext")
        if not commtext:
            continue

        # First line is the job headline
        raw_text = commtext.get_text(separator="\n", strip=True)
        first_line = raw_text.split("\n")[0].strip()
        if not first_line:
            continue

        # Parse "Company | Role | Location | ..." format
        parts = [p.strip() for p in first_line.split("|")]
        if len(parts) < 2:
            continue

        company = parts[0]
        # Role is typically parts[1]; check all parts for role match
        role = parts[1] if len(parts) > 1 else ""

        if not matches_roles(" ".join(parts), roles):
            continue

        # Location: look for city/remote keyword in parts[2+]
        location = "Remote"
        for part in parts[2:]:
            if re.search(r'\bremote\b', part, re.IGNORECASE):
                location = "Remote"
                break
            # If it looks like a city (short, no http, no $)
            if len(part) < 40 and "http" not in part and "$" not in part:
                location = part
                break

        url = f"https://news.ycombinator.com/item?id={comment_id}" if comment_id else thread_url

        jobs.append({
            "company": company,
            "role": role,
            "location": location,
            "url": url,
            "source": "HN Who's Hiring",
            "date_found": date.today().isoformat(),
        })

    log.info("[hn_who_is_hiring] %d jobs matched", len(jobs))
    return jobs


def scrape_anthropic(config: dict) -> list[dict]:
    """
    Playwright-based scraper for the Anthropic careers page.
    JS-rendered — requests alone won't work.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        log.warning("[anthropic] playwright not installed, skipping")
        return []

    roles = roles_list(config)
    jobs = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto("https://www.anthropic.com/careers/jobs", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            content = page.content()
        finally:
            browser.close()

    soup = BeautifulSoup(content, "html.parser")

    # Extract links that look like job postings
    seen_hrefs: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if not re.search(r'greenhouse\.io/anthropic/jobs/\d+', href):
            continue
        if href in seen_hrefs:
            continue
        seen_hrefs.add(href)

        # Title lives in div.jobRole > p.caption; location in div.jobLocation > p.caption
        role_el = a_tag.select_one("[class*='jobRole'] p")
        title = role_el.get_text(strip=True) if role_el else a_tag.get_text(strip=True)
        if not title or not matches_roles(title, roles):
            continue

        loc_el = a_tag.select_one("[class*='jobLocation'] p")
        location = loc_el.get_text(strip=True) if loc_el else ""
        if not location:
            location = "Remote"

        full_url = href if href.startswith("http") else f"https://www.anthropic.com{href}"

        jobs.append({
            "company": "Anthropic",
            "role": title,
            "location": location,
            "url": full_url,
            "source": "Anthropic Careers",
            "date_found": date.today().isoformat(),
        })

    log.info("[anthropic] %d jobs matched", len(jobs))
    return jobs


# ── Orchestrator ──────────────────────────────────────────────────────────────

SCRAPERS: dict[str, callable] = {
    "remoteok": scrape_remoteok,
    "weworkremotely": scrape_weworkremotely,
    "hn_who_is_hiring": scrape_hn_who_is_hiring,
    "anthropic": scrape_anthropic,
}


def run_all_scrapers(config: dict) -> list[dict]:
    source_cfg = scraper_cfg(config).get("sources", {})
    timeout = timeout_seconds(config)
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    for source_id, scraper_fn in SCRAPERS.items():
        if not source_cfg.get(source_id, False):
            log.info("[%s] disabled in config, skipping", source_id)
            continue
        try:
            jobs = scraper_fn(config)
            # Dedup within this batch
            for job in jobs:
                url = job.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_jobs.append(job)
        except requests.exceptions.Timeout:
            log.warning("[%s] timed out after %ds", source_id, timeout)
        except requests.exceptions.RequestException as exc:
            log.warning("[%s] request failed: %s", source_id, exc)
        except Exception as exc:  # noqa: BLE001
            log.error("[%s] unexpected error: %s", source_id, exc, exc_info=True)

    return all_jobs


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape job boards and append to jobs.csv")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Print matches without writing to CSV")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    config_path = (
        Path(args.config) if args.config
        else Path(__file__).parent.parent / "config.yaml"
    )
    if not config_path.exists():
        log.error("config.yaml not found at %s", config_path)
        sys.exit(1)

    config = load_config(config_path)

    if not scraper_cfg(config).get("enabled", False):
        print("Scraper disabled. Set job_search.scraper.enabled: true in config.yaml")
        sys.exit(0)

    output_dir = resolve_output_dir(config)
    csv_path = output_dir / "jobs.csv"

    known_urls, header, next_num = load_existing_urls(csv_path)

    if not header:
        # Initialise header if CSV doesn't exist yet
        header = [
            "#", "Company", "Product/Purpose", "Role", "Comp", "Location",
            "Fit", "GD Rating", "Source", "Date Found", "Status",
            "Date Applied", "Link", "Notes",
        ]
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(header)

    log.info("Loaded %d existing URLs from %s", len(known_urls), csv_path)

    all_jobs = run_all_scrapers(config)
    new_jobs = [j for j in all_jobs if j.get("url") not in known_urls]

    log.info("Found %d jobs total, %d new", len(all_jobs), len(new_jobs))

    if args.dry_run:
        for j in new_jobs:
            print(f"  [{j['source']:22s}] {j['company']:30s} | {j['role']:45s} | {j['location']}")
            print(f"    {j['url']}")
        print(f"\n{len(new_jobs)} new jobs (dry-run, nothing written)")
        return

    written = append_jobs(csv_path, new_jobs, header, next_num)
    print(f"Scraper complete: {written} new jobs appended to {csv_path}")

    if written > 0:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{written} new jobs found" with title "Job Scraper"'],
            check=False,
        )


if __name__ == "__main__":
    main()
