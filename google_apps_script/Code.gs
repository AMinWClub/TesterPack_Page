/**
 * Sorrad — daily UTM sales -> Google Sheets
 * Paste into Extensions > Apps Script in the target Google Sheet.
 * Script Properties (Project Settings):
 *   SALES_API_URL = https://api.example.com/googlesheetapi/
 *   SALES_API_KEY = same random secret as SALES_API_KEY on FastAPI server
 */
const SALES_SETTINGS = Object.freeze({
  sheetName: 'Sheet1',      // change to actual Google Sheet tab name if needed
  firstDataRow: 2,
  headerRow: 1,
  sourceCol: 7,            // G
  mediumCol: 8,            // H
  campaignCol: 9,          // I
  discountCodeCol: 10,     // J - discount_code (optional OR match)
  firstOutputCol: 11,     // K; A:J will never be written by this script
  skippedRows: [],        // all rows with a valid source are included
  startDate: '2026-11-16', // 1405/08/25, inclusive
  maxFilters: 500
});

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('گزارش فروش UTM')
    .addItem('به‌روزرسانی فروش', 'syncDailySales')
    .addItem('فعال‌کردن آپدیت ساعتی', 'installHourlySalesTrigger')
    .addItem('حذف آپدیت ساعتی', 'removeHourlySalesTriggers')
    .addToUi();
}

function syncDailySales() {
  const lock = LockService.getDocumentLock() || LockService.getScriptLock();
  if (!lock.tryLock(30000)) {
    throw new Error('Another daily-sales sync is already running.');
  }
  try {
    syncDailySalesUnlocked_();
  } finally {
    lock.releaseLock();
  }
}

function getSalesSpreadsheet_() {
  const props = PropertiesService.getScriptProperties();
  const id = (props.getProperty('SALES_SPREADSHEET_ID') || '').trim();
  if (id) return SpreadsheetApp.openById(id);
  const active = SpreadsheetApp.getActiveSpreadsheet();
  if (!active) {
    throw new Error('No active spreadsheet. Set SALES_SPREADSHEET_ID in Script Properties.');
  }
  // Persist once so hourly triggers work even without an active spreadsheet.
  props.setProperty('SALES_SPREADSHEET_ID', active.getId());
  return active;
}

