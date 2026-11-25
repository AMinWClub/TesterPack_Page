# GoogleSheet Report — Optional Medium (TesterPack)

This note supersedes the older `GOOGLESHEET_API.md` and `GOOGLESHEET_DISCOUNT_V4.md` statements that required **both** `source` and `medium`.

The Google Sheet uses G=`utm_source`, H=`utm_medium`, I=`utm_campaign`, J=`discount_code` and writes sales from K onward.

- **Source (G) is required.** A blank source excludes the row, even when other fields are present. Its previously computed K+ values are cleared on the next successful sync that has at least one valid source row.
- **Medium (H) is optional.** If blank, do not restrict `utm_medium` (including orders whose UTM medium is missing).
- **Campaign (I) is optional independently of medium.** Source+campaign without medium means match that source and campaign, across all mediums.
- **Discount code (J) is optional and uses OR** with the whole UTM condition. Matches are literal, normalized, case-insensitive substrings of MongoDB `discount_code`; a matching order counts only once per sheet row even if both branches match.
- With source alone, sum all `total` values for orders with the same normalized `utm_source`, regardless of medium or campaign.
- Historical recalculation, Jalali headers, duplicate-column cleanup, exclusions for `state == "cancelled"`, and Tehran day grouping by `date_paid` remain unchanged.
- Rows with identical or overlapping filters may contain overlapping totals; do not sum row totals as though they were disjoint.

The FastAPI endpoint stays `POST /googlesheetapi/` and uses the existing `SALES_API_KEY`; no `.env` changes or schema migration are needed.

## Deployment order

1. Apply the patch from the root of `TesterPack_Page` on commit `115b7356b74eeda3d61d4694b792cb97a26ad494` (main at patch creation).
2. Restart the **existing** TesterPack FastAPI service.
3. Copy the updated `google_apps_script/Code.gs` into Google Sheets → Extensions → Apps Script; updating the repository file alone does not update Google's copy.
4. Run `syncDailySales` once, confirm source-only sales, then allow the existing hourly trigger to continue.

## Regression tests

```bash
python -m pytest -q tests/test_googlesheet_report.py tests/test_googlesheet_discount.py tests/test_googlesheet_optional_medium.py
node tests/test_code_gs.cjs
```
