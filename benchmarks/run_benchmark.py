"""
Benchmark: exact streaming scan vs approximate answers from a sample.

For each sampling method and sample rate it measures
  - speed:   time of every query, exact vs approx, plus the one-off sample build time
  - accuracy: relative error of every result cell vs the exact answer
  - CI coverage: how often the true value falls inside the 95% interval
  - memory: peak resident memory of the process

    python benchmarks/run_benchmark.py --data data/orders_10m.csv
    python benchmarks/run_benchmark.py --data data/orders_100m.csv --rates 0.001 0.01

Results go to benchmarks/results/ (CSV + results.md + charts).
"""
import argparse
import json
import platform
import resource
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trendforge import Engine  # noqa: E402
from trendforge.estimators import MIN_RELIABLE_ROWS as RELIABLE  # noqa: E402

QUERIES = {
    "Q1": "SELECT AVG(price) FROM orders GROUP BY region",
    "Q2": "SELECT SUM(revenue), COUNT(*) FROM orders WHERE discount > 0 GROUP BY region",
    "Q3": "SELECT AVG(revenue) FROM orders WHERE category = 'C03' GROUP BY region",
    "Q4": "SELECT COUNT(*), AVG(quantity) FROM orders WHERE price > 100 GROUP BY category",
    "Q5": "SELECT SUM(revenue) FROM orders WHERE region = 'Oceania' AND quantity >= 3",
}
SAMPLE_COLUMNS = ["region", "category", "price", "quantity", "discount", "revenue"]

OUT = ROOT / "benchmarks/results"      # overridden by --out


def peak_rss_mb():
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 1024 if platform.system() != "Darwin" else rss / 1024 / 1024   # Linux: KB, macOS: bytes


