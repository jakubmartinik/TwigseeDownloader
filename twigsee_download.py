#!/usr/bin/env python3
"""
Twigsee Photo Downloader
========================
Logs into Twigsee, finds posts with photos, opens each post's photo gallery,
and downloads all photos by clicking through the gallery arrows.

Usage:
    python3 twigsee_download.py --no-headless
    python3 twigsee_download.py --max-age-days 7
    python3 twigsee_download.py --download-dir /path/to/photos
"""

import os
import sys
import json
import hashlib
import logging
import argparse
import re
import subprocess
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
# In Home Assistant add-on, /data is persistent storage; fall back to script dir
DATA_DIR = Path("/data") if Path("/data").exists() else SCRIPT_DIR
DEFAULT_DOWNLOAD_DIR = SCRIPT_DIR / "photos"
STATE_FILE = DATA_DIR / "storage_state.json"
MANIFEST_FILE = DATA_DIR / "downloaded.json"  # fallback, overridden in run()
LOG_FILE = DATA_DIR / "twigsee.log"

TWIGSEE_URL = "https://app.twigsee.com"
DEFAULT_MAX_AGE_DAYS = 5
MAX_SCROLL_ATTEMPTS = 30
SCROLL_WAIT_SEC = 2

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("twigsee")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env():
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


def get_credentials():
    email = os.environ.get("TWIGSEE_EMAIL", "")
    password = os.environ.get("TWIGSEE_PASSWORD", "")
    if not email or not password:
        log.error("Missing TWIGSEE_EMAIL or TWIGSEE_PASSWORD.")
        sys.exit(1)
    return email, password


def load_manifest(path):
    if path.exists():
        data = json.loads(path.read_text())
        data.setdefault("uploaded_albums", [])
        return data
    return {"processed_posts": [], "downloaded_files": [], "uploaded_albums": []}


def save_manifest(path, manifest):
    path.write_text(json.dumps(manifest, indent=2))


def make_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def normalize_name(text):
    """Normalize text for use as a folder name: strip emojis, diacritics, spaces, special chars."""
    # Remove emojis and other non-letter/digit/space chars
    cleaned = ""
    for ch in text:
        cat = unicodedata.category(ch)
        # Keep letters, digits, spaces, basic punctuation
        if cat.startswith(("L", "N")) or ch in " -_":
            cleaned += ch
    # Normalize diacritics (č→c, ř→r, etc.)
    nfkd = unicodedata.normalize("NFKD", cleaned)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    # Replace spaces with underscores, collapse multiples
    ascii_text = re.sub(r"[\s_-]+", "_", ascii_text).strip("_")
    return ascii_text or "unknown"


# ---------------------------------------------------------------------------
# Czech date parsing
# ---------------------------------------------------------------------------

CZECH_MONTHS = {
    "leden": 1, "ledna": 1, "únor": 2, "února": 2,
    "březen": 3, "března": 3, "duben": 4, "dubna": 4,
    "květen": 5, "května": 5, "červen": 6, "června": 6,
    "červenec": 7, "července": 7, "srpen": 8, "srpna": 8,
    "září": 9, "říjen": 10, "října": 10,
    "listopad": 11, "listopadu": 11, "prosinec": 12, "prosince": 12,
}


def parse_post_date(text):
    if not text:
        return None
    text = text.strip()

    match = re.search(r"(\d{1,2})\.\s*([a-záéíóúůýčďěňřšťžA-Z]+)", text)
    if match:
        day = int(match.group(1))
        month = CZECH_MONTHS.get(match.group(2).lower())
        if month:
            try:
                return datetime(datetime.now().year, month, day)
            except ValueError:
                pass

    text_lower = text.lower()
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if "dnes" in text_lower or "today" in text_lower:
        return today
    if "včera" in text_lower or "yesterday" in text_lower:
        return today - timedelta(days=1)

    m = re.search(r"před\s+(\d+)\s+dn", text_lower)
    if m:
        return today - timedelta(days=int(m.group(1)))

    return None


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def dismiss_popups(page):
    """Dismiss cookie banners and modals."""
    for text in ["Souhlasím", "OK", "Zavřít", "Rozumím", "Pokračovat"]:
        try:
            btn = page.query_selector(f'button:has-text("{text}")')
            if btn and btn.is_visible():
                btn.click()
                log.info("Dismissed: %s", text)
                time.sleep(0.5)
        except Exception:
            pass
    page.keyboard.press("Escape")
    time.sleep(0.5)
    # Force remove any remaining modals
    page.evaluate('() => document.querySelectorAll(".MuiModal-root").forEach(m => m.remove())')
    time.sleep(0.5)


