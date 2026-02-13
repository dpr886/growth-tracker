"""
Growth Hirings Tracker
Polls Slack #growth-openings for job URLs, extracts details via Claude API,
and creates entries in the Notion database.

Runs as a cron job every 5-15 minutes.
"""

import os
import re
import json
import time
import logging
from datetime import datetime, timezone

import requests
import anthropic
from notion_client import Client as NotionClient

# ── Config ──────────────────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ.get(
    "NOTION_DATABASE_ID", "2e9bdb1a-66df-803e-a613-d59ed1397517"
)

SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "C0ADKCQTZHU")  # #growth-openings
LAST_TS_FILE = "last_processed_ts.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Clients ─────────────────────────────────────────────────────────────────
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
notion = NotionClient(auth=NOTION_TOKEN)


# ── Helpers ─────────────────────────────────────────────────────────────────
def get_last_processed_ts() -> str:
    """Read the last processed Slack message timestamp."""
    if os.path.exists(LAST_TS_FILE):
        with open(LAST_TS_FILE, "r") as f:
            return f.read().strip()
    return "0"


def save_last_processed_ts(ts: str):
    """Persist the latest processed timestamp."""
    with open(LAST_TS_FILE, "w") as f:
        f.write(ts)


def extract_urls(text: str) -> list[str]:
    """Extract URLs from a Slack message (handles Slack's <url> format)."""
    # Slack wraps URLs in angle brackets: <https://example.com>
    slack_urls = re.findall(r"<(https?://[^>|]+)(?:\|[^>]*)?>", text)
    if slack_urls:
        return slack_urls
    # Fallback: plain URLs
    return re.findall(r"https?://[^\s<>\"]+", text)


def decode_linkedin_activity_date(url: str) -> str | None:
    """Extract date from LinkedIn activity ID in the URL."""
    match = re.search(r"activity[:-](\d+)", url)
    if match:
        activity_id = int(match.group(1))
        timestamp_ms = activity_id >> 22
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    return None


def fetch_page_content(url: str) -> str:
    """Fetch the text content of a job posting URL."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        # Return first 15k chars to stay within context limits
        return resp.text[:15000]
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return ""


# Known job board domains to look for inside LinkedIn post pages
JOB_BOARD_PATTERNS = [
    "greenhouse.io", "boards.greenhouse.io",
    "lever.co", "jobs.lever.co",
    "ashbyhq.com", "jobs.ashbyhq.com",
    "workday.com", "myworkdayjobs.com",
    "smartrecruiters.com",
    "jobvite.com",
    "icims.com",
    "applytojob.com",
    "bamboohr.com",
    "recruitee.com",
    "workable.com",
    "breezy.hr",
    "jazz.co", "resumator.com",
    "wellfound.com",
    "linkedin.com/jobs/view",
]


def extract_job_urls_from_page(page_content: str, source_url: str) -> list[str]:
    """Extract job board URLs embedded within a LinkedIn post page."""
    # Find all URLs in the HTML content
    raw_urls = re.findall(r'https?://[^\s<>"\'\\,;)}\]]+', page_content)

    job_urls = []
    seen = set()
    for url in raw_urls:
        # Clean trailing punctuation
        url = url.rstrip(".")
        # Skip the source URL itself
        if url == source_url:
            continue
        # Check if it matches a known job board
        if any(domain in url for domain in JOB_BOARD_PATTERNS):
            if url not in seen:
                seen.add(url)
                job_urls.append(url)

    log.info(f"Found {len(job_urls)} job board URL(s) inside page: {job_urls}")
    return job_urls


def extract_job_details(page_content: str, url: str, source_url: str | None, linkedin_post_content: str | None = None) -> dict | None:
    """Use Claude to extract structured job details from page content."""
    if not page_content and not linkedin_post_content:
        return None

    content_sections = ""
    if linkedin_post_content:
        content_sections += f"""LinkedIn Post Content (use this for company name if not found in job page):
{linkedin_post_content[:8000]}

---

"""
    if page_content:
        content_sections += f"""Job Page HTML Content:
{page_content}"""

    prompt = f"""Extract job posting details from the content below.
Return ONLY a JSON object with these fields (use null for any field not found):

{{
  "company_name": "Company name",
  "open_role": "Job title",
  "job_type": "Full-time or Part-time",
  "location": "Location(s), semicolon-separated if multiple",
  "compensation_range": "Salary/comp range if listed",
  "link_to_apply": "Direct application URL if found in the page, otherwise null",
  "job_listed_date": "Date the job was posted in YYYY-MM-DD format, or null if not found"
}}

Important:
- For company_name, look in both the job page AND the LinkedIn post content. Check the LinkedIn poster's profile/company, the post text, or the job page title/header. Never return null or "Unknown" if a company is mentioned anywhere.
- Default job_type to "Full-time" if not specified
- For location, combine all listed locations with semicolons
- For compensation, include the full range as stated
- For job_listed_date, look for any posting date, published date, or "posted X days/weeks ago" and convert to YYYY-MM-DD
- Only return the JSON, no other text

URL of the posting: {url}

