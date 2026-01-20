import os

from core import history_repo
from core import projects


def _count_unassigned(rows):
    _, unassigned = projects.group_rows_by_project(rows)
    return len(unassigned)


def main():
    query = history_repo.HistoryQuery()

    csv_result = history_repo.list_history_rows_csv(query, page=1, per_page=1, error=None)
    sql_result = history_repo.list_history_rows_sql(query, page=1, per_page=1, error=None)

    csv_count = _count_unassigned(csv_result.rows_all)
    sql_count = _count_unassigned(sql_result.rows_all)

    print("Unassigned jobs parity")
    print(f"CSV unassigned: {csv_count}")
    print(f"SQL unassigned: {sql_count}")
    if csv_count != sql_count:
        print("WARNING: counts differ")


if __name__ == "__main__":
    main()