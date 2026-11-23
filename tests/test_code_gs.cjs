/** Run with: node tests/test_code_gs.cjs (no npm dependencies). */
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const code = fs.readFileSync(path.join(__dirname, '../google_apps_script/Code.gs'), 'utf8');

const DAYS = [
  ['2026-11-16', '1405/08/25'],
  ['2026-11-17', '1405/08/26'],
  ['2026-11-18', '1405/08/27'],
  ['2026-11-19', '1405/08/28'],
  ['2026-11-20', '1405/08/29'],
  ['2026-11-21', '1405/08/30'],
  ['2026-11-22', '1405/09/01'],
  ['2026-11-23', '1405/09/02'],
];

function buildMock(options = {}) {
  const written = [];
  const logs = [];
  const deleted = [];
  const cells = new Map();
  const formulas = new Map();
  let lastCol = 19;
  const key = (row, col) => `${row}:${col}`;
  const set = (row, col, value) => cells.set(key(row, col), value);
  const get = (row, col) => cells.has(key(row, col)) ? cells.get(key(row, col)) : '';
  DAYS.forEach((day, i) => set(1, 11 + i, day[1]));
  set(1, 19, '1405/09/01 2'); // duplicate header observed in uploaded workbook
  set(2, 19, 39642400);  // numeric historical sales, observed in the uploaded file
  set(3, 19, options.duplicateHasUnsafeData ? 'do-not-delete' : '');
  if (options.duplicateHasFormula) formulas.set(key(3, 19), '=42');
  if (options.duplicateHasUnrefreshedNumeric) set(5, 19, 900);
  set(3, 11, 46318); // preexisting stale second data row amount must be updated
  const inputs = [
    ['bale', 'daily', '', ''],
    ['instagram', 'konnjed', 'aban', 'konjed'],  // J = discount code, OR with UTM
    ['landing', 'home', 'bf', ''],
    ['bale', '', '', 'konjed'], // excluded: source and medium are still required
  ];
  const makeRange = (row, col, height = 1, width = 1) => ({
    getDisplayValues() {
      if (row === 2 && col === 7 && height === 4 && width === 4) return inputs;
      return Array.from({length:height}, (_,i) => Array.from({length:width}, (_,j) => String(get(row+i,col+j))));
    },
    getDisplayValue() {
      assert.strictEqual(height, 1); assert.strictEqual(width, 1);
      return String(get(row, col));
    },
    getValues() {
      return Array.from({length:height}, (_,i) => Array.from({length:width}, (_,j) => get(row+i,col+j)));
    },
    getFormulas() {
      return Array.from({length:height}, (_,i) => Array.from({length:width}, (_,j) => formulas.get(key(row+i,col+j)) || ''));
    },
    setNumberFormat() { assert.strictEqual(width, 1); return this; },
    setValues(values) {
      assert.strictEqual(width, 1, 'Do not perform multi-column output operations');
      if (options.failOn && options.failOn.row === row && options.failOn.col === col) {
        throw new Error('Please make a selection within a single column to perform column level actions.');
      }
      assert.strictEqual(height, values.length);
      written.push({row, col, values});
      values.forEach((v, i) => set(row+i,col,v[0]));
      return this;
    },
    setValue(value) {
      assert.strictEqual(height, 1); assert.strictEqual(width, 1);
      written.push({row, col, values: [[value]]});
      set(row, col, value);
      return this;
    },
  });
  const sheet = {
    getLastRow: () => 5,
    getLastColumn: () => lastCol,
    getRange: makeRange,
    getMaxColumns: () => 24,
    deleteColumn: col => {
      assert.strictEqual(col, 19, 'Only trailing duplicate S may be deleted');
      assert.ok(!options.duplicateHasUnsafeData && !options.duplicateHasFormula && !options.duplicateHasUnrefreshedNumeric);
      deleted.push(col);
      for (let r = 1; r <= 5; r++) cells.delete(key(r, col));
      lastCol = 18;
    },
    insertColumnsAfter: () => { throw new Error('No columns need inserting in this fixture'); },
    setColumnWidths: () => { throw new Error('Column resizing is not needed'); },
  };
  const context = {
    console: {log(x) { logs.push(x); }, warn(x) { logs.push(x); }, error(x) { logs.push(x); }},
    Set, Date, Number, JSON,
    SpreadsheetApp: {
      getActiveSpreadsheet: () => ({getId: () => 'spreadsheet-id', getSheetByName: name => name === 'Sheet1' ? sheet : null}),
      openById: () => ({getSheetByName: () => sheet}),
      flush() {},
    },
    LockService: {getDocumentLock: () => ({tryLock: () => true, releaseLock() {}})},
    PropertiesService: {getScriptProperties: () => ({
      setProperty: () => {},
      getProperty: key => ({
        SALES_API_URL: 'https://example.com/googlesheetapi/',
        SALES_API_KEY: 'test-key',
      })[key],
    })},
    UrlFetchApp: {
      fetch(url, args) {
        const filters = JSON.parse(args.payload).rows;
        assert.deepStrictEqual(filters.map(r => r.row), [2, 3, 4], 'Row 3 must now be included');
        assert.deepStrictEqual(filters[1], {row: 3, source: 'instagram', medium: 'konnjed', campaign: 'aban', discount_code: 'konjed'});
        assert.deepStrictEqual(filters[0], {row: 2, source: 'bale', medium: 'daily', campaign: null, discount_code: null});
        assert.deepStrictEqual(filters[2], {row: 4, source: 'landing', medium: 'home', campaign: 'bf', discount_code: null});
        return {
          getResponseCode: () => 200,
          getContentText: () => JSON.stringify({
            start_date: '2026-11-16', end_date: '2026-11-23',
            matching_logic: options.legacyBackend ? undefined : 'utm_or_discount_regex_v1',
            days: options.duplicateApiDay ? DAYS.map((d, i) => ({date:d[0], jalali:i === 7 ? DAYS[6][1] : d[1]})) :
              DAYS.map(d => ({date:d[0],jalali:d[1]})),
            rows: [
              {row: 2, totals: ['1500000','0','0','0','0','0','5000','6000']},
              {row: 3, totals: ['1700000','0','0','0','0','0','25000','35000']},
              {row: 4, totals: ['0','3200000','0','0','0','0','0','0']},
            ],
          }),
        };
      },
    },
    Utilities: {formatDate: d => d.toISOString().slice(0, 10)},
  };
  vm.createContext(context);
  vm.runInContext(code, context);
  const run = () => { try {vm.runInContext('syncDailySales()', context);return undefined;} catch(e) {return e;} };
  return {run,written,logs,deleted,get};
}

