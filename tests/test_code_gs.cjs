/** Run with: node tests/test_code_gs.cjs (no npm dependencies). */
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const changes = [];
const written = [];
const rows = [
  ['bale', 'daily', ''],       // real sheet row 2
  ['', '', ''],              // real sheet row 3 (excluded blogger)
  ['landing', 'home', 'bf'],  // real sheet row 4
  ['bale', '', ''],           // real sheet row 5 (missing medium)
];
const cols = 20;
const makeRange = (row, col, n, width) => ({
  getDisplayValues() {
    assert.strictEqual(col, 7);
    assert.strictEqual(width, 3);
    return rows;
  },
  setNumberFormat() { return this; },
  setFontWeight() { return this; },
  setBackground() { return this; },
  setHorizontalAlignment() { return this; },
  setValues(values) {
    changes.push({ row, col, n, width });
    written.push({ row, col, values });
    return this;
  },
});
const sheet = {
  getLastRow: () => 5,
  getRange: makeRange,
  getMaxColumns: () => cols,
  insertColumnsAfter: () => { throw new Error('not expected'); },
  setColumnWidths: () => {},
};
const context = {
  console,
  Set,
  Date,
  Number,
  JSON,
  SpreadsheetApp: {
    getActiveSpreadsheet: () => ({ getId: () => 'spreadsheet-id', getSheetByName: name => name === 'Sheet1' ? sheet : null }),
    openById: id => { assert.strictEqual(id, 'spreadsheet-id'); return { getSheetByName: () => sheet }; },
    flush() {},
  },
  LockService: {
    getDocumentLock: () => ({ tryLock: () => true, releaseLock() {} }),
  },
  PropertiesService: {
    getScriptProperties: () => ({ setProperty: (key, value) => { assert.strictEqual(key, 'SALES_SPREADSHEET_ID'); assert.strictEqual(value, 'spreadsheet-id'); }, getProperty: key => ({
      SALES_API_URL: 'https://example.com/googlesheetapi/',
      SALES_API_KEY: 'test-key',
    })[key] }),
  },
  UrlFetchApp: {
    fetch(url, options) {
      assert.ok(url.startsWith('https://'));
      assert.strictEqual(options.headers['X-API-Key'], 'test-key');
      const filters = JSON.parse(options.payload).rows;
      assert.deepStrictEqual(filters.map(r => r.row), [2, 4]);
      assert.strictEqual(filters[0].campaign, null);
      return {
        getResponseCode: () => 200,
        getContentText: () => JSON.stringify({
          start_date: '2026-11-16', end_date: '2026-11-17',
          days: [
            { date: '2026-11-16', jalali: '1405/08/25' },
            { date: '2026-11-17', jalali: '1405/08/26' },
          ],
          rows: [{ row: 2, totals: ['1500000', '0'] },
                 { row: 4, totals: ['0', '3200000'] }],
        }),
      };
    },
  },
  Utilities: {
    formatDate: date => date.toISOString().slice(0, 10),
  },
};
vm.createContext(context);
const code = fs.readFileSync(path.join(__dirname, '../google_apps_script/Code.gs'), 'utf8');
vm.runInContext(code, context);
vm.runInContext('syncDailySales()', context);
assert.deepStrictEqual(changes.map(x => x.row), [1, 2, 4]); // row3 NEVER written
assert.deepStrictEqual(JSON.parse(JSON.stringify(written[0].values)), [['1405/08/25', '1405/08/26']]);
assert.deepStrictEqual(JSON.parse(JSON.stringify(written[1].values)), [[1500000, 0]]);
assert.deepStrictEqual(JSON.parse(JSON.stringify(written[2].values)), [[0, 3200000], ['', '']]);
assert.ok(changes.every(x => x.col >= 11));
console.log('Apps Script mock integration test passed');
