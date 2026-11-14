#!/usr/bin/env python3
"""Measure long-session bridge updates.

Build one session containing N events.  Deleting the middle event is the
worst split case and reinserting it is the inverse bridge merge.  Both rely
on treap navigation and subtree aggregates, never on scanning all events or
sessions.
"""

import argparse
import math
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sessions import Store


def build_long_session(size):
    store = Store(5)
    for i in range(size):
        # Every adjacent time difference is exactly five.
        store.put(f"e-{i:010d}", "long", i * 5, i)
    sessions = store.read("long")
    if len(sessions) != 1:
        raise AssertionError(f"expected one session, got {len(sessions)}")
    session = sessions[0]
    if session["count"] != size or session["sum"] != size * (size - 1) // 2:
        raise AssertionError("incorrect long-session aggregate")
    return store


def measure(size, rounds):
    store = build_long_session(size)
    middle = size // 2
    middle_id = f"e-{middle:010d}"

    # Untimed assertions also demonstrate the required bridge split.
    deleted = store.delete(middle_id)
    if len(deleted["removed"]) != 1 or len(deleted["added"]) != 2:
        raise AssertionError("bridge deletion did not split one session into two")
    if len(store.read("long")) != 2:
        raise AssertionError("expected exactly two sessions after split")

    reinserted = store.put(middle_id, "long", middle * 5, middle)
    if len(reinserted["removed"]) != 2 or len(reinserted["added"]) != 1:
        raise AssertionError("bridge insertion did not merge two sessions")
    if store.read("long")[0]["count"] != size:
        raise AssertionError("expected one restored long session")

    delete_times = []
    reinsert_times = []
    for _ in range(rounds):
        start = time.perf_counter_ns()
        store.delete(middle_id)
        delete_times.append(time.perf_counter_ns() - start)

        start = time.perf_counter_ns()
        store.put(middle_id, "long", middle * 5, middle)
        reinsert_times.append(time.perf_counter_ns() - start)

    tracemalloc.start()
    measured_store = build_long_session(size)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del measured_store

    return {
        "size": size,
        "delete_median_us": statistics.median(delete_times) / 1_000,
        "reinsert_median_us": statistics.median(reinsert_times) / 1_000,
        "current_bytes_per_event": current / size,
        "peak_bytes_per_event": peak / size,
    }


def log_log_slope(rows, field):
    xs = [math.log(row["size"]) for row in rows]
    ys = [math.log(row[field]) for row in rows]
    x_mean = statistics.mean(xs)
    y_mean = statistics.mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    denominator = sum((x - x_mean) ** 2 for x in xs)
    return numerator / denominator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=[1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072],
    )
    parser.add_argument("--rounds", type=int, default=31)
    args = parser.parse_args()

    if args.rounds < 1:
        raise SystemExit("--rounds must be positive")

    rows = [measure(size, args.rounds) for size in args.sizes]
    headers = (
        "N",
        "split median µs",
        "merge median µs",
        "B/event current",
        "B/event peak",
    )
    print("\t".join(headers))
    previous = None
    for row in rows:
        if previous is not None and row["size"] == previous["size"] * 2:
            print(
                "doubling ratios: split "
                f"{row['delete_median_us'] / previous['delete_median_us']:.2f}, "
                "merge "
                f"{row['reinsert_median_us'] / previous['reinsert_median_us']:.2f}",
                file=sys.stderr,
            )
        print(
            f"{row['size']}\t"
            f"{row['delete_median_us']:.3f}\t"
            f"{row['reinsert_median_us']:.3f}\t"
            f"{row['current_bytes_per_event']:.1f}\t"
            f"{row['peak_bytes_per_event']:.1f}"
        )
        previous = row

    print(
        "log-log median-time slopes: split "
        f"{log_log_slope(rows, 'delete_median_us'):.3f}, merge "
        f"{log_log_slope(rows, 'reinsert_median_us'):.3f}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
