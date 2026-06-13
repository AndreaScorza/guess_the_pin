#!/usr/bin/env python3
"""
Solver for https://www.guessthepin.com/
- 4-digit PIN (0000-9999), POST to /prg.php
- Cloudflare bypass via cloudscraper
- N processes × M threads: processes bypass GIL for cloudscraper CPU work
- Win flag shared via multiprocessing.Value; progress via multiprocessing.Queue
"""

import argparse
import json
import multiprocessing as mp
import threading
import time
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import cloudscraper
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn

POST_URL = "https://www.guessthepin.com/prg.php"
HOME_URL = "https://www.guessthepin.com/"
DEFAULT_PROCESSES = 4
DEFAULT_WORKERS = 50
RESULTS_FILE = "results.json"

console = Console()


def save_result(pin: str, guesses: int, elapsed: float, rate: float, processes: int, workers: int) -> None:
    try:
        with open(RESULTS_FILE) as f:
            records = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        records = []

    records.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pin": pin,
        "guesses": guesses,
        "elapsed_s": round(elapsed, 2),
        "req_per_s": round(rate, 1),
        "processes": processes,
        "workers": workers,
    })

    with open(RESULTS_FILE, "w") as f:
        json.dump(records, f, indent=2)


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


def get_scraper(tl: threading.local) -> cloudscraper.CloudScraper:
    if not hasattr(tl, "scraper"):
        s = make_scraper()
        try:
            s.get(HOME_URL, timeout=10)
        except Exception:
            pass
        tl.scraper = s
    return tl.scraper


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
# Process worker (runs inside each child process)
# ---------------------------------------------------------------------------

def process_worker(pins_chunk: list, win_flag, result_queue: mp.Queue, n_workers: int) -> None:
    tl = threading.local()
    local_win = threading.Event()

    def task(pin: str) -> tuple[bool, str, int]:
        if win_flag.value or local_win.is_set():
            return False, pin, -1
        scraper = get_scraper(tl)
        for attempt in range(2):
            try:
                win, status = try_pin(scraper, pin)
                return win, pin, status
            except Exception:
                tl.scraper = make_scraper()
                try:
                    tl.scraper.get(HOME_URL, timeout=10)
                except Exception:
                    pass
                scraper = tl.scraper
                if attempt == 1:
                    return False, pin, -1

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        futures = {ex.submit(task, p): p for p in pins_chunk}
        try:
            for future in as_completed(futures):
                try:
                    result = future.result()
                    if result is None:
                        continue
                    win, pin, status = result
                except Exception as e:
                    result_queue.put(("error", futures[future], str(e)))
                    continue

                if status == -1:
                    result_queue.put(("skip", pin))
                    continue

                if win:
                    win_flag.value = 1
                    local_win.set()
                    result_queue.put(("win", pin))
                    ex.shutdown(wait=False, cancel_futures=True)
                    return

                result_queue.put(("done", pin, status))
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Guess-the-PIN solver")
    parser.add_argument("--processes", type=int, default=DEFAULT_PROCESSES,
                        help=f"number of processes (default: {DEFAULT_PROCESSES})")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help=f"threads per process (default: {DEFAULT_WORKERS})")
    args = parser.parse_args()

    pins = [f"{i:04d}" for i in range(9999, -1, -1)]
    n = args.processes
    chunk_size = (len(pins) + n - 1) // n
    chunks = [pins[i * chunk_size : (i + 1) * chunk_size] for i in range(n)]

    win_flag = mp.Value("b", 0)
    result_queue: mp.Queue = mp.Queue()

    total_workers = n * args.workers
    console.print(
        f"[bold]Guess-the-PIN solver[/] "
        f"(processes: {n}, workers/process: {args.workers}, total: {total_workers}, cores: {mp.cpu_count()})"
    )
    console.print(f"Endpoint: {POST_URL}")
    console.print()

    processes = [
        mp.Process(
            target=process_worker,
            args=(chunk, win_flag, result_queue, args.workers),
            daemon=True,
        )
        for chunk in chunks
    ]
    for p in processes:
        p.start()

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
        task_id = progress.add_task("Guessing…", total=len(pins))

        try:
            while completed < len(pins):
                if not any(p.is_alive() for p in processes) and result_queue.empty():
                    break

                try:
                    item = result_queue.get(timeout=0.5)
                except Exception:
                    continue

                kind = item[0]

                if kind == "win":
                    _, pin = item
                    win_flag.value = 1
                    progress.stop()
                    for p in processes:
                        p.terminate()
                    elapsed = time.monotonic() - start
                    rate = completed / max(elapsed, 0.001)
                    save_result(pin, completed, elapsed, rate, n, args.workers)
                    console.print(f"\n[bold green]WIN! The PIN is: {pin}[/]")
                    console.print(f"Solved after {completed} guesses in {elapsed:.1f}s ({rate:.0f} req/s).")
                    console.print(f"Result saved to [dim]{RESULTS_FILE}[/]")
                    return

                elif kind == "done":
                    _, pin, status = item
                    completed += 1
                    rate = completed / max(time.monotonic() - start, 0.001)
                    progress.update(
                        task_id,
                        advance=1,
                        description=f"{rate:.0f} req/s | last {pin} ({status})",
                    )

                elif kind == "skip":
                    completed += 1
                    progress.update(task_id, advance=1)

                elif kind == "error":
                    _, pin, err = item
                    progress.log(f"[red]Error on {pin}: {err}[/]")

        except KeyboardInterrupt:
            win_flag.value = 1
            for p in processes:
                p.terminate()
            console.print("\n[yellow]Interrupted.[/]")
            sys.exit(1)

    for p in processes:
        p.join(timeout=2)

    console.print("[yellow]Finished all PINs without a win – the PIN likely changed mid-run.[/]")


if __name__ == "__main__":
    main()
