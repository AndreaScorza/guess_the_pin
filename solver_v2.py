#!/usr/bin/env python3
"""
Solver for https://www.guessthepin.com/
- 4-digit PIN (0000-9999), POST to /prg.php
- Cloudflare bypass via cloudscraper
- Concurrent requests via ThreadPoolExecutor (default 100 workers, ~90 req/s sweet spot)
- No progress persistence — full sweep completes in ~2 min
"""

import argparse
import threading
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

POST_URL = "https://www.guessthepin.com/prg.php"
HOME_URL = "https://www.guessthepin.com/"
DEFAULT_WORKERS = 100

console = Console()
thread_local = threading.local()
win_event = threading.Event()


# ---------------------------------------------------------------------------
# Win detection
# ---------------------------------------------------------------------------

def is_winner(html: str) -> bool:
    lower = html.lower()
    if "startconfetti()" in lower:
        return True
    if 'id="winner"' in lower and len(html) > 5000:
        idx = lower.find('id="winner"')
        snippet = lower[idx : idx + 500]
        if any(kw in snippet for kw in ["congrats", "you got", "correct", "<h2>", "<h3>"]):
            return True
    return False


# ---------------------------------------------------------------------------
# Scraper (one per thread via thread-local storage)
# ---------------------------------------------------------------------------

def make_scraper() -> cloudscraper.CloudScraper:
    return cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False},
        delay=5,
    )


def get_scraper() -> cloudscraper.CloudScraper:
    if not hasattr(thread_local, "scraper"):
        s = make_scraper()
        try:
            s.get(HOME_URL, timeout=10)
        except Exception:
            pass
        thread_local.scraper = s
    return thread_local.scraper


def try_pin(scraper: cloudscraper.CloudScraper, pin: str) -> tuple[bool, int]:
    resp = scraper.post(
        POST_URL,
        data={"guess": pin},
        headers={"Referer": HOME_URL},
        allow_redirects=True,
        timeout=10,
    )
    return is_winner(resp.text), resp.status_code


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

def worker_task(pin: str) -> tuple[bool, str, int]:
    """Try one PIN. Returns (is_win, pin, http_status) or status=-1 if skipped."""
    if win_event.is_set():
        return False, pin, -1
    scraper = get_scraper()
    for attempt in range(2):
        try:
            win, status = try_pin(scraper, pin)
            return win, pin, status
        except Exception:
            thread_local.scraper = make_scraper()
            try:
                thread_local.scraper.get(HOME_URL, timeout=10)
            except Exception:
                pass
            scraper = thread_local.scraper
            if attempt == 1:
                raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Guess-the-PIN solver")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"concurrent workers (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    pins = [f"{i:04d}" for i in range(10000)]

    console.print(f"[bold]Guess-the-PIN solver[/] (workers: {args.workers})")
    console.print(f"Endpoint: {POST_URL}")
    console.print()

    completed = 0
    start = time.monotonic()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        refresh_per_second=4,
    ) as progress:
        task = progress.add_task("Guessing…", total=len(pins))

        executor = ThreadPoolExecutor(max_workers=args.workers)
        try:
            futures = {executor.submit(worker_task, pin): pin for pin in pins}

            for future in as_completed(futures):
                try:
                    win, pin, status = future.result()
                except Exception as e:
                    pin = futures[future]
                    progress.log(f"[red]Error on {pin}: {e}[/]")
                    continue

                if status == -1:
                    progress.update(task, advance=1)
                    continue

                completed += 1

                if win:
                    win_event.set()
                    progress.stop()
                    console.print(f"\n[bold green]WIN! The PIN is: {pin}[/]")
                    console.print(f"Solved after {completed} guesses.")
                    executor.shutdown(wait=False, cancel_futures=True)
                    return

                rate = completed / max(time.monotonic() - start, 0.001)
                progress.update(task, advance=1, description=f"{rate:.0f} req/s | last {pin} ({status})")

        except KeyboardInterrupt:
            win_event.set()
            executor.shutdown(wait=False, cancel_futures=True)
            console.print("\n[yellow]Interrupted.[/]")
            sys.exit(1)

    console.print("[yellow]Finished all PINs without a win – the PIN likely changed mid-run.[/]")


if __name__ == "__main__":
    main()
