#!/usr/bin/env python3
"""
Solver for https://www.guessthepin.com/
- 4-digit PIN (0000-9999), POST to /prg.php
- Cloudflare bypass via cloudscraper
- Progress saved to progress.json for resume support
- PINs ordered: statistically rare (human-blind-spot) first
"""

import json
import time
import sys
from pathlib import Path

import cloudscraper
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

PROGRESS_FILE = Path("progress.json")
POST_URL = "https://www.guessthepin.com/prg.php"
HOME_URL = "https://www.guessthepin.com/"
DELAY_SECONDS = 0.3  # polite rate limit


console = Console()


# ---------------------------------------------------------------------------
# PIN ordering: try human "blind spots" first so we race ahead of manual
# guessers who cluster on memorable patterns.
# ---------------------------------------------------------------------------

# Patterns humans over-index on – try these LAST
COMMON_PINS = {
    # all-same digit
    *[f"{d}{d}{d}{d}" for d in "0123456789"],
    # sequential
    "1234", "2345", "3456", "4567", "5678", "6789",
    "0123", "9876", "8765", "7654", "6543", "5432", "4321",
    # keyboard patterns / pop-culture
    "1337", "0000", "1111", "0001", "0007", "2580", "1212",
    "6969", "4269", "0911", "9111", "2468", "1357",
    # years (common birth years / memorable years)
    *[str(y) for y in range(1950, 2026)],
}


def pin_ordering() -> list[str]:
    """All 10 000 PINs, uncommon ones first."""
    all_pins = [f"{i:04d}" for i in range(10000)]
    rare   = [p for p in all_pins if p not in COMMON_PINS]
    common = [p for p in all_pins if p in COMMON_PINS]
    return rare + common


# ---------------------------------------------------------------------------
# Progress persistence
# ---------------------------------------------------------------------------

def load_progress() -> tuple[set[str], str | None]:
    if PROGRESS_FILE.exists():
        data = json.loads(PROGRESS_FILE.read_text())
        return set(data.get("tried", [])), data.get("won")
    return set(), None


def save_progress(tried: set[str], won: str | None = None) -> None:
    PROGRESS_FILE.write_text(json.dumps({"tried": sorted(tried), "won": won}, indent=2))


# ---------------------------------------------------------------------------
# Win detection
# ---------------------------------------------------------------------------

WINNER_SIGNALS = [
    'id="winner"',
    "you got it",
    "you guessed",
    "winner",
    "startConfetti",  # JS called on correct guess
]


def is_winner(html: str) -> bool:
    lower = html.lower()
    # The page always embeds the confetti JS but only calls startConfetti() on win
    if "startconfetti()" in lower:
        return True
    # Server-rendered winner block has non-trivial content
    if 'id="winner"' in lower and len(html) > 5000:
        # extra check: winner section must have visible text
        idx = lower.find('id="winner"')
        snippet = lower[idx : idx + 500]
        if any(kw in snippet for kw in ["congrats", "you got", "correct", "<h2>", "<h3>"]):
            return True
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=5,
    )


def try_pin(scraper: cloudscraper.CloudScraper, pin: str) -> tuple[bool, int]:
    """Submit one guess. Returns (is_win, http_status)."""
    resp = scraper.post(
        POST_URL,
        data={"guess": pin},
        headers={"Referer": HOME_URL},
        allow_redirects=True,
        timeout=20,
    )
    return is_winner(resp.text), resp.status_code


def main() -> None:
    tried, won = load_progress()

    if won:
        console.print(f"[bold green]Already solved![/] PIN was [bold]{won}[/]")
        sys.exit(0)

    pins = pin_ordering()
    remaining = [p for p in pins if p not in tried]

    console.print(f"[bold]Guess-the-PIN solver[/]")
    console.print(f"Total: {len(pins)} | Already tried: {len(tried)} | Remaining: {len(remaining)}")
    console.print(f"Endpoint: {POST_URL}")
    console.print()

    scraper = make_scraper()

    # Warm up: fetch homepage to establish Cloudflare clearance cookie
    try:
        console.print("Establishing Cloudflare session…")
        scraper.get(HOME_URL, timeout=20)
        console.print("[green]Session established.[/]")
    except Exception as e:
        console.print(f"[yellow]Warm-up failed ({e}), proceeding anyway[/]")

    errors = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        task = progress.add_task("Guessing…", total=len(remaining))

        for idx, pin in enumerate(remaining):
            try:
                win, status = try_pin(scraper, pin)
                tried.add(pin)
                errors = 0  # reset on success

                if win:
                    save_progress(tried, pin)
                    progress.stop()
                    console.print(f"\n[bold green]🎉 WIN! The PIN is: {pin}[/]")
                    console.print(f"Solved after {len(tried)} total guesses.")
                    return

                progress.update(task, advance=1, description=f"Tried {pin} ({status})")

                # Persist every 200 guesses
                if idx % 200 == 0:
                    save_progress(tried)

                time.sleep(DELAY_SECONDS)

            except KeyboardInterrupt:
                save_progress(tried)
                console.print("\n[yellow]Interrupted – progress saved.[/]")
                sys.exit(1)

            except Exception as e:
                errors += 1
                console.print(f"[red]Error on {pin}: {e}[/]")
                if errors >= 5:
                    # Refresh scraper after repeated failures (new CF clearance)
                    console.print("[yellow]Too many errors – refreshing session…[/]")
                    scraper = make_scraper()
                    try:
                        scraper.get(HOME_URL, timeout=20)
                    except Exception:
                        pass
                    errors = 0
                time.sleep(2 * errors)
                continue

    save_progress(tried)
    console.print("[yellow]Finished all PINs without a win – the PIN likely changed mid-run.[/]")
    console.print("Delete progress.json and re-run to start a fresh sweep.")


if __name__ == "__main__":
    main()
