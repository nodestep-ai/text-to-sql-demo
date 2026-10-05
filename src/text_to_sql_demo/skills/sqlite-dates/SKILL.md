---
name: sqlite-dates
description: SQLite date functions, and how to filter and group by year, quarter, month, week or day
---

# Dates in SQLite

SQLite has no date type. Dates are usually text in ISO 8601, such as `2025-03-14` or `2025-03-14 09:30:00`, which compares and sorts correctly as text.

- A period: `ordered_at >= '2025-01-01' AND ordered_at < '2026-01-01'`. This also covers times on the last day, which `BETWEEN '2025-01-01' AND '2025-12-31'` leaves out.
- Year: `strftime('%Y', ordered_at)`. Month: `strftime('%Y-%m', ordered_at)`. Day: `date(ordered_at)`.
- Quarter: `strftime('%Y', ordered_at) || '-Q' || ((CAST(strftime('%m', ordered_at) AS INTEGER) + 2) / 3)`.
- Week: `strftime('%Y-%W', ordered_at)`. Weeks start on Monday; days before the first Monday of a year are week 00.
- Weekday: `strftime('%w', ordered_at)`, where 0 is Sunday.
- Relative to today: `date('now')`, `date('now', 'start of month')`, `date('now', 'start of month', '-1 month')`, `date('now', 'start of year')`, `date('now', '-7 days')`.
- Days between two dates: `julianday(later) - julianday(earlier)`.
- Unix seconds: `datetime(column, 'unixepoch')`.

`'now'` is the server's clock in UTC. When the data ends before today, "last month" may hold no rows: check the latest date with `max(ordered_at)` and say which period you used.