def get_card_metadata(card):
    """Extract teacher name, date+time, and title from a card."""
    try:
        return card.evaluate('''el => {
            // Teacher name: span right before the clock icon span (in the header)
            let teacher = null;
            const clockUse = [...el.querySelectorAll('use')].find(u => {
                const href = u.getAttribute('xlink:href') || u.getAttribute('href') || '';
                return href === '#twigsee-clock';
            });
            if (clockUse) {
                const clockSpan = clockUse.closest('span');
                if (clockSpan && clockSpan.previousElementSibling) {
                    teacher = clockSpan.previousElementSibling.textContent.trim();
                }
            }

            // Date+time: span with clock icon
            let dateTime = null;
            if (clockUse) {
                const span = clockUse.closest('span');
                if (span) { dateTime = span.textContent.trim(); }
            }

            // Title: try multiple strategies
            let title = null;
            const allSpans = el.querySelectorAll('span');

            // Strategy 1: span whose next sibling span contains a span[title] (description)
            for (const s of allSpans) {
                const next = s.nextElementSibling;
                if (next && next.tagName === 'SPAN' && next.querySelector('span[title]')) {
                    title = s.textContent.trim();
                    break;
                }
            }

            // Strategy 2: span that is a direct child of a div, where that div's parent
            // also contains the photo grid (MuiGridLegacy-container). Title is the first
            // text-only span in such a container.
            if (!title) {
                const photoGrids = el.querySelectorAll('.MuiGridLegacy-container');
                for (const grid of photoGrids) {
                    let parent = grid.parentElement;
                    while (parent && parent !== el) {
                        // Look for a sibling div above this that contains a title span
                        let prev = parent.previousElementSibling;
                        while (prev) {
                            const span = prev.querySelector(':scope > span');
                            if (span && span.textContent.trim() && !span.querySelector('svg')) {
                                title = span.textContent.trim();
                                break;
                            }
                            prev = prev.previousElementSibling;
                        }
                        if (title) break;
                        parent = parent.parentElement;
                    }
                    if (title) break;
                }
            }

            // Strategy 3: any span that is the first child of its parent div, comes after
            // the clock/header area, has text, and no children spans with title attribute
            if (!title && clockUse) {
                const headerSpan = clockUse.closest('span');
                let foundClock = false;
                for (const s of allSpans) {
                    if (s === headerSpan) { foundClock = true; continue; }
                    if (!foundClock) continue;
                    const text = s.textContent.trim();
                    // Skip empty, very short, avatar names, "Activity" labels
                    if (!text || text.length < 3) continue;
                    if (s.querySelector('svg, img')) continue;
                    // Skip if parent is an avatar group
                    if (s.closest('.MuiAvatarGroup-root, .MuiAvatar-root')) continue;
                    // Title should be a direct text span, not nested deeply
                    if (s.children.length === 0 || (s.children.length === 1 && s.children[0].tagName === 'SPAN')) {
                        title = text;
                        break;
                    }
                }
            }

            return { teacher, dateTime, title };
        }''')
    except Exception:
        return {"teacher": None, "dateTime": None, "title": None}


