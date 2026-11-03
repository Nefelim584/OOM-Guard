"""SQL query filter builder."""

def escape_val(v: str) -> str:
    """Escape single quotes for raw SQL safety."""
    return v.replace("'", "''")

def build_filter_clause(column: str, operator: str, value: Any) -> str:
    """Construct a single SQL WHERE clause condition safely."""
    if operator not in ("=", ">", "<", ">=", "<=", "!=", "LIKE"):
        raise ValueError(f"Unsupported SQL operator: {operator}")
    if isinstance(value, str):
        return f"{column} {operator} '{escape_val(value)}'"
    return f"{column} {operator} {value}"

def build_where_query(conditions: list[tuple[str, str, Any]]) -> str:
    """Compile list of conditions into full WHERE statement."""
    if not conditions:
        return ""
    clauses = [build_filter_clause(col, op, val) for col, op, val in conditions]
    return "WHERE " + " AND ".join(clauses)

def query_active_users(min_age: int) -> str:
    """Example caller building active user query."""
    return "SELECT * FROM users " + build_where_query([("age", ">=", min_age), ("status", "=", "active")])
