"""
A small SQL parser for the subset of SQL TrendForge supports:

    SELECT <agg>[, <agg> ...][, <group_col>]
    FROM <table>
    [WHERE <col> <op> <value> [AND <col> <op> <value> ...]]
    [GROUP BY <group_col>]

    <agg>   = COUNT(*) | SUM(col) | AVG(col)
    <op>    = = != < <= > >=
    <value> = number | 'string'

It's regex based, not a full grammar. That's enough for analytical aggregate queries,
and it gives clear error messages when a query is outside the supported subset.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

AGG_FUNCS = ("COUNT", "SUM", "AVG")
OPS = ("<=", ">=", "!=", "=", "<", ">")

_QUERY_RE = re.compile(
    r"^\s*SELECT\s+(?P<select>.+?)\s+FROM\s+(?P<table>\w+)"
    r"(?:\s+WHERE\s+(?P<where>.+?))?"
    r"(?:\s+GROUP\s+BY\s+(?P<group>\w+))?\s*;?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_AGG_RE = re.compile(r"^(COUNT|SUM|AVG)\s*\(\s*(\*|\w+)\s*\)$", re.IGNORECASE)
_COND_RE = re.compile(r"^(\w+)\s*(<=|>=|!=|=|<|>)\s*(.+)$")


class QueryError(ValueError):
    pass


Value = Union[float, int, str]


@dataclass
class Query:
    table: str
    aggregates: List[Tuple[str, Optional[str]]]           # e.g. [("AVG", "price"), ("COUNT", None)]
    filters: List[Tuple[str, str, Value]] = field(default_factory=list)
    group_by: Optional[str] = None

    @property
    def columns(self) -> List[str]:
        """Columns that must be read from disk to answer the query."""
        cols = {c for _, c in self.aggregates if c}
        cols |= {c for c, _, _ in self.filters}
        if self.group_by:
            cols.add(self.group_by)
        return sorted(cols)

    def output_names(self) -> List[str]:
        return [f"{f.lower()}_{c}" if c else f.lower() for f, c in self.aggregates]


def _parse_value(text: str) -> Value:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1]
    try:
        number = float(text)
    except ValueError:
        raise QueryError(f"can't read value {text!r} (put strings in single quotes)")
    return int(number) if number.is_integer() and "." not in text else number


def _split_and(where: str) -> List[str]:
    # split on AND that is not inside quotes
    parts, current, in_quote = [], [], False
    tokens = re.split(r"(\s+AND\s+|')", where, flags=re.IGNORECASE)
    for tok in tokens:
        if tok == "'":
            in_quote = not in_quote
            current.append(tok)
        elif re.fullmatch(r"\s+AND\s+", tok, flags=re.IGNORECASE) and not in_quote:
            parts.append("".join(current))
            current = []
        else:
            current.append(tok)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def parse(sql: str) -> Query:
    m = _QUERY_RE.match(sql)
    if not m:
        raise QueryError("query must look like: SELECT AVG(col) FROM table [WHERE ...] [GROUP BY col]")

    group_by = m.group("group")
    aggregates, plain_cols = [], []
    for item in [s.strip() for s in m.group("select").split(",")]:
        agg = _AGG_RE.match(item)
        if agg:
            func, col = agg.group(1).upper(), agg.group(2)
            if col == "*":
                if func != "COUNT":
                    raise QueryError(f"{func}(*) is not supported, only COUNT(*)")
                col = None
            aggregates.append((func, col))
        elif re.fullmatch(r"\w+", item):
            plain_cols.append(item)
        else:
            raise QueryError(f"unsupported select item: {item!r}")

    if not aggregates:
        raise QueryError("query needs at least one aggregate: COUNT(*), SUM(col) or AVG(col)")
    for col in plain_cols:
        if col != group_by:
            raise QueryError(f"column {col!r} must be in GROUP BY (only one group column is supported)")

    filters = []
    if m.group("where"):
        for cond in _split_and(m.group("where")):
            c = _COND_RE.match(cond)
            if not c:
                raise QueryError(f"can't parse condition {cond!r} (only AND of simple comparisons)")
            filters.append((c.group(1), c.group(2), _parse_value(c.group(3))))

    return Query(table=m.group("table"), aggregates=aggregates, filters=filters, group_by=group_by)