def get_card_date_text(card):
    """Get date text from a card (looks for clock icon span)."""
    try:
        return card.evaluate('''el => {
            const uses = el.querySelectorAll('use');
            for (const u of uses) {
                const href = u.getAttribute('xlink:href') || u.getAttribute('href') || '';
                if (href === '#twigsee-clock') {
                    const span = u.closest('span');
                    if (span) return span.textContent.trim();
                }
            }
            return null;
        }''')
    except Exception:
        return None


def card_has_photos(card):
    """Check if card has large photo thumbnails (bg-image divs > 100px)."""
    try:
        return card.evaluate('''el => {
            const divs = el.querySelectorAll('div');
            for (const d of divs) {
                const bg = window.getComputedStyle(d).backgroundImage;
                if (bg && bg !== 'none' && bg.includes('base64')) {
                    const r = d.getBoundingClientRect();
                    if (r.width > 100 && r.height > 100) return true;
                }
            }
            return false;
        }''')
    except Exception:
        return False


def click_first_photo(card, page):
    """Click the first photo thumbnail in a card. Returns True if modal opened.
    Detects photo grids by looking for either a +N badge or multiple large
    bg-image divs (to distinguish from a single avatar)."""
    try:
        clicked = card.evaluate('''el => {
            // Collect large bg-image divs (> 100x100)
            const photos = [];
            el.querySelectorAll('div').forEach(d => {
                const bg = window.getComputedStyle(d).backgroundImage;
                if (bg && bg !== 'none' && bg.includes('base64')) {
                    const r = d.getBoundingClientRect();
                    if (r.width > 100 && r.height > 100) {
                        photos.push({ el: d, area: r.width * r.height });
                    }
                }
            });

            // Also check <img> tags with base64 or large images
            el.querySelectorAll('img').forEach(img => {
                const r = img.getBoundingClientRect();
                if (r.width > 100 && r.height > 100) {
                    photos.push({ el: img, area: r.width * r.height });
                }
            });

            if (photos.length === 0) return false;

            // Click the largest one
            photos.sort((a, b) => b.area - a.area);
            photos[0].el.click();
            return true;
        }''')

        if not clicked:
            return False

        time.sleep(2)
        modal = page.query_selector('.MuiModal-root')
        return modal is not None and modal.is_visible()
    except Exception as e:
        log.warning("  Failed to click photo: %s", e)
        return False


def get_current_photo_filename(page):
    """Get the filename of the currently displayed photo from the modal info panel."""
    return page.evaluate('''() => {
        const modal = document.querySelector('.MuiModal-root');
        if (!modal) return null;
        const text = modal.textContent || '';
        const m = text.match(/Název média[:\\s]*([^\\n]+?)(?:\\s*Typ|\\s*Rozlišení|$)/);
        return m ? m[1].trim() : null;
    }''')


def has_right_arrow(page):
    """Check if the right arrow button exists and is not disabled."""
    return page.evaluate('''() => {
        const modal = document.querySelector('.MuiModal-root');
        if (!modal) return false;
        const uses = modal.querySelectorAll('use');
        for (const u of uses) {
            const href = u.getAttribute('xlink:href') || u.getAttribute('href') || '';
            if (href === '#twigsee-chevron-right') {
                const btn = u.closest('button');
                return btn && !btn.disabled;
            }
        }
        return false;
    }''')


def click_right_arrow(page):
    """Click the right arrow. Returns True if clicked."""
    return page.evaluate('''() => {
        const modal = document.querySelector('.MuiModal-root');
        if (!modal) return false;
        const uses = modal.querySelectorAll('use');
        for (const u of uses) {
            const href = u.getAttribute('xlink:href') || u.getAttribute('href') || '';
            if (href === '#twigsee-chevron-right') {
                const btn = u.closest('button');
                if (btn && !btn.disabled) { btn.click(); return true; }
            }
        }
        return false;
    }''')


