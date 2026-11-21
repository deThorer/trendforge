import pandas as pd
import pytest

from trendforge.exact import exact_query
from trendforge.query import parse


@pytest.mark.parametrize("chunksize", [7, 1_000, 1_000_000])   # odd chunk sizes test merging
def test_exact_matches_pandas(small_csv, chunksize):
    df = pd.read_csv(small_csv)
    q = parse("SELECT AVG(price), SUM(revenue), COUNT(*) FROM orders WHERE quantity > 1 GROUP BY region")
    got, rows = exact_query(small_csv, q, chunksize=chunksize)
    d = df[df.quantity > 1].groupby("region")
    assert rows == len(df)
    pd.testing.assert_series_equal(got["avg_price"], d.price.mean(), check_names=False)
    pd.testing.assert_series_equal(got["sum_revenue"], d.revenue.sum(), check_names=False)
    assert (got["count"] == d.size()).all()


def test_count_star_without_group(small_csv):
    got, _ = exact_query(small_csv, parse("SELECT COUNT(*) FROM orders"), chunksize=3_000)
    assert got["count"].iloc[0] == 20_000
