"""
Turning a sample into estimates with 95% confidence intervals.

Notation, per stratum h:  N_h = rows in the full file,  n_h = rows in the sample,
f_h = n_h / N_h. For a uniform sample there is just one stratum (h = "__all__").

For a group g (from GROUP BY) and a row i, define the indicator
    I_i = 1 if row i passes the WHERE filter and belongs to group g, else 0

COUNT  : Y_i = I_i                Total  T = sum_h N_h * mean_h(Y)
SUM(x) : Y_i = I_i * x_i          Var(T) = sum_h N_h^2 (1 - f_h) s_h^2 / n_h
AVG(x) : R = SUM / COUNT (ratio estimator). Its variance comes from linearisation:
         Z_i = I_i * (x_i - R),   Var(R) ~= Var(T_Z) / COUNT^2

s_h^2 is the sample variance of Y (or Z) over ALL n_h rows of the stratum, including the
zeros for rows that don't match - that's what makes the filter part of the estimate.

Caveat: the CIs use a normal approximation. They are reliable when a group has a few
dozen sampled rows; for tiny groups on skewed columns (e.g. revenue) they are too narrow.
`sample_rows` is returned so the caller can flag those groups.

Everything is computed from per-(stratum, group) sums so it stays vectorised:
    sum Y, sum Y^2 over matching rows (non-matching rows contribute 0).
"""
import numpy as np
import pandas as pd

from trendforge.query import Query
from trendforge.sampler import ALL, Sample
from trendforge.stream import filter_mask

Z_95 = 1.96


def _stratum_total(sum_y, sum_y2, n, N):
    """Estimated total and its variance for one stratum, from sums over the sample."""
    mean = sum_y / n
    var = np.where(n > 1, (sum_y2 - n * mean ** 2) / np.maximum(n - 1, 1), 0.0)
    var = np.clip(var, 0, None)                      # guard tiny negative values from rounding
    fpc = 1 - n / N                                  # finite population correction
    return N * mean, N ** 2 * fpc * var / n


def estimate(sample: Sample, query: Query, z: float = Z_95) -> pd.DataFrame:
    data = sample.data
    matched = data[filter_mask(data, query.filters)]
    group = matched[query.group_by].astype(str) if query.group_by else pd.Series(ALL, index=matched.index)

    strata = pd.DataFrame({"N": pd.Series(sample.population),
                           "n": data["_stratum"].astype(str).value_counts()}).fillna(0)
    strata = strata[strata["n"] > 0]

    # per (stratum, group) sums over matching rows
    keys = [matched["_stratum"].astype(str), group.rename("group")]
    stats = {"c": matched.groupby(keys, observed=True).size()}
    for col in {c for _, c in query.aggregates if c}:
        x = matched[col].astype(float)
        stats[f"s_{col}"] = x.groupby(keys, observed=True).sum()
        stats[f"q_{col}"] = (x ** 2).groupby(keys, observed=True).sum()
    stats = pd.DataFrame(stats).fillna(0)
    if stats.empty:
        return pd.DataFrame()

    groups = stats.index.get_level_values(1).unique()
    full = pd.MultiIndex.from_product([strata.index, groups], names=["_stratum", "group"])
    stats = stats.reindex(full, fill_value=0)       # strata where a group has no rows -> zeros
    N = strata["N"].reindex(stats.index.get_level_values(0)).to_numpy()
    n = strata["n"].reindex(stats.index.get_level_values(0)).to_numpy()

    def total(sum_y, sum_y2):
        t, v = _stratum_total(sum_y.to_numpy(), sum_y2.to_numpy(), n, N)
        g = stats.index.get_level_values(1)
        return pd.Series(t).groupby(g).sum(), pd.Series(v).groupby(g).sum()

    count_t, count_v = total(stats["c"], stats["c"])            # indicator: Y = Y^2
    out = pd.DataFrame(index=count_t.index)
    for (func, col), name in zip(query.aggregates, query.output_names()):
        if func == "COUNT":
            est, var = count_t, count_v
        else:
            sum_t, sum_v = total(stats[f"s_{col}"], stats[f"q_{col}"])
            if func == "SUM":
                est, var = sum_t, sum_v
            else:
                ratio = sum_t / count_t
                r = ratio.reindex(stats.index.get_level_values(1)).to_numpy()
                s, q, c = stats[f"s_{col}"], stats[f"q_{col}"], stats["c"]
                z_sum = s - r * c                                   # sum of Z = I (x - R)
                z_sq = q - 2 * r * s + r ** 2 * c                   # sum of Z^2
                _, z_var = total(z_sum, z_sq)
                est, var = ratio, z_var / count_t ** 2
        half = z * np.sqrt(var)
        out[name] = est
        out[f"{name}_ci"] = half
        out[f"{name}_rel_ci_pct"] = 100 * half / est.abs().replace(0, np.nan)

    # how many sampled rows back each group's estimate. Below ~30 the normal approximation
    # behind the CI is unreliable (especially for skewed columns), so callers should warn.
    out["sample_rows"] = stats["c"].groupby(level=1).sum().astype(int)
    out.index.name = query.group_by or "group"
    return out.sort_index()


MIN_RELIABLE_ROWS = 30
