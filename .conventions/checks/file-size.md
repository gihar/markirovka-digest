# File Size Limits

## Pass/Fail Rules

| Metric                | Limit     |
|-----------------------|-----------|
| Lines per file        | 800 max   |
| Lines per function    | 50 max    |
| Nesting depth         | 4 levels  |

## FAIL conditions
- Any `.py` file exceeds 800 lines.
- Any function body exceeds 50 lines.
- Nesting deeper than 4 levels (e.g., if > for > if > try > if).

## PASS conditions
- All files are under 800 lines.
- All functions are under 50 lines.
- No deeply nested blocks beyond 4 levels.

## Current codebase reference
- `models.py`: 37 lines
- `config.py`: 117 lines
- `db.py`: 145 lines
- `publisher.py`: 144 lines
- `downloader.py`: 167 lines

Typical file size is 100-200 lines. Extract new modules when approaching 400.
