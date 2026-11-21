import numpy as np
import pandas as pd
import pytest

from trendforge.estimators import estimate
from trendforge.exact import exact_query
from trendforge.query import parse
from trendforge.sampler import build_stratified, build_uniform

SQL = "SELECT AVG(price), SUM(revenue), COUNT(*) FROM orders WHERE quantity > 1 GROUP BY region"


def test_full_sample_gives_exact_answer_and_zero_ci(small_csv):
    q = parse(SQL)
    exact, _ = exact_query(small_csv, q)
    est = estimate(build_uniform(small_csv, 1.0), q)
    for col in exact.columns:
        np.testing.assert_allclose(est[col], exact[col], rtol=1e-9)
        np.testing.assert_allclose(est[f"{col}_ci"], 0, atol=1e-6)    # finite population correction


def test_count_of_strata_is_exact(small_csv):
    s = build_stratified(small_csv, "region", rate=0.05, min_rows=50)
    est = estimate(s, parse("SELECT COUNT(*) FROM orders GROUP BY region"))
    true = pd.read_csv(small_csv).region.value_counts()
    np.testing.assert_allclose(est["count"], true.reindex(est.index))
    assert (est["count_ci"] == 0).all()


@pytest.mark.parametrize("builder", ["uniform", "stratified"])
def test_estimates_are_close(small_csv, builder):
    q = parse(SQL)
    exact, _ = exact_query(small_csv, q)
    s = (build_uniform(small_csv, 0.3, seed=3) if builder == "uniform"
         else build_stratified(small_csv, "region", 0.3, min_rows=200, seed=3))
    est = estimate(s, q)
    big = exact.index[exact["count"] > 1_000]                   # well-populated groups
    for col in exact.columns:
        err = (est.loc[big, col] - exact.loc[big, col]).abs()
        # the 95% CI is 1.96 sigma wide; allow up to ~3.5 sigma so the test is not flaky
        assert (err <= 1.8 * est.loc[big, f"{col}_ci"] + 1e-9).all()
        assert ((err / exact.loc[big, col]) < 0.25).all()
    assert (est["sample_rows"] > 0).all()


def test_filter_with_no_matches_returns_empty(small_csv):
    s = build_uniform(small_csv, 0.2)
    assert estimate(s, parse("SELECT COUNT(*) FROM orders WHERE price < 0")).empty
