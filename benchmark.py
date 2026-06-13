#!/usr/bin/env python3
"""
Benchmark runner — runs a solver N times and reports stats.

Usage:
  python benchmark.py solver_v3 --runs 10
  python benchmark.py solver_v3 --runs 10 -- --processes 6 --workers 80
  python benchmark.py --stats              # stats over all results.json entries
  python benchmark.py --stats --last 10   # stats over last 10 entries
"""

import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

RESULTS_FILE = "results.json"
console = Console()


def load_results() -> list:
    try:
        with open(RESULTS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def fmt(value: float, decimals: int = 1) -> str:
    return f"{value:.{decimals}f}"


def print_stats(results: list) -> None:
    if not results:
        console.print("[red]No results to show.[/]")
        return

    elapsed = [r["elapsed_s"] for r in results]
    guesses = [r["guesses"] for r in results]
    rates   = [r["req_per_s"] for r in results]
    pins    = [r["pin"] for r in results]

    table = Table(title=f"\nStats over {len(results)} run(s)", show_lines=True)
    table.add_column("Metric", style="bold")
    table.add_column("Mean",   justify="right")
    table.add_column("Median", justify="right")
    table.add_column("Min",    justify="right")
    table.add_column("Max",    justify="right")
    table.add_column("Stdev",  justify="right")

    def row(label, values, unit="", decimals=1):
        u = f" {unit}" if unit else ""
        table.add_row(
            label,
            fmt(statistics.mean(values),   decimals) + u,
            fmt(statistics.median(values), decimals) + u,
            fmt(min(values),               decimals) + u,
            fmt(max(values),               decimals) + u,
            fmt(statistics.stdev(values) if len(values) > 1 else 0.0, decimals) + u,
        )

    row("Time to solve", elapsed, "s")
    row("Guesses",       guesses, "", decimals=0)
    row("Req/s",         rates,   "req/s")

    console.print(table)
    console.print(f"PINs found: {', '.join(pins)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark a solver script",
        epilog="Pass solver args after --:  benchmark.py solver_v3 --runs 5 -- --processes 4 --workers 50",
    )
    parser.add_argument("solver", nargs="?", help="solver script name (e.g. solver_v3)")
    parser.add_argument("--runs", type=int, default=10, help="number of runs (default: 10)")
    parser.add_argument("--delay", type=float, default=0, metavar="SEC", help="seconds to wait between runs (default: 0)")
    parser.add_argument("--stats", action="store_true", help="show stats from results.json without running")
    parser.add_argument("--last", type=int, default=None, metavar="N", help="limit stats to last N results")
    args, solver_args = parser.parse_known_args()
    if solver_args and solver_args[0] == "--":
        solver_args = solver_args[1:]

    if args.stats:
        results = load_results()
        if args.last:
            results = results[-args.last:]
        print_stats(results)
        return

    if not args.solver:
        parser.error("solver name is required unless --stats is used")

    solver_path = Path(args.solver)
    if solver_path.suffix != ".py":
        solver_path = solver_path.with_suffix(".py")

    if not solver_path.exists():
        console.print(f"[red]File not found: {solver_path}[/]")
        sys.exit(1)

    before = len(load_results())

    console.print(f"[bold]Benchmark[/]: {solver_path}  |  runs: {args.runs}")
    if solver_args:
        console.print(f"Solver args: {' '.join(solver_args)}")
    console.print()

    for i in range(args.runs):
        console.rule(f"Run {i + 1}/{args.runs}")
        result = subprocess.run(
            [sys.executable, str(solver_path)] + solver_args,
            check=False,
        )
        if result.returncode not in (0, 1):
            console.print(f"[yellow]Run {i + 1} exited with code {result.returncode}[/]")
        if args.delay and i < args.runs - 1:
            console.print(f"[dim]Waiting {args.delay}s…[/]")
            time.sleep(args.delay)

    new_results = load_results()[before:]

    if not new_results:
        console.print("[red]No new results recorded — did the solver find any PINs?[/]")
        return

    print_stats(new_results)


if __name__ == "__main__":
    main()