def click_download_button(page):
    """Click the 'Stáhnout' (Download) button in the modal."""
    return page.evaluate('''() => {
        const modal = document.querySelector('.MuiModal-root');
        if (!modal) return false;
        // Find button or span containing "Stáhnout"
        const els = modal.querySelectorAll('button, span');
        for (const el of els) {
            if (el.textContent.trim().includes('Stáhnout')) {
                const btn = el.closest('button') || el;
                btn.click();
                return true;
            }
        }
        return false;
    }''')


def close_modal(page):
    """Close the photo modal."""
    closed = page.evaluate('''() => {
        const modal = document.querySelector('.MuiModal-root');
        if (!modal) return false;
        const uses = modal.querySelectorAll('use');
        for (const u of uses) {
            const href = u.getAttribute('xlink:href') || u.getAttribute('href') || '';
            if (href === '#twigsee-close') {
                const btn = u.closest('button');
                if (btn) { btn.click(); return true; }
            }
        }
        return false;
    }''')
    if not closed:
        page.keyboard.press("Escape")
    time.sleep(1)


# ---------------------------------------------------------------------------
# Rclone upload
# ---------------------------------------------------------------------------

def upload_post(post_dir: Path, remote: str, rclone_conf: str, attempts: int = 3) -> bool:
    """Upload a single post folder to Google Photos. Runs in a background thread.
    Returns True on success, False if all attempts failed."""
    album = post_dir.name
    for attempt in range(1, attempts + 1):
        log.info("  [upload] '%s' (attempt %d/%d)", album, attempt, attempts)
        result = subprocess.run(
            [
                "rclone", "copy",
                str(post_dir),
                f"{remote}/{album}",
                "--config", rclone_conf,
                "--log-level", "NOTICE",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            log.info("  [upload] Done '%s'", album)
            return True
        rclone_output = "\n".join(filter(None, [result.stderr.strip(), result.stdout.strip()]))
        log.warning("  [upload] Attempt %d/%d failed for '%s' (exit %d):\n%s",
                    attempt, attempts, album, result.returncode, rclone_output or "(no output)")
        if attempt < attempts:
            time.sleep(10 * attempt)  # 10s, 20s between retries
    log.error("  [upload] All %d attempts failed for '%s'", attempts, album)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(headless, download_dir, max_age_days, teacher_filter=None, rclone_conf=None, rclone_remote=None):
    from playwright.sync_api import sync_playwright

    load_env()
    email, password = get_credentials()
    download_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = download_dir / "downloaded.json"
    manifest = load_manifest(manifest_file)
    upload_enabled = bool(rclone_conf and rclone_remote)
    upload_futures: list[tuple[Path, Future]] = []
    uploaded: set[str] = set(manifest.get("uploaded_albums", []))
    executor = ThreadPoolExecutor(max_workers=4) if upload_enabled else None

    def flush_uploads():
        """Wait for all pending upload futures, retry failures, save manifest."""
        if executor is None:
            return
        failed_dirs: list[Path] = []
        if upload_futures:
            log.info("Waiting for %d pending upload(s)...", len(upload_futures))
            for post_dir, future in upload_futures:
                try:
                    if future.result():
                        uploaded.add(post_dir.name)
                    else:
                        failed_dirs.append(post_dir)
                except Exception as e:
                    log.error("  [upload] Unexpected error for '%s': %s", post_dir.name, e)
                    failed_dirs.append(post_dir)
        executor.shutdown(wait=False)
        upload_futures.clear()

        if failed_dirs:
            log.info("Final sweep: retrying %d failed upload(s)...", len(failed_dirs))
            for post_dir in failed_dirs:
                if upload_post(post_dir, rclone_remote, rclone_conf, attempts=3):
                    uploaded.add(post_dir.name)
                else:
                    log.error("  [upload] Permanently failed for '%s'", post_dir.name)
            log.info("Final sweep done.")

        manifest["uploaded_albums"] = sorted(uploaded)
        save_manifest(manifest_file, manifest)

        all_dirs = {p.name for p in download_dir.iterdir() if p.is_dir()}
        not_uploaded = all_dirs - uploaded
        if not_uploaded:
            log.warning("Albums NOT uploaded (%d): %s", len(not_uploaded), ", ".join(sorted(not_uploaded)))

    cutoff = (datetime.now() - timedelta(days=max_age_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    log.info("Looking for posts from %s onwards.", cutoff.strftime("%Y-%m-%d"))

    with sync_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": [
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-setuid-sandbox",
                "--disable-extensions",
                "--disable-background-networking",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--no-first-run",
                "--single-process",
                "--js-flags=--max-old-space-size=256",
            ],
        }
        chromium_path = os.environ.get("CHROMIUM_PATH")
        if chromium_path and Path(chromium_path).exists():
            launch_args["executable_path"] = chromium_path
        browser = p.chromium.launch(**launch_args)
        ctx_args = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }
        if STATE_FILE.exists():
            ctx_args["storage_state"] = str(STATE_FILE)

        context = browser.new_context(**ctx_args)
        page = context.new_page()

        # --- Login ---
        log.info("Navigating to Twigsee...")
        page.goto(TWIGSEE_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        # Always try to login — wait for the email input or the feed
        log.info("Current URL: %s", page.url)
        try:
            page.wait_for_selector('input[type="email"], input[name="email"], div[id^="timelineCard"]', timeout=10000)
        except Exception:
            pass

        feed_loaded = page.query_selector('div[id^="timelineCard"]')
        if not feed_loaded:
            log.info("Feed not visible yet, navigating to login...")
            page.goto(f"{TWIGSEE_URL}/auth/login", wait_until="networkidle", timeout=30000)
            time.sleep(5)
            log.info("After login redirect, URL: %s", page.url)

            # Wait for either the login form OR redirect back to timeline
            email_sel = 'input[type="email"], input[name="email"], input[autocomplete="email"], input[type="text"]'
            try:
                page.wait_for_selector(f'{email_sel}, div[id^="timelineCard"]', timeout=30000)
            except Exception:
                pass
            time.sleep(2)
            log.info("After wait, URL: %s", page.url)

            # Re-check: if we're on timeline or feed cards exist, we're logged in
            if "login" not in page.url and "auth" not in page.url or page.query_selector('div[id^="timelineCard"]'):
                log.info("Already logged in. Waiting for feed...")
                try:
                    page.wait_for_selector('div[id^="timelineCard"]', timeout=30000)
                except Exception:
                    log.info("Feed still not loaded, continuing anyway...")
                context.storage_state(path=str(STATE_FILE))
            else:
                log.info("Logging in...")
                if not page.query_selector(email_sel):
                    page.screenshot(path=str(DATA_DIR / "debug_login_page.png"))
                    Path(DATA_DIR / "debug_login_page.html").write_text(page.content()[:10000])
                    log.error("Login form never appeared. URL: %s", page.url)
                    browser.close()
                    return 0
                page.fill(email_sel, email)
                pwd_sel = 'input[type="password"], input[name="password"]'
                page.fill(pwd_sel, password)
                page.click('button[type="submit"]')
                page.wait_for_load_state("networkidle", timeout=15000)
                time.sleep(3)
                context.storage_state(path=str(STATE_FILE))
                log.info("Session saved.")
        else:
            log.info("Already logged in.")
            context.storage_state(path=str(STATE_FILE))

        # --- Dismiss popups ---
        dismiss_popups(page)
        time.sleep(1)

        # Wait for feed — try multiple times, dismiss popups between attempts
        feed_found = False
        for attempt in range(3):
            try:
                page.wait_for_selector('div[id^="timelineCard"]', timeout=15000)
                feed_found = True
                break
            except Exception:
                log.info("Feed not loaded yet (attempt %d/3), retrying...", attempt + 1)
                dismiss_popups(page)
                time.sleep(3)

        if not feed_found:
            log.error("No posts found. URL: %s", page.url)
            page.screenshot(path=str(DATA_DIR / "debug_no_posts.png"))
            browser.close()
            return 0

        # --- Process posts ---
        log.info("Scanning feed...")
        processed = set(manifest["processed_posts"])
        downloaded = set(manifest["downloaded_files"])
        total_new = 0
        posts_done = 0
        seen = set()

        # Re-queue any folders that were downloaded but never successfully uploaded
        if upload_enabled:
            pending_upload = [
                d for d in download_dir.iterdir()
                if d.is_dir() and d.name not in uploaded
            ]
            if pending_upload:
                log.info("Re-queuing %d folder(s) that were not previously uploaded.", len(pending_upload))
                for post_dir in pending_upload:
                    future = executor.submit(upload_post, post_dir, rclone_remote, rclone_conf)
                    upload_futures.append((post_dir, future))

        for scroll_round in range(MAX_SCROLL_ATTEMPTS):
            cards = page.query_selector_all('div[id^="timelineCard"]')
            new_cards = [c for c in cards if (c.get_attribute("id") or "") not in seen]

            if not new_cards:
                log.info("No new cards after scroll %d, scrolling more...", scroll_round + 1)
                page.evaluate("window.scrollBy(0, window.innerHeight)")
                time.sleep(SCROLL_WAIT_SEC)
                continue

            for card in new_cards:
                card_id = card.get_attribute("id") or ""
                seen.add(card_id)
                post_hash = make_hash(card_id)

                # Extract metadata
                meta = get_card_metadata(card)
                date_text = meta["dateTime"]
                post_date = parse_post_date(date_text)
                date_str = post_date.strftime("%Y-%m-%d") if post_date else "unknown"

                # Build folder name: date_teacher_title
                teacher = normalize_name(meta["teacher"] or "unknown")
                title = normalize_name(meta["title"] or "untitled")
                base_folder_name = f"{date_str}_{teacher}_{title}"
                # Avoid collisions: if folder already exists for a different post, add a counter
                folder_name = base_folder_name
                counter = 2
                while (download_dir / folder_name).exists() and post_hash not in processed:
                    folder_name = f"{base_folder_name}_{counter}"
                    counter += 1

                # Teacher filter?
                if teacher_filter and teacher_filter.lower() not in (meta["teacher"] or "").lower():
                    continue

                # Too old?
                if post_date and post_date < cutoff:
                    log.info("Post %s from %s is too old — done.", card_id, date_str)
                    manifest["processed_posts"] = list(processed)
                    manifest["downloaded_files"] = list(downloaded)
                    save_manifest(manifest_file, manifest)
                    context.storage_state(path=str(STATE_FILE))
                    browser.close()
                    flush_uploads()
                    log.info("Done! %d posts, %d new photos.", posts_done, total_new)
                    print(f"NEW_PHOTOS={total_new}")
                    return total_new

                # Already processed?
                if post_hash in processed:
                    log.info("Post %s already done — skip.", card_id)
                    continue

                log.info("=== %s ===", folder_name)

                # Scroll into view first, then try to click a photo
                try:
                    card.scroll_into_view_if_needed()
                    time.sleep(1)
                except Exception:
                    pass

                # Click the first photo to open the gallery modal (retry up to 3 times)
                gallery_opened = False
                for attempt in range(3):
                    if click_first_photo(card, page):
                        gallery_opened = True
                        break
                    log.warning("  Gallery open failed (attempt %d/3), retrying...", attempt + 1)
                    try:
                        card.scroll_into_view_if_needed()
                    except Exception:
                        pass
                    time.sleep(2)
                if not gallery_opened:
                    log.warning("  Could not open gallery after 3 attempts — will retry next run.")
                    continue

                # --- Walk through all photos ---
                photo_count = 0
                consecutive_fails = 0
                had_failures = False
                # Subfolder per post
                post_dir = download_dir / folder_name
                post_dir.mkdir(parents=True, exist_ok=True)

                while True:
                    time.sleep(1.5)

                    # Build filename: NNN.ext (just the index)
                    photo_index = photo_count + 1
                    base_fname = f"{photo_index:03d}"

                    # Check if file already exists on disk (any extension)
                    existing = list(post_dir.glob(f"{base_fname}.*"))
                    if existing:
                        log.info("  [skip] %s already on disk", existing[0].name)
                        photo_count += 1
                        consecutive_fails = 0
                    else:
                        # Download current photo (retry up to 3 times)
                        dl_ok = False
                        for dl_attempt in range(3):
                            try:
                                with page.expect_download(timeout=30000) as dl_info:
                                    if not click_download_button(page):
                                        log.warning("  No download button found.")
                                        break
                                dl = dl_info.value
                                orig = dl.suggested_filename or "photo.jpg"
                                ext = Path(orig).suffix or ".jpg"
                                fname = f"{base_fname}{ext}"
                                dest = post_dir / fname
                                dl.save_as(str(dest))
                                downloaded.add(make_hash(fname))
                                total_new += 1
                                photo_count += 1
                                consecutive_fails = 0
                                log.info("  [%d] %s", photo_count, fname)
                                dl_ok = True
                                break
                            except Exception as e:
                                log.warning("  Download failed (attempt %d/3): %s", dl_attempt + 1, e)
                                time.sleep(2)
                        if not dl_ok:
                            consecutive_fails += 1
                            had_failures = True
                            if consecutive_fails >= 2:
                                break

                    # Click right arrow to go to next photo
                    try:
                        if not has_right_arrow(page):
                            log.info("  No more photos. Total: %d", photo_count)
                            break
                        click_right_arrow(page)
                        time.sleep(1)
                    except Exception:
                        log.info("  Arrow click failed. Total: %d", photo_count)
                        break

                # Close modal
                try:
                    close_modal(page)
                except Exception:
                    log.warning("  Modal close failed, pressing Escape.")
                    try:
                        page.keyboard.press("Escape")
                        time.sleep(1)
                    except Exception:
                        pass

                if had_failures:
                    log.warning("  Post had download failures — will retry next run.")
                else:
                    processed.add(post_hash)
                    if executor is not None:
                        future = executor.submit(upload_post, post_dir, rclone_remote, rclone_conf)
                        upload_futures.append((post_dir, future))
                posts_done += 1
                manifest["processed_posts"] = list(processed)
                manifest["downloaded_files"] = list(downloaded)
                save_manifest(manifest_file, manifest)
                log.info("  Post done. %d new photos so far.", total_new)

            # Scroll the last card into view to trigger loading more
            cards = page.query_selector_all('div[id^="timelineCard"]')
            if cards:
                try:
                    cards[-1].scroll_into_view_if_needed()
                except Exception:
                    pass
            page.evaluate("window.scrollBy(0, window.innerHeight)")
            time.sleep(SCROLL_WAIT_SEC)

        # --- Save state ---
        context.storage_state(path=str(STATE_FILE))
        manifest["processed_posts"] = list(processed)
        manifest["downloaded_files"] = list(downloaded)
        save_manifest(manifest_file, manifest)
        browser.close()

    flush_uploads()

    log.info("Done! %d posts, %d new photos.", posts_done, total_new)
    print(f"NEW_PHOTOS={total_new}")
    return total_new


def main():
    parser = argparse.ArgumentParser(description="Twigsee Photo Downloader")
    parser.add_argument("--headless", dest="headless", action="store_true", default=True)
    parser.add_argument("--no-headless", dest="headless", action="store_false")
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--teacher", type=str, default=None,
                        help="Only download from this teacher (partial match)")
    parser.add_argument("--rclone-conf", type=str, default=None,
                        help="Path to rclone config file")
    parser.add_argument("--rclone-remote", type=str, default=None,
                        help="rclone remote destination (e.g. googlephotos:album)")
    args = parser.parse_args()
    run(headless=args.headless, download_dir=args.download_dir,
        max_age_days=args.max_age_days, teacher_filter=args.teacher,
        rclone_conf=args.rclone_conf, rclone_remote=args.rclone_remote)


if __name__ == "__main__":
    main()