// Main regression: row 3 is refreshed and S (historical duplicate) is deleted.
const good = buildMock();
assert.strictEqual(good.run(), undefined);
assert.deepStrictEqual(good.deleted, [19]);
assert.strictEqual(good.get(1, 17), '1405/09/01');
assert.strictEqual(good.get(1, 18), '1405/09/02');
assert.strictEqual(good.get(3, 11), 1700000); // no more stale 46318
assert.strictEqual(good.get(3, 17), 25000);
assert.strictEqual(good.get(3, 18), 35000);
assert.strictEqual(good.get(5, 11), '');
assert.ok(good.written.every(w => w.col >= 11)); // A:J must never be touched
assert.ok(good.logs.some(s => String(s).includes('removed 1 trailing duplicate')));
assert.strictEqual(good.run(), undefined);
assert.deepStrictEqual(good.deleted, [19], 'Repeated runs must not create/delete new columns');

// Duplicate columns with free-text, formula, or unrefreshed numeric data are preserved.
for (const caseName of ['duplicateHasUnsafeData','duplicateHasFormula','duplicateHasUnrefreshedNumeric']) {
  const unsafe = buildMock({[caseName]:true});
  assert.strictEqual(unsafe.run(), undefined);
  assert.deepStrictEqual(unsafe.deleted, []);
  assert.ok(unsafe.logs.some(s => String(s).includes('outside refreshed rows')));
}

// An old backend would ignore discount_code and return incorrect totals.
// Refuse to update the sheet rather than silently use UTM-only amounts.
const legacy = buildMock({legacyBackend:true});
assert.ok(String(legacy.run()).includes('Deploy the v4 backend first'));
assert.deepStrictEqual(legacy.written, []);

// Invalid API date mapping is rejected before writing values or headers.
const badApi = buildMock({duplicateApiDay:true});
assert.ok(String(badApi.run()).includes('duplicate Jalali date'));
assert.deepStrictEqual(badApi.written, []);

// Failure during sales write does not prematurely write any date headers.
const failed = buildMock({failOn:{row:2,col:12}});
assert.ok(String(failed.run()).includes('writing sales: column 12, rows 2-5'));
assert.ok(failed.written.every(w => w.row !== 1));
console.log('PASS: G:J discount_code payload; row 3 included; numeric-only stale S removed; unsafe columns preserved; repeat sync idempotent; invalid API rejected; no premature headers');
