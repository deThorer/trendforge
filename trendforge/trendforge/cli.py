"""
Command line interface.

    # build a sample once and save it
    python -m trendforge.cli sample data/orders_10m.csv --rate 0.01 --strata region --out data/orders.sample

    # approximate query using the saved sample
    python -m trendforge.cli query data/orders_10m.csv "SELECT AVG(price) FROM orders GROUP BY region" \
        --sample data/orders.sample

    # exact query (full streaming scan)
    python -m trendforge.cli query data/orders_10m.csv "SELECT AVG(price) FROM orders GROUP BY region" --exact
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from trendforge.engine import Engine
from trendforge.estimators import MIN_RELIABLE_ROWS
from trendforge.query import QueryError


def main(argv=None):
    parser = argparse.ArgumentParser(prog="trendforge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="build a sample from a CSV file")
    s.add_argument("csv")
    s.add_argument("--rate", type=float, default=0.01)
    s.add_argument("--strata", nargs="*", help="column(s) to stratify on (default: uniform)")
    s.add_argument("--min-rows", type=int, default=2000)
    s.add_argument("--columns", nargs="*", help="only keep these columns")
    s.add_argument("--out", required=True)

    q = sub.add_parser("query", help="run a query")
    q.add_argument("csv")
    q.add_argument("sql")
    q.add_argument("--sample", help="saved sample file (approximate mode)")
    q.add_argument("--exact", action="store_true")
    q.add_argument("--chunksize", type=int, default=1_000_000)

    args = parser.parse_args(argv)
    try:
        if args.cmd == "sample":
            tf = Engine()
            tf.register("t", args.csv)
            strata = args.strata[0] if args.strata and len(args.strata) == 1 else args.strata
            sample = tf.build_sample("t", rate=args.rate, strata=strata or None,
                                     min_rows=args.min_rows, columns=args.columns)
            sample.save(args.out)
            print(sample.summary())
            print(f"saved -> {args.out}")
        else:
            tf = Engine(chunksize=args.chunksize)
            from trendforge.query import parse
            table = parse(args.sql).table
            tf.register(table, args.csv)
            if not args.exact:
                if not args.sample:
                    sys.exit("approximate mode needs --sample (or use --exact)")
                tf.load_sample(table, args.sample)
            r = tf.query(args.sql, exact=args.exact)
            with pd.option_context("display.width", 140, "display.max_columns", 20):
                print(r.table)
            print(f"\n{r.mode} | {r.seconds:.3f}s | {r.rows_scanned:,} rows scanned")
            if r.mode == "approx" and (r.table["sample_rows"] < MIN_RELIABLE_ROWS).any():
                print(f"warning: some groups have fewer than {MIN_RELIABLE_ROWS} sample rows; "
                      "their intervals are not reliable")
    except (QueryError, KeyError, FileNotFoundError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
