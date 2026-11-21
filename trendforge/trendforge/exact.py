"""
Exact query execution: one streaming pass over the whole file.

Each chunk produces partial aggregates (sum and count per group), which are merged
at the end. AVG is computed as total sum / total count, never as an average of chunk
averages (that would be wrong when chunks have different group sizes).
"""
import pandas as pd

from trendforge.query import Query
from trendforge.stream import DEFAULT_CHUNKSIZE, file_columns, filter_mask, read_chunks

ALL = "__all__"


def _partials(df: pd.DataFrame, query: Query) -> pd.DataFrame:
    """sum_<col> and cnt_<col> per group for one chunk (cnt_* is COUNT(*))."""
    df = df[filter_mask(df, query.filters)]
    keys = df[query.group_by] if query.group_by else pd.Series(ALL, index=df.index)
    out = {"cnt_*": df.groupby(keys, observed=True).size()}
    for col in {c for _, c in query.aggregates if c}:
        g = df[col].groupby(keys, observed=True)
        out[f"sum_{col}"] = g.sum()
        out[f"cnt_{col}"] = g.count()          # non-null values, for AVG
    return pd.DataFrame(out)


def exact_query(path, query: Query, chunksize: int = DEFAULT_CHUNKSIZE):
    total = None
    rows = 0
    columns = query.columns or file_columns(path)[:1]     # COUNT(*) only: read one column
    for chunk in read_chunks(path, columns=columns, chunksize=chunksize):
        rows += len(chunk)
        part = _partials(chunk, query)
        total = part if total is None else total.add(part, fill_value=0)

    result = pd.DataFrame(index=total.index if total is not None else [])
    for (func, col), name in zip(query.aggregates, query.output_names()):
        if func == "COUNT":
            result[name] = total["cnt_*"]
        elif func == "SUM":
            result[name] = total[f"sum_{col}"]
        else:
            result[name] = total[f"sum_{col}"] / total[f"cnt_{col}"]
    result.index.name = query.group_by or "group"
    return result.sort_index(), rows
