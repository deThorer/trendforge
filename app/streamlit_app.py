"""
TrendForge web UI.

    streamlit run app/streamlit_app.py

Sidebar: choose a CSV, build a sample (method, rate).
Main: write a query, run it approximately and/or exactly, compare.
"""
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trendforge import Engine, QueryError  # noqa: E402
from trendforge.estimators import MIN_RELIABLE_ROWS  # noqa: E402
from trendforge.query import parse  # noqa: E402

TABLE = "orders"
EXAMPLES = [
    "SELECT AVG(price), COUNT(*) FROM orders GROUP BY region",
    "SELECT SUM(revenue) FROM orders WHERE discount > 0 GROUP BY region",
    "SELECT AVG(revenue) FROM orders WHERE category = 'C03' GROUP BY region",
    "SELECT COUNT(*), AVG(quantity) FROM orders WHERE price > 100 GROUP BY category",
    "SELECT SUM(revenue) FROM orders WHERE region = 'Oceania' AND quantity >= 3",
]

st.set_page_config(page_title="TrendForge", layout="wide")


@st.cache_resource
def get_engine():
    # one engine per server process, so a built sample survives reruns of the script
    return Engine()


engine = get_engine()

# ---------------------------------------------------------------- sidebar
st.sidebar.title("TrendForge")
st.sidebar.caption("Approximate queries on big CSV files")

csv_files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "data").glob("*.csv"))
if not csv_files:
    st.sidebar.error("No CSV in data/. Run: python scripts/generate_data.py --rows 10_000_000 "
                     "--out data/orders_10m.csv")
    st.stop()

csv = st.sidebar.selectbox("Data file", csv_files)
size_gb = (ROOT / csv).stat().st_size / 1e9
st.sidebar.write(f"{size_gb:.2f} GB on disk")
if engine.tables.get(TABLE) != ROOT / csv:
    engine.register(TABLE, ROOT / csv)
    engine.samples.pop(TABLE, None)          # a sample of another file is no longer valid

st.sidebar.subheader("Sample")
method = st.sidebar.radio("Method", ["stratified", "uniform"], horizontal=True)
columns = engine.columns(TABLE)
strata = None
if method == "stratified":
    default = ["region"] if "region" in columns else columns[:1]
    strata = st.sidebar.multiselect("Stratify on", columns, default=default)
rate_pct = st.sidebar.select_slider("Sample rate (%)", options=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0], value=1.0)
min_rows = st.sidebar.number_input("Min rows per stratum", 0, 100_000, 2000, step=500,
                                   disabled=(method == "uniform"))

if st.sidebar.button("Build sample", type="primary"):
    with st.spinner("One pass over the file..."):
        strata_arg = (strata[0] if len(strata) == 1 else strata) if strata else None
        engine.build_sample(TABLE, rate=rate_pct / 100, strata=strata_arg, min_rows=int(min_rows))

sample = engine.samples.get(TABLE)
if sample is not None:
    st.sidebar.success(sample.summary())
    st.sidebar.caption(f"Sample memory: {sample.data.memory_usage(deep=True).sum() / 1e6:.1f} MB")
else:
    st.sidebar.info("No sample yet: only exact queries are possible.")

# ---------------------------------------------------------------- main
st.title("Query")
example = st.selectbox("Examples", EXAMPLES)
sql = st.text_area("SQL", value=example, height=90)
st.caption("Supported: COUNT(*), SUM(col), AVG(col) · WHERE with =, !=, <, <=, >, >= joined by AND · "
           f"GROUP BY one column · table name: `{TABLE}`")

col_a, col_b = st.columns(2)
run_approx = col_a.button("Run approximate", disabled=sample is None)
run_exact = col_b.button("Run exact (full scan)")
compare = st.checkbox("Run both and compare", value=False)


def show_result(result, title):
    st.subheader(title)
    st.write(f"{result.seconds:.3f}s · {result.rows_scanned:,} rows scanned")
    st.dataframe(result.table)


def approx_chart(result):
    q = result.query
    name = q.output_names()[0]
    t = result.table
    if len(t) <= 1:
        return
    chart = pd.DataFrame({"estimate": t[name], "low": t[name] - t[f"{name}_ci"],
                          "high": t[name] + t[f"{name}_ci"]})
    st.caption(f"{name}: estimate with 95% interval")
    st.line_chart(chart)


if run_approx or run_exact or compare:
    try:
        parse(sql)
        approx = exact = None
        if (run_approx or compare) and sample is not None:
            approx = engine.query(sql)
            show_result(approx, "Approximate")
            approx_chart(approx)
            low = approx.table.index[approx.table["sample_rows"] < MIN_RELIABLE_ROWS].tolist()
            if low:
                st.warning(f"Fewer than {MIN_RELIABLE_ROWS} sample rows for: {', '.join(map(str, low))}. "
                           "Treat these estimates with caution, or run exact.")
        if run_exact or compare:
            with st.spinner("Scanning the whole file in 1M-row chunks..."):
                exact = engine.query(sql, exact=True)
            show_result(exact, "Exact")

        if approx is not None and exact is not None:
            st.subheader("Comparison")
            names = approx.query.output_names()
            diff = pd.DataFrame(index=exact.table.index)
            for n in names:
                diff[f"{n}_rel_error_%"] = (100 * (approx.table[n] - exact.table[n]).abs()
                                            / exact.table[n].abs())
                diff[f"{n}_in_95%_ci"] = ((approx.table[n] - exact.table[n]).abs()
                                          <= approx.table[f"{n}_ci"])
            m1, m2 = st.columns(2)
            m1.metric("Speedup (this query)", f"{exact.seconds / max(approx.seconds, 1e-6):.0f}x")
            m2.metric("Mean relative error", f"{diff.filter(like='rel_error').mean().mean():.2f}%")
            st.dataframe(diff.round(3))
    except (QueryError, KeyError) as exc:
        st.error(str(exc))
