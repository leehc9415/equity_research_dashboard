# Calendar integration and DART updates

The top-level Calendar menu loads `dashboard/data/calendar-data.json` on first entry. The export dashboard keeps its existing data and controls. Direct link: `#calendar`.

The public bundle contains DART metadata and actual figures, plus the minimal Quantiwise history, consensus and market capitalization used by the calendar. The user explicitly authorized publishing this supplement to the public GitHub repository and Pages site on 2026-10-28. The whole workbook, unrelated companies and extraction files are not published. The original Excel workbook is never opened, edited or refreshed by this integration.

## GitHub collection

Add the OpenDART API key as repository Actions secret `DART_API_KEY`. Do not put it in any tracked file. The `Update DART calendar` workflow queries filings every 30 minutes on weekdays from 09:07 through 21:37 KST. Scheduled execution can be delayed by GitHub. Manual execution is also available.

The first collection checks the preceding 31 days. Later runs overlap the last successful collection by seven days to catch late arrivals and corrections. Receipt IDs are retained so reprocessing is idempotent. Multiple release dates in one fiscal quarter and separate IR filings are preserved. IR date ranges expand to their individual event days.

Collection and validation complete before atomically replacing the published calendar. If an API response, date, earnings unit or correction relationship cannot be interpreted, collection fails and the existing file remains unchanged. Unknown correction relationships require review rather than silently changing an unrelated event.

The updater does not acquire fresh Quantiwise estimates or market capitalizations on GitHub. Published supplements retain the workbook's saved timestamp and original period/base-date settings; they are not represented as a live feed or guaranteed pre-announcement consensus vintage. New DART collections preserve matching supplements and do not reuse a different quarter's consensus. DART-only bundles still reject Quantiwise enrichments during validation. The browser never receives the API key or the whole workbook.

## Deployment

`Deploy investment dashboard` publishes dashboard changes on main and can be called by either collector. Export and DART collection remain independent. All deployments use the same concurrency group and check out current main so they publish the current combined dashboard. GITHUB_TOKEN-generated data commits do not need to trigger another push workflow: each collector calls deployment directly after a verified update.

## Checks

```
python -m unittest discover -s tests -p test_calendar_pipeline.py -v
python scripts/validate_calendar_data.py
```

This is the first integrated calendar pipeline. It does not claim that the planned central SQLite database, full long-term source archive or local unattended Quantiwise refresh has already been implemented. Those migrations must preserve the calendar schema and receipt/correction history.
