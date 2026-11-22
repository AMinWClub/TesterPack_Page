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
  firstOutputCol: 11,     // K; A:J will never be written by this script
  skippedRows: [3],       // actual row 3 = blogger / کنجد
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
  const sheet = getSalesSpreadsheet_().getSheetByName(cfg.sheetName);
  if (!sheet) throw new Error('Sheet not found: ' + cfg.sheetName);

  const lastRow = Math.max(sheet.getLastRow(), cfg.firstDataRow - 1);
  if (lastRow < cfg.firstDataRow) {
    throw new Error('There are no data rows.');
  }
  const count = lastRow - cfg.firstDataRow + 1;
  const input = sheet.getRange(cfg.firstDataRow, cfg.sourceCol, count, 3).getDisplayValues();
  const excluded = new Set(cfg.skippedRows);
  const filters = [];
  const validRows = new Set();

  for (let i = 0; i < input.length; i++) {
    const sheetRow = cfg.firstDataRow + i;
    if (excluded.has(sheetRow)) continue;
    const source = String(input[i][0] || '').trim();
    const medium = String(input[i][1] || '').trim();
    const campaign = String(input[i][2] || '').trim();
    // No source OR no medium => excluded (not a MongoDB filter with empty values).
    if (!source || !medium) continue;
    filters.push({ row: sheetRow, source: source, medium: medium,
                   campaign: campaign || null });
    validRows.add(sheetRow);
  }
  if (filters.length > cfg.maxFilters) {
    throw new Error('Too many matching rows (' + filters.length + '); max is ' + cfg.maxFilters);
  }

  const properties = PropertiesService.getScriptProperties();
  const apiUrl = (properties.getProperty('SALES_API_URL') || '').trim();
  const apiKey = (properties.getProperty('SALES_API_KEY') || '').trim();
  if (!apiUrl.startsWith('https://') || !apiKey) {
    throw new Error('Set SALES_API_URL (HTTPS) and SALES_API_KEY in Script Properties.');
  }

  const response = UrlFetchApp.fetch(apiUrl, {
    method: 'post',
    contentType: 'application/json; charset=utf-8',
    headers: { 'X-API-Key': apiKey },
    payload: JSON.stringify({ rows: filters }),
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
  const resultsByRow = validateApiResponse_(data, filters, cfg.startDate);
  const days = data.days;
  const width = days.length;
  if (width === 0) throw new Error('API returned zero date columns.');

  // All validation is done before modifying a single cell.
  const output = [];
  for (let i = 0; i < count; i++) {
    const sheetRow = cfg.firstDataRow + i;
    if (excluded.has(sheetRow)) {
      output.push(null); // preserve the excluded blogger row in K+ too
    } else if (!validRows.has(sheetRow)) {
      output.push(Array(width).fill('')); // remove old results for incomplete filters
    } else {
      output.push(resultsByRow[sheetRow]);
    }
  }

  const requiredLastCol = cfg.firstOutputCol + width - 1;
  if (requiredLastCol > sheet.getMaxColumns()) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), requiredLastCol - sheet.getMaxColumns());
  }
  const headers = sheet.getRange(cfg.headerRow, cfg.firstOutputCol, 1, width);
  headers.setNumberFormat('@'); // force Jalali dates to remain text
  headers.setValues([days.map(d => d.jalali)]);
  headers.setFontWeight('bold').setBackground('#e8eef7')
         .setHorizontalAlignment('center');
  sheet.setColumnWidths(cfg.firstOutputCol, width, 110);

  // Write in contiguous blocks, but do not touch skippedRows (row 3).
  let firstIndex = -1;
  let block = [];
  function flushBlock_() {
    if (!block.length) return;
    const range = sheet.getRange(cfg.firstDataRow + firstIndex,
                                 cfg.firstOutputCol, block.length, width);
    range.setNumberFormat('#,##0');
    range.setValues(block);
    block = [];
    firstIndex = -1;
  }
  for (let i = 0; i < output.length; i++) {
    if (output[i] === null) {
      flushBlock_();
      continue;
    }
    if (firstIndex === -1) firstIndex = i;
    block.push(output[i]);
  }
  flushBlock_();
  SpreadsheetApp.flush();
  console.log('UTM sales updated: ' + filters.length + ' filtered rows, ' +
              width + ' days, through ' + data.end_date);
}

function validateApiResponse_(response, filters, startDate) {
  if (!response || response.start_date !== startDate ||
      !Array.isArray(response.days) || !Array.isArray(response.rows)) {
    throw new Error('Invalid sales API response structure');
  }
  const days = response.days;
  for (let i = 0; i < days.length; i++) {
    const date = new Date(startDate + 'T00:00:00Z');
    date.setUTCDate(date.getUTCDate() + i);
    const expectedDate = Utilities.formatDate(date, 'UTC', 'yyyy-MM-dd');
    if (!days[i] || days[i].date !== expectedDate ||
        !/^\d{4}\/\d{2}\/\d{2}$/.test(days[i].jalali)) {
      throw new Error('Invalid or out-of-order day at column index ' + i);
    }
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