{content_sections}"""

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        # Clean up markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception as e:
        log.error(f"Claude extraction failed: {e}")
        return None


def is_duplicate(link_to_apply: str) -> bool:
    """Check if an entry with this Link to Apply URL already exists in Notion."""
    try:
        results = notion.databases.query(
            database_id=NOTION_DATABASE_ID,
            filter={"property": "Link to Apply", "url": {"equals": link_to_apply}},
            page_size=1,
        )
        if results["results"]:
            log.info(f"⏭️ Duplicate found for {link_to_apply}, skipping.")
            return True
    except Exception as e:
        log.warning(f"Dedup check failed: {e}")
    return False


def create_notion_entry(details: dict, source: str, job_listed_date: str | None):
    """Create a new page in the Notion database."""
    # Dedup: skip if this job URL already exists
    link = details.get("link_to_apply")
    if link and is_duplicate(link):
        return False

    properties = {
        "Company Name": {"title": [{"text": {"content": details.get("company_name") or "Unknown"}}]},
    }

    # Text properties
    text_mappings = {
        "Open Role": details.get("open_role"),
        "Location": details.get("location"),
        "Compensation Range": details.get("compensation_range"),
        "Source": source,
    }
    for prop, value in text_mappings.items():
        if value:
            properties[prop] = {"rich_text": [{"text": {"content": value}}]}

    # URL property
    link = details.get("link_to_apply")
    if link:
        properties["Link to Apply"] = {"url": link}

    # Select property
    job_type = details.get("job_type", "Full-time")
    if job_type in ("Full-time", "Part-time"):
        properties["Job Type"] = {"select": {"name": job_type}}

    # Date property
    if job_listed_date:
        properties["Job Listed Date"] = {"date": {"start": job_listed_date}}

    try:
        notion.pages.create(parent={"database_id": NOTION_DATABASE_ID}, properties=properties)
        log.info(f"✅ Added to Notion: {details.get('company_name')} — {details.get('open_role')}")
        return True
    except Exception as e:
        log.error(f"Notion creation failed: {e}")
        return False


def post_slack_reaction(channel: str, timestamp: str, emoji: str = "white_check_mark"):
    """Add a reaction to a Slack message to indicate it's been processed."""
    try:
        requests.post(
            "https://slack.com/api/reactions.add",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"channel": channel, "timestamp": timestamp, "name": emoji},
        )
    except Exception:
        pass  # Non-critical


# ── Main Loop ───────────────────────────────────────────────────────────────
def poll_and_process():
    """Main function: read new Slack messages, extract job details, update Notion."""
    last_ts = get_last_processed_ts()
    log.info(f"Polling Slack since ts={last_ts}")

    # Fetch messages from Slack
    resp = requests.get(
        "https://slack.com/api/conversations.history",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={
            "channel": SLACK_CHANNEL_ID,
            "oldest": last_ts,
            "limit": 50,
        },
    )
    data = resp.json()
    if not data.get("ok"):
        log.error(f"Slack API error: {data.get('error')}")
        return

    messages = data.get("messages", [])
    if not messages:
        log.info("No new messages.")
        return

    # Process oldest first
    messages.sort(key=lambda m: float(m["ts"]))
    latest_ts = last_ts

    for msg in messages:
        text = msg.get("text", "")
        ts = msg["ts"]
        urls = extract_urls(text)

        if not urls:
            continue

        log.info(f"Processing message with {len(urls)} URL(s): {urls}")

        # Categorize URLs
        linkedin_post_url = None
        job_urls = []

        for u in urls:
            if "linkedin.com" in u and ("/feed/" in u or "/posts/" in u or "activity" in u):
                linkedin_post_url = u
            else:
                job_urls.append(u)

        # Fetch LinkedIn post content if available (used for company name extraction)
        linkedin_post_content = None
        if linkedin_post_url:
            log.info(f"Fetching LinkedIn post for context: {linkedin_post_url}")
            linkedin_post_content = fetch_page_content(linkedin_post_url)

        # If only LinkedIn post URL, scan for embedded job links
        if not job_urls and linkedin_post_url:
            log.info(f"LinkedIn post detected, scanning for embedded job URLs...")
            embedded_job_urls = extract_job_urls_from_page(linkedin_post_content or "", linkedin_post_url)
            if embedded_job_urls:
                job_urls = embedded_job_urls
            else:
                # No embedded job links found, use the post itself
                job_urls = [linkedin_post_url]

        # If only non-LinkedIn job URLs, use them as source too
        source_url = linkedin_post_url or urls[0]

        # Decode LinkedIn date if available
        linkedin_date = None
        if linkedin_post_url:
            linkedin_date = decode_linkedin_activity_date(linkedin_post_url)

        # Fallback date: use Slack message timestamp
        slack_date = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")

        for job_url in job_urls:
            log.info(f"Fetching: {job_url}")
            content = fetch_page_content(job_url)
            # Pass LinkedIn post content for better company name extraction
            # Also include the original Slack message text as fallback context
            post_ctx = linkedin_post_content if (job_url != linkedin_post_url) else None
            slack_ctx = f"Slack message text: {text}\n\n" if text else ""
            combined_post_ctx = (slack_ctx + (post_ctx or "")).strip() or None
            details = extract_job_details(content, job_url, source_url, linkedin_post_content=combined_post_ctx)

            if details:
                # Use the direct job URL as Link to Apply if Claude didn't find one
                if not details.get("link_to_apply"):
                    details["link_to_apply"] = job_url

                # Date priority: Claude-extracted > LinkedIn activity ID > Slack message date
                job_date = details.pop("job_listed_date", None) or linkedin_date or slack_date

                success = create_notion_entry(
                    details=details,
                    source=source_url,
                    job_listed_date=job_date,
                )
                if success:
                    post_slack_reaction(SLACK_CHANNEL_ID, ts)
            else:
                log.warning(f"Could not extract details from {job_url}")
                post_slack_reaction(SLACK_CHANNEL_ID, ts, "warning")

        latest_ts = ts

    save_last_processed_ts(latest_ts)
    log.info(f"Done. Processed {len(messages)} messages. Last ts={latest_ts}")


if __name__ == "__main__":
    poll_and_process()
