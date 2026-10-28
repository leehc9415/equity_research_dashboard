# Calendar integration and DART updates

The top-level Calendar menu loads `dashboard/data/calendar-data.json` on first entry. The export dashboard keeps its existing data and controls. Direct link: `#calendar`. The calendar body has a fixed height at each viewport size. Every selected-day event is rendered; only the right-hand details region scrolls, with no result pagination. Mobile stacks a fixed-height details region below the month grid.

The public bundle contains DART metadata and actual figures, plus the Quantiwise history, consensus and market capitalization used by the calendar. The user explicitly authorized publishing this supplement to the public GitHub repository and Pages site on 2026-10-28. The saved, normalized company reference is tracked at `data/reference/quantiwise-calendar.json` so newly arriving filings can join by stock and fiscal quarter. It includes only the required financial fields, fiscal month and source settings from the supplied universe. It excludes workbook internals, company-name duplicates, original source-row addresses and unrelated extraction files. The original Excel workbook is never opened, edited or refreshed by this integration.

## GitHub collection

Add the OpenDART API key as repository Actions secret `DART_API_KEY`. Do not put it in any tracked file. The `Update DART calendar` workflow queries filings every 30 minutes on weekdays from 09:07 through 21:37 KST. Scheduled execution can be delayed by GitHub. Manual execution is also available.

The first collection checks the preceding 31 days. Later runs overlap the last successful collection by seven days to catch late arrivals and corrections. Receipt IDs are retained so reprocessing is idempotent. Multiple release dates in one fiscal quarter and separate IR filings are preserved. IR date ranges expand to their individual event days.

Validated events replace the published calendar atomically. OpenDART original-file responses 013 (no data) and 014 (file missing) receive three bounded attempts. If still unavailable, only that filing enters `pendingDocuments`; other verified filings continue to publish. Pending receipts never enter `processedReceipts` and retry from their saved filing metadata even after aging out of the rolling list window. Successful retries remove the queue item. A queue means `collection.complete=false`; its count is visible on the calendar and in the Actions summary/warning. It is never treated as a fully collected run.

Authentication, quota, unknown response formats, date/earnings parsing and ambiguous correction errors still fail the run and preserve the last good file. API status codes are logged without response bodies, URLs or keys. Unknown correction relationships require review rather than silently changing an unrelated event.

After each collection, the updater applies `calendar_quantiwise.enrich_calendar` to all calendar events, including filings newly received that day. A current-period consensus joins by stock and quarter; no prior published event for that stock is required. Historical comparisons rebuild from DART values and the saved company reference, so repeated runs are idempotent and corrections recompute comparability. DART figures stay authoritative. Material discrepancies disable the affected consensus comparison; unverified currencies and non-December fiscal calendars do not join quarter financials.

The updater does not acquire fresh Quantiwise estimates or market capitalizations on GitHub. Published supplements retain the workbook's saved timestamp and original period/base-date settings; they are not represented as a live feed or guaranteed pre-announcement consensus vintage. A different quarter's consensus is not reused. DART-only bundles still reject Quantiwise enrichments during validation. The browser receives only the events' joined values, never the API key, whole workbook or whole company reference.

## Deployment

`Deploy investment dashboard` publishes dashboard changes on main and can be called by either collector. Export and DART collection remain independent. All deployments use the same concurrency group and check out current main so they publish the current combined dashboard. GITHUB_TOKEN-generated data commits do not need to trigger another push workflow: each collector calls deployment directly after a verified update.

## Checks

```
python -m unittest discover -s tests -p test_calendar_pipeline.py -v
python scripts/validate_calendar_data.py
```

This is the first integrated calendar pipeline. It does not claim that the planned central SQLite database, full long-term source archive or local unattended Quantiwise refresh has already been implemented. Those migrations must preserve the calendar schema and receipt/correction history.
