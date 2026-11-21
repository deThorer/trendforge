"""
Generate a synthetic e-commerce orders CSV of any size, written in chunks so memory
stays flat (100M rows ~ 5.5 GB on disk).

    python scripts/generate_data.py --rows 10_000_000 --out data/orders_10m.csv
    python scripts/generate_data.py --rows 100_000_000 --out data/orders_100m.csv

Columns: order_id, order_date, region, category, price, quantity, discount, revenue
Distributions are skewed on purpose (a few big regions and categories, a long tail,
log-normal prices), because that's where approximate queries get hard.
"""
import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

REGIONS = ["North America", "Europe", "East Asia", "South Asia", "Latin America",
           "Middle East", "Africa", "Oceania"]
REGION_P = [0.30, 0.25, 0.15, 0.12, 0.08, 0.06, 0.035, 0.005]          # Oceania is rare
CATEGORIES = [f"C{i:02d}" for i in range(1, 51)]
CAT_P = 1 / np.arange(1, 51) ** 1.1                                     # Zipf-like
CAT_P = CAT_P / CAT_P.sum()
DATES = pd.date_range("2023-01-01", "2024-12-31").strftime("%Y-%m-%d").to_numpy()

# each category has its own typical price; each region a small price multiplier
CAT_PRICE = np.random.default_rng(123).lognormal(3.5, 0.8, len(CATEGORIES))
REGION_MULT = np.array([1.10, 1.05, 0.95, 0.70, 0.80, 1.00, 0.75, 1.20])


def make_chunk(rng, start_id, n):
    reg = rng.choice(len(REGIONS), n, p=REGION_P)
    cat = rng.choice(len(CATEGORIES), n, p=CAT_P)
    price = CAT_PRICE[cat] * REGION_MULT[reg] * rng.lognormal(0, 0.5, n)
    qty = rng.geometric(0.45, n)
    discount = np.where(rng.random(n) < 0.3, rng.choice([0.05, 0.1, 0.2, 0.3], n), 0.0)
    return pd.DataFrame({
        "order_id": np.arange(start_id, start_id + n),
        "order_date": DATES[rng.integers(0, len(DATES), n)],
        "region": np.array(REGIONS)[reg],
        "category": np.array(CATEGORIES)[cat],
        "price": price.round(2),
        "quantity": qty,
        "discount": discount,
        "revenue": (price * qty * (1 - discount)).round(2),
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=lambda s: int(s.replace("_", "")), default=1_000_000)
    parser.add_argument("--out", default="data/orders.csv")
    parser.add_argument("--chunk", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(args.seed)
    start = time.perf_counter()
    written = 0
    with open(out, "w", newline="") as f:
        while written < args.rows:
            n = min(args.chunk, args.rows - written)
            make_chunk(rng, written, n).to_csv(f, index=False, header=(written == 0))
            written += n
            print(f"\r{written:,} / {args.rows:,} rows", end="", flush=True)
    size_gb = out.stat().st_size / 1e9
    print(f"\nwrote {out} ({size_gb:.2f} GB) in {time.perf_counter() - start:.0f}s")


if __name__ == "__main__":
    main()