function syncDailySalesUnlocked_() {
  const cfg = SALES_SETTINGS;
  let stage = 'starting';
  try {
    stage = 'loading sheet';
    const sheet = getSalesSpreadsheet_().getSheetByName(cfg.sheetName);
    if (!sheet) throw new Error('Sheet not found: ' + cfg.sheetName);

    const lastRow = Math.max(sheet.getLastRow(), cfg.firstDataRow - 1);
    if (lastRow < cfg.firstDataRow) throw new Error('There are no data rows.');
    const count = lastRow - cfg.firstDataRow + 1;

    stage = 'reading G:J';
    const input = sheet.getRange(cfg.firstDataRow, cfg.sourceCol, count,
                                 cfg.discountCodeCol - cfg.sourceCol + 1).getDisplayValues();
    const excluded = new Set(cfg.skippedRows);
    const filters = [];
    const validRows = new Set();

    for (let i = 0; i < input.length; i++) {
      const sheetRow = cfg.firstDataRow + i;
      if (excluded.has(sheetRow)) continue;
      const source = String(input[i][0] || '').trim();
      const medium = String(input[i][1] || '').trim();
      const campaign = String(input[i][2] || '').trim();
      const discountCode = String(input[i][3] || '').trim();
      if (!source) continue;
      filters.push({
        row: sheetRow, source, medium: medium || null,
        campaign: campaign || null,
        discount_code: discountCode || null
      });
      validRows.add(sheetRow);
    }
    if (filters.length > cfg.maxFilters) {
      throw new Error('Too many matching rows (' + filters.length + '); max is ' + cfg.maxFilters);
    }
    if (!filters.length) throw new Error('No valid source rows to query.');
    console.log('UTM sync: querying ' + filters.length + ' rows');

    const properties = PropertiesService.getScriptProperties();
    const apiUrl = (properties.getProperty('SALES_API_URL') || '').trim();
    const apiKey = (properties.getProperty('SALES_API_KEY') || '').trim();
    if (!apiUrl.startsWith('https://') || !apiKey) {
      throw new Error('Set SALES_API_URL (HTTPS) and SALES_API_KEY in Script Properties.');
    }

    stage = 'API request';
    const response = UrlFetchApp.fetch(apiUrl, {
      method: 'post',
      contentType: 'application/json; charset=utf-8',
      headers: {'X-API-Key': apiKey},
      payload: JSON.stringify({rows: filters}),
      muteHttpExceptions: true,
      followRedirects: false,
      timeoutSeconds: 60
    });
    const status = response.getResponseCode();
    if (status !== 200) {
      throw new Error('Sales API returned HTTP ' + status + ': ' +
                      response.getContentText().slice(0, 250));
    }
    const data = JSON.parse(response.getContentText());

    stage = 'validating API response';
    const resultsByRow = validateApiResponse_(data, filters, cfg.startDate);
    const days = data.days;
    const width = days.length;
    if (!width) throw new Error('API returned zero date columns.');
    const nonZeroValues = data.rows.reduce((sum, item) =>
      sum + item.totals.filter(value => Number(value) !== 0).length, 0);
    console.log('UTM API response validated: ' + data.rows.length + ' rows, ' +
                width + ' days, ' + nonZeroValues + ' non-zero daily totals');

    const output = [];
    for (let i = 0; i < count; i++) {
      const sheetRow = cfg.firstDataRow + i;
      if (excluded.has(sheetRow)) {
        output.push(null);             // reserved for optional future manual exclusions
      } else if (!validRows.has(sheetRow)) {
        output.push(Array(width).fill('')); // missing source
      } else {
        output.push(resultsByRow[sheetRow]);
      }
    }

    const requiredLastCol = cfg.firstOutputCol + width - 1;
    if (requiredLastCol > sheet.getMaxColumns()) {
      stage = 'inserting missing columns';
      sheet.insertColumnsAfter(sheet.getMaxColumns(), requiredLastCol - sheet.getMaxColumns());
      SpreadsheetApp.flush();
    }

    // IMPORTANT: Write ONE COLUMN at a time. Avoid all column-wide actions
    // and multi-column output selections; this also makes failures diagnosable.
    // Write VALUES FIRST, headers SECOND, so an interrupted run doesn't leave
    // new dates with no sales figures.
    for (let dayIndex = 0; dayIndex < width; dayIndex++) {
      const col = cfg.firstOutputCol + dayIndex;
      let segmentIndex = -1;
      let segment = [];

      function writeSegment_() {
        if (!segment.length) return;
        const firstRow = cfg.firstDataRow + segmentIndex;
        stage = 'writing sales: column ' + col + ', rows ' + firstRow +
                '-' + (firstRow + segment.length - 1);
        const range = sheet.getRange(firstRow, col, segment.length, 1);
        range.setValues(segment);
        SpreadsheetApp.flush();
        segment = [];
        segmentIndex = -1;
      }

      for (let i = 0; i < output.length; i++) {
        if (output[i] === null) {
          writeSegment_();
          continue;
        }
        if (segmentIndex === -1) segmentIndex = i;
        segment.push([output[i][dayIndex]]);
      }
      writeSegment_();
    }

    // Headers are single-cell writes, with no resizing or multi-column formatting.
    for (let dayIndex = 0; dayIndex < width; dayIndex++) {
      const col = cfg.firstOutputCol + dayIndex;
      stage = 'writing Jalali header: column ' + col;
      const header = sheet.getRange(cfg.headerRow, col, 1, 1);
      header.setNumberFormat('@');
      header.setValue(days[dayIndex].jalali);
      SpreadsheetApp.flush();
    }

    // Remove stale, empty, duplicated report columns outside the current date range.
    // E.g. column S having `1405/09/01 2` after Q=`1405/09/01`, R=`1405/09/02`.
    // Numeric sales snapshots are superseded by the freshly written API results.
    // Never delete a column containing formulas, text or unrelated manual data.
    stage = 'removing empty duplicate date columns';
    try {
      const removedDuplicates = removeTrailingDuplicateDateColumns_(sheet, cfg, days, validRows);
      if (removedDuplicates) {
        console.log('UTM sync: removed ' + removedDuplicates + ' trailing duplicate date column(s)');
      }
    } catch (cleanupError) {
      console.warn('UTM sales already written; duplicate cleanup failed: ' + cleanupError);
    }

    // Styling is optional and MUST NOT prevent sales from being populated.
    stage = 'optional number formatting';
    try {
      for (let dayIndex = 0; dayIndex < width; dayIndex++) {
        const col = cfg.firstOutputCol + dayIndex;
        // Format all but skipped row; number formats do not change numeric values.
        let segmentIndex = -1;
        function formatSegment_(endIndex) {
          if (segmentIndex === -1) return;
          sheet.getRange(cfg.firstDataRow + segmentIndex, col,
                         endIndex - segmentIndex, 1).setNumberFormat('#,##0');
          segmentIndex = -1;
        }
        for (let i = 0; i < output.length; i++) {
          if (output[i] === null) formatSegment_(i);
          else if (segmentIndex === -1) segmentIndex = i;
        }
        formatSegment_(output.length);
      }
      SpreadsheetApp.flush();
    } catch (formatError) {
      console.warn('UTM sales successfully written, optional formatting skipped: ' + formatError);
    }

    console.log('UTM sales updated: ' + filters.length + ' filtered rows, ' +
                width + ' days, through ' + data.end_date);
  } catch (error) {
    const detail = error && error.message ? error.message : String(error);
    console.error('UTM sales FAILED at [' + stage + ']: ' + detail);
    throw new Error('UTM sales FAILED at [' + stage + ']: ' + detail);
  }
}