def compare(exact: pd.DataFrame, approx: pd.DataFrame):
    """Relative error and CI hit for every (group, aggregate) cell of the exact answer."""
    rows = []
    for col in exact.columns:
        for group, true in exact[col].items():
            if group in approx.index and pd.notna(approx.loc[group, col]):
                est, ci = approx.loc[group, col], approx.loc[group, f"{col}_ci"]
                rel = abs(est - true) / abs(true) if true else 0.0
                rows.append({"group": group, "agg": col, "rel_error": rel,
                             "in_ci": abs(est - true) <= ci, "missing": False,
                             "sample_rows": approx.loc[group, "sample_rows"]})
            else:   # group not in the sample at all -> counts as 100% error
                rows.append({"group": group, "agg": col, "rel_error": 1.0, "in_ci": False,
                             "missing": True, "sample_rows": 0})
    return rows


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default=str(ROOT / "data/orders_10m.csv"))
    parser.add_argument("--rates", type=float, nargs="+", default=[0.001, 0.0025, 0.005, 0.01, 0.02, 0.05])
    parser.add_argument("--methods", nargs="+", default=["uniform", "stratified"])
    parser.add_argument("--min-rows", type=int, default=2000, help="minimum sample rows per stratum")
    parser.add_argument("--strata", nargs="+", default=["region"],
                        help="stratification column(s), e.g. --strata region category")
    parser.add_argument("--chunksize", type=int, default=1_000_000)
    parser.add_argument("--repeats", type=int, default=1, help="samples per setting (different seeds), averaged")
    parser.add_argument("--out", default=str(OUT), help="results folder")
    parser.add_argument("--report-only", action="store_true",
                        help="rebuild results.md and the chart from the CSVs/meta.json already in --out")
    args = parser.parse_args()
    OUT = Path(args.out)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.report_only:
        rebuild_report()
        return

    tf = Engine(chunksize=args.chunksize)
    tf.register("orders", args.data)
    size_gb = Path(args.data).stat().st_size / 1e9

    print(f"exact baseline on {args.data} ({size_gb:.2f} GB)")
    truth, exact_time, rows_total = {}, {}, 0
    for qid, sql in QUERIES.items():
        r = tf.query(sql, exact=True)
        truth[qid], exact_time[qid], rows_total = r.table, r.seconds, r.rows_scanned
        print(f"  {qid}: {r.seconds:6.2f}s")

    runs, cells = [], []
    for method in args.methods:
        for rate in args.rates:
            for seed in range(args.repeats):
                s = tf.build_sample("orders", rate=rate, columns=SAMPLE_COLUMNS, min_rows=args.min_rows,
                                    strata=args.strata if method == "stratified" else None, seed=seed)
                approx_total = 0.0
                for qid, sql in QUERIES.items():
                    r = tf.query(sql)
                    approx_total += r.seconds
                    for c in compare(truth[qid], r.table):
                        cells.append({"method": method, "rate": rate, "seed": seed, "query": qid, **c})
                    runs.append({"method": method, "rate": rate, "seed": seed, "query": qid,
                                 "approx_s": r.seconds, "exact_s": exact_time[qid]})
                print(f"  {method:10s} rate={rate:<7} seed={seed} sample={len(s.data):>10,} rows  "
                      f"build={s.build_seconds:6.2f}s  queries={approx_total:.3f}s  "
                      f"sample mem={s.data.memory_usage(deep=True).sum() / 1e6:.0f} MB")
                runs.append({"method": method, "rate": rate, "seed": seed, "query": "_build",
                             "approx_s": s.build_seconds, "exact_s": 0.0, "sample_rows": len(s.data)})
                del s
                tf.samples.clear()

    runs, cells = pd.DataFrame(runs), pd.DataFrame(cells)
    runs.to_csv(OUT / "timings.csv", index=False)
    cells.to_csv(OUT / "errors.csv", index=False)

    # ---- summary per (method, rate)
    exact_workload = sum(exact_time.values())
    summary = []
    for (method, rate), g in cells.groupby(["method", "rate"]):
        t = runs[(runs.method == method) & (runs.rate == rate)]
        n_rep = t["seed"].nunique()
        build = t.loc[t["query"] == "_build", "approx_s"].mean()
        q_time = t.loc[t["query"] != "_build", "approx_s"].sum() / n_rep       # per run of the 5 queries
        summary.append({
            "method": method, "rate": rate,
            "sample_rows": int(t.loc[t["query"] == "_build", "sample_rows"].mean()),
            "reliable_cells_pct": 100 * (g["sample_rows"] >= RELIABLE).mean(),
            "reliable_mean_error_pct": 100 * g.loc[g["sample_rows"] >= RELIABLE, "rel_error"].mean(),
            "mean_rel_error_pct": 100 * g["rel_error"].mean(),
            "median_rel_error_pct": 100 * g["rel_error"].median(),
            "max_rel_error_pct": 100 * g["rel_error"].max(),
            "missing_groups": int(g["missing"].sum()),
            "ci_coverage_pct": 100 * g["in_ci"].mean(),
            "build_s": build, "approx_queries_s": q_time,
            "query_speedup_x": exact_workload / q_time,                       # sample already built
            "workload_speedup_x": exact_workload / (build + q_time),          # including the build pass
        })
    summary = pd.DataFrame(summary)
    num = summary.columns.drop(["method", "rate"])
    summary[num] = summary[num].round(3)
    summary.to_csv(OUT / "summary.csv", index=False)

    reliable = cells[cells["sample_rows"] >= RELIABLE]
    per_query = (100 * reliable.pivot_table(index=["method", "query"], columns="rate",
                                            values="rel_error", aggfunc="mean")).round(2)
    per_query.to_csv(OUT / "error_by_query.csv")

    meta = {"data": Path(args.data).name, "rows": rows_total, "size_gb": size_gb,
            "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
            "chunksize": args.chunksize, "strata": args.strata, "min_rows": args.min_rows,
            "repeats": args.repeats, "peak_mb": round(peak_rss_mb()), "exact_s": exact_time}
    (OUT / "meta.json").write_text(json.dumps(meta, indent=2))
    write_report(summary, per_query, meta)
    make_charts(summary)
    print(summary.to_string(index=False))
    print(f"\npeak memory: {peak_rss_mb():.0f} MB   -> {OUT / 'results.md'}")


