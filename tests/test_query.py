import pytest

from trendforge.query import QueryError, parse


def test_basic_group_by():
    q = parse("SELECT AVG(price), COUNT(*), region FROM orders GROUP BY region")
    assert q.table == "orders"
    assert q.aggregates == [("AVG", "price"), ("COUNT", None)]
    assert q.group_by == "region"
    assert q.columns == ["price", "region"]
    assert q.output_names() == ["avg_price", "count"]


def test_where_with_strings_numbers_and_and():
    q = parse("select sum(revenue) from orders where category = 'C07' and price >= 10.5 and quantity != 2")
    assert q.filters == [("category", "=", "C07"), ("price", ">=", 10.5), ("quantity", "!=", 2)]


def test_and_inside_quotes_is_not_split():
    q = parse("SELECT COUNT(*) FROM t WHERE name = 'black and white'")
    assert q.filters == [("name", "=", "black and white")]


def test_trailing_semicolon_and_whitespace():
    assert parse("  SELECT COUNT(*) FROM t ;  ").aggregates == [("COUNT", None)]


@pytest.mark.parametrize("sql", [
    "SELECT price FROM orders",                              # no aggregate
    "SELECT AVG(*) FROM orders",                             # AVG(*)
    "SELECT AVG(price), region FROM orders",                 # region not grouped
    "SELECT MAX(price) FROM orders",                         # unsupported function
    "SELECT COUNT(*) FROM orders WHERE region = Europe",     # unquoted string
    "DELETE FROM orders",
])
def test_rejects_unsupported(sql):
    with pytest.raises(QueryError):
        parse(sql)