/** Turn Persian/Arabic digits into ASCII and recognize historical date headers.
 * Supports yyyy/mm/dd, dd/mm/yyyy, and a Sheets-table duplicate suffix (e.g. " 2").
 * Returns null for unrelated columns, keeping them completely out of scope.
 */
function canonicalJalaliHeader_(value) {
  const text = String(value == null ? '' : value)
    .replace(/[۰-۹]/g, c => String(c.charCodeAt(0) - 1776))
    .replace(/[٠-٩]/g, c => String(c.charCodeAt(0) - 1632))
    .replace(/[\u200e\u200f\u200c\u200d\u2060]/g, '')
    .trim();
  const parts = text.match(/^(\d{1,4})[\/\-](\d{1,2})[\/\-](\d{1,4})(?:\s+\d+)?$/);
  if (!parts) return null;
  let year, month, day;
  if (parts[1].length === 4) {
    year = Number(parts[1]); month = Number(parts[2]); day = Number(parts[3]);
  } else if (parts[3].length === 4) {
    year = Number(parts[3]); month = Number(parts[2]); day = Number(parts[1]);
  } else return null;
  if (year < 1300 || year > 1600 || month < 1 || month > 12 ||
      day < 1 || day > (month <= 6 ? 31 : month <= 11 ? 30 : 30)) return null;
  return String(year).padStart(4, '0') + '/' +
         String(month).padStart(2, '0') + '/' + String(day).padStart(2, '0');
}

/** Only removes a *trailing* column if its header repeats a reported day.
 * Existing numeric daily-sales snapshots are safe to discard AFTER refreshing
 * their canonical columns from the API. The whole column must contain either
 * blank cells or numeric values in a row that was just refreshed. A formula,
 * free text, or data outside a refreshed row prevents deletion.
 * Future dates, columns A:J, and nonreport columns are never deleted.
 */