def write_report(summary, per_query, meta):
    exact_time = meta["exact_s"]
    lines = [
        "# Benchmark results", "",
        f"- Data: `{meta['data']}`: {meta['rows']:,} rows, {meta['size_gb']:.2f} GB",
        f"- Machine: {meta['machine']}, chunk size {meta['chunksize']:,} rows",
        f"- Stratified sampling on: {', '.join(meta['strata'])} (min {meta['min_rows']:,} rows per stratum)",
        f"- Each setting averaged over {meta['repeats']} sample(s) with different random seeds",
        f"- Peak process memory during the whole benchmark: **{meta['peak_mb']:,} MB**",
        f"- Exact workload (5 queries, each a full streaming scan): **{sum(exact_time.values()):.1f}s**",
        "", "## Queries", "",
    ] + [f"- **{k}**: `{v}` (exact: {exact_time[k]:.2f}s)" for k, v in QUERIES.items()] + [
        "", "## Accuracy and speed by sample rate", "",
        "- **error % (reliable)**: mean relative error over results backed by >= "
        f"{RELIABLE} sample rows (the engine flags the others)",
        "- **error % (all)**: every result cell; groups missing from the sample count as 100%",
        "- **query speedup**: exact workload time / approximate query time, sample already built",
        "- **workload speedup**: same, but including the one-off sample build pass", "",
        _table(summary[SHOWN].rename(columns=SHORT_NAMES), index=False),
        "", "![error vs rate](error_vs_rate.png)", "",
        f"## Mean relative error (%) by query, reliable results only (>= {RELIABLE} sample rows)", "",
        "Columns are sample rates.", "",
        _table(per_query), "",
        "Full numbers (median/max error, missing groups, etc.) are in `summary.csv`.", "",
    ]
    (OUT / "results.md").write_text("\n".join(lines) + "\n")


def rebuild_report():
    summary = pd.read_csv(OUT / "summary.csv")
    per_query = pd.read_csv(OUT / "error_by_query.csv", index_col=[0, 1])
    meta = json.loads((OUT / "meta.json").read_text())
    write_report(summary, per_query, meta)
    make_charts(summary)
    print(f"rebuilt {OUT / 'results.md'}")


SHOWN = ["method", "rate", "sample_rows", "reliable_mean_error_pct", "mean_rel_error_pct",
         "ci_coverage_pct", "build_s", "approx_queries_s", "query_speedup_x", "workload_speedup_x"]
SHORT_NAMES = {"sample_rows": "sample rows", "reliable_mean_error_pct": "error % (reliable)",
               "mean_rel_error_pct": "error % (all)", "ci_coverage_pct": "CI coverage %",
               "build_s": "build s", "approx_queries_s": "5 queries s",
               "query_speedup_x": "query speedup", "workload_speedup_x": "workload speedup"}


def _table(df, index=True):
    """Markdown table without needing the optional 'tabulate' package."""
    df = df.reset_index() if index else df
    head = "| " + " | ".join(map(str, df.columns)) + " |"
    sep = "|" + "---|" * len(df.columns)
    def fmt(v):
        if isinstance(v, (int, np.integer)) and abs(v) >= 10_000:
            return f"{v:,}"
        if isinstance(v, float) and v >= 1:
            return f"{v:,.2f}"
        return str(v)
    body = ["| " + " | ".join(fmt(v) for v in row) + " |" for row in df.itertuples(index=False)]
    return "\n".join([head, sep] + body)


def make_charts(summary):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9.5, 4))
    for method, color in [("uniform", "#9bb7d4"), ("stratified", "#2f5d8a")]:
        g = summary[summary.method == method]
        if len(g):
            ax.plot(100 * g["rate"], g["reliable_mean_error_pct"], marker="o", color=color,
                    label=f"{method}: reliable results (>= {RELIABLE} sample rows)")
            ax.plot(100 * g["rate"], g["mean_rel_error_pct"], marker="o", ls="--", color=color,
                    label=f"{method}: all results")
    from matplotlib.ticker import FuncFormatter
    ax.set_xscale("log")
    ax.set_yscale("log")
    plain = FuncFormatter(lambda v, _: f"{v:g}")
    ax.xaxis.set_major_formatter(plain)
    ax.yaxis.set_major_formatter(plain)
    ax.yaxis.set_minor_formatter(plain)
    ax.set_xlabel("sample rate (%)")
    ax.set_ylabel("mean relative error (%)")
    ax.set_title("Query error vs sample rate (5-query suite)")
    ax.legend(frameon=False, fontsize=8, loc="upper left", bbox_to_anchor=(1.0, 1.0))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "error_vs_rate.png", dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    main()
