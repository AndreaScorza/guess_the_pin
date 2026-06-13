# guess-the-pin solver

Automated solver for [guessthepin.com](https://www.guessthepin.com/), a game where you guess a secret 4-digit PIN.

## Run the solver

```bash
# defaults: 4 processes × 50 threads
uv run python solver_v3.py

# tune concurrency
uv run python solver_v3.py --processes 6 --workers 80
```

Each win is appended to `results.json`.

## Benchmark

Runs the solver N times and prints mean, median, min, max, and stdev for solve time, guesses, and req/s.

```bash
# 10 runs with solver defaults
uv run python benchmark.py solver_v3 --runs 10

# 10 runs with custom solver args (pass after --)
uv run python benchmark.py solver_v3 --runs 10 -- --processes 6 --workers 80

# add a 30s cooldown between runs to avoid rate limiting
uv run python benchmark.py solver_v3 --runs 10 --delay 30 -- --processes 6 --workers 80

# stats over all results in results.json (no new runs)
uv run python benchmark.py --stats

# stats over last 10 entries
uv run python benchmark.py --stats --last 10
```