function removeTrailingDuplicateDateColumns_(sheet, cfg, days, validRows) {
  const expectedLastCol = cfg.firstOutputCol + days.length - 1;
  const expectedDates = new Set(days.map(d => d.jalali));
  let removed = 0;
  // Re-read right edge after each deletion; this handles multiple stale columns.
  while (sheet.getLastColumn() > expectedLastCol) {
    const col = sheet.getLastColumn();
    const rawHeader = sheet.getRange(cfg.headerRow, col, 1, 1).getDisplayValue();
    const parsed = canonicalJalaliHeader_(rawHeader);
    if (!parsed || !expectedDates.has(parsed)) break;

    const height = Math.max(0, sheet.getLastRow() - cfg.headerRow);
    if (height > 0) {
      const bodyRange = sheet.getRange(cfg.headerRow + 1, col, height, 1);
      const values = bodyRange.getValues();
      const formulas = bodyRange.getFormulas();
      const hasUnsafeContent = values.some((v, i) => {
        const sheetRow = cfg.headerRow + 1 + i;
        if (formulas[i][0]) return true;
        if (v[0] === '' || v[0] === null) return false;
        return !validRows.has(sheetRow) ||
               typeof v[0] !== 'number' || !Number.isFinite(v[0]);
      });
      if (hasUnsafeContent) {
        console.warn('Duplicate date column ' + col +
                     ' has formula/text/data outside refreshed rows; not deleting it');
        break;
      }
    }
    sheet.deleteColumn(col);
    SpreadsheetApp.flush();
    removed++;
  }
  return removed;
}

function validateApiResponse_(response, filters, startDate) {
  if (!response || response.start_date !== startDate ||
      !Array.isArray(response.days) || !Array.isArray(response.rows)) {
    throw new Error('Invalid sales API response structure');
  }
  // Old backends silently ignore the new discount_code field. Fail safely
  // instead of writing incorrect UTM-only amounts if backend is not upgraded.
  if (filters.some(row => row.discount_code) &&
      response.matching_logic !== 'utm_or_discount_regex_v1') {
    throw new Error('Sales API does not support UTM OR discount_code yet. Deploy the v4 backend first.');
  }
  const days = response.days;
  const jalaliSeen = new Set();
  for (let i = 0; i < days.length; i++) {
    const date = new Date(startDate + 'T00:00:00Z');
    date.setUTCDate(date.getUTCDate() + i);
    const expectedDate = Utilities.formatDate(date, 'UTC', 'yyyy-MM-dd');
    if (!days[i] || days[i].date !== expectedDate ||
        !/^\d{4}\/\d{2}\/\d{2}$/.test(days[i].jalali)) {
      throw new Error('Invalid or out-of-order day at column index ' + i);
    }
    if (jalaliSeen.has(days[i].jalali)) {
      throw new Error('Sales API returned a duplicate Jalali date: ' + days[i].jalali);
    }
    jalaliSeen.add(days[i].jalali);
  }
  if (response.rows.length !== filters.length) {
    throw new Error('API returned an unexpected number of rows');
  }
  const requested = new Set(filters.map(r => r.row));
  const mapped = {};
  for (const item of response.rows) {
    if (!requested.has(item.row) || mapped[item.row] ||
        !Array.isArray(item.totals) || item.totals.length !== days.length) {
      throw new Error('Invalid / duplicate / missing sales row: ' + item.row);
    }
    mapped[item.row] = item.totals.map(value => {
      const raw = String(value);
      if (!/^-?\d+(\.\d+)?$/.test(raw)) {
        throw new Error('Invalid numeric value from sales API: ' + raw);
      }
      const num = Number(raw);
      if (!Number.isFinite(num) || Math.abs(num) > Number.MAX_SAFE_INTEGER) {
        throw new Error('Sales amount exceeds safe Google Apps Script numeric range');
      }
      return num;
    });
  }
  for (const requestedRow of requested) {
    if (!mapped[requestedRow]) throw new Error('Missing API row: ' + requestedRow);
  }
  return mapped;
}

function installHourlySalesTrigger() {
  getSalesSpreadsheet_(); // save the bound Spreadsheet ID for unattended triggers
  removeHourlySalesTriggers();
  ScriptApp.newTrigger('syncDailySales').timeBased().everyHours(1).create();
  console.log('Hourly sales synchronization enabled.');
}

function removeHourlySalesTriggers() {
  for (const trigger of ScriptApp.getProjectTriggers()) {
    if (trigger.getHandlerFunction() === 'syncDailySales') {
      ScriptApp.deleteTrigger(trigger);
    }
  }
  console.log('Daily sales synchronization triggers removed.');
}
