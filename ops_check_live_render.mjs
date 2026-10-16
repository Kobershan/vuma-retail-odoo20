import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { execFileSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = '/home/kob/Documents/vuma-retail';
const OSPREADSHEET = path.join(
  HERE, "odoo", "addons", "spreadsheet", "static", "src", "o_spreadsheet");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "render-check-"));
const pkg = path.join(root, "node_modules", "@odoo", "o-spreadsheet");
fs.mkdirSync(pkg, { recursive: true });
fs.copyFileSync(
  path.join(OSPREADSHEET, "o_spreadsheet.js"),
  path.join(pkg, "o_spreadsheet.cjs")
);
fs.writeFileSync(path.join(pkg, "package.json"),
  JSON.stringify({ name: "@odoo/o-spreadsheet", type: "module", main: "index.js" }));
fs.writeFileSync(path.join(pkg, "index.js"), `
import { createRequire } from "node:module";
const nodeRequire = createRequire(import.meta.url);
const modules = {};
const amdRequire = (name) => {
  if (!(name in modules)) throw new Error("module not registered: " + name);
  return modules[name];
};
globalThis.odoo = {
  define(name, deps, factory) {
    if (typeof deps === "function") { factory = deps; deps = []; }
    modules[name] = factory(amdRequire, ...deps.map(amdRequire));
  },
};
modules["@odoo/owl"] = nodeRequire("./owl_stub.cjs");
nodeRequire("./o_spreadsheet.cjs");
export default modules["@spreadsheet/o_spreadsheet/o_spreadsheet"];
`);
fs.writeFileSync(path.join(pkg, "owl_stub.cjs"), `
const noop = () => {};
const TYPE_NAMES = ["string","number","boolean","any","array","object","optional","or","union","enum","lazy","instanceOf","function","validateObject","validateFunction","validateArrayOf","validateRecordOf","validateString","validateNumber","validateBoolean","validateDate","validateTime","validateInteger","validateColor","validateSet"];
const typeStub = Object.fromEntries(TYPE_NAMES.map((name) => [name, () => new Proxy(function check(){}, { get: (t,k) => (k in t ? t[k] : () => t) })]));
const real = { whenReady: (fn) => { if (typeof fn === "function") fn(); return Promise.resolve(); }, status: () => ({}), types: typeStub, useConfig: () => ({}), useEffect: noop, useListener: noop, usePlugin: noop, useProps: () => ({}), onMounted: noop, onPatched: noop, onWillPatch: noop, onWillStart: noop, onWillUnmount: noop, onWillUpdateProps: noop, providePlugins: noop, blockDom: Object.assign((node) => node, { text: () => new (class {})() }), markRaw: (v) => v, toRaw: (v) => v, reactive: (v) => v, signal: (v) => ({ value: v }), props: () => ({}), field: () => undefined, proxy: {} };
const fallback = new Map(); const anyKey = (k) => { if (!fallback.has(k)) fallback.set(k, /^[A-Z]/.test(k) ? class {} : noop); return fallback.get(k); };
module.exports = new Proxy(real, { get: (t, k) => (typeof k === "string" && k in t ? t[k] : anyKey(k)), has: () => true });
`);
fs.writeFileSync(path.join(pkg, "odoo_env.mjs"), `
import spreadsheet from "./index.js";
const { chartDataSourceRegistry, pivotRegistry, functionRegistry } = spreadsheet.registries;
const CHART_TYPES = ["bar","combo","line","pie","scorecard","gauge","scatter","waterfall","pyramid","radar","geo","funnel","sunburst","treemap","calendar","bubble"];
const EXCLUDED = ["scorecard","gauge","calendar","bubble"];
chartDataSourceRegistry.add("odoo", { supportedChartTypes: CHART_TYPES.filter((t) => !EXCLUDED.includes(t)), fromExternalDefinition: (ds) => ds, validate: () => "Success", transform: (ds) => ds, adaptRanges: (ds) => ds, getDefinition: (ds) => ds, duplicateInDuplicatedSheet: (ds) => ds, getContextCreation: (ds) => ({ dataSource: ds }), getHierarchicalContextCreation: (ds) => ({ dataSource: ds }), toExcelDataSets: () => undefined, extractData: () => ({ dataSetsValues: [], labelValues: [] }), extractHierarchicalData: () => ({ dataSetsValues: [], labelValues: [] }), onDataSetHover: () => {}, onDataSetClick: () => {} });
// The engine calls init() on every registered pivot class as it evaluates the
// sheet (see PIVOT.VALUE). Odoo's real OdooPivot defines it at
// spreadsheet/static/src/pivot/odoo_pivot.js:188; without it here every KPI cell
// throws "pivot.init is not a function", which buries any genuine failure under
// a wall of identical noise.
// The engine drives a pivot class through a small lifecycle: init() when it
// starts evaluating, load() to fetch, assertIsValid() to decide whether PIVOT.VALUE
// may return a figure. Odoo's real OdooPivot defines all three
// (spreadsheet/static/src/pivot/odoo_pivot.js:188-206 and its loader), so the
// stub does too -- otherwise every KPI cell throws and the noise buries any
// genuine failure.
class OdooPivot {
  constructor(custom, params) {
    this.custom = custom;
    this.params = params;
    // getDefinitionForPivotModel() maps over this.measures, so it has to exist
    // even in the stub or every KPI cell throws on the read.
    this.measures = params?.definition?.measures ?? [];
    this.model = params?.definition?.model;
    this.columns = params?.definition?.columns ?? [];
    this.rows = params?.definition?.rows ?? [];
    this.context = params?.definition?.context ?? {};
    this.domain = params?.definition?.domain ?? "[]";
  }
  // The engine calls this whole set on a pivot while it evaluates the sheet.
  // Odoo's real OdooPivot defines each of them (see the method list in
  // spreadsheet/static/src/pivot/odoo_pivot.js), so the stub mirrors the full
  // surface rather than growing one method per failure: with any one of them
  // missing, every KPI cell throws and the resulting wall of identical
  // TypeErrors buries whatever the render probe was actually meant to catch.
  //
  // The harness holds no field metadata and no server data, so the values are
  // neutral rather than right: this probe is about document shape and the
  // render path, not about figures. ops_check_chart_data.py is what checks the
  // data actually loads, against the running server.
  init() {}
  load() { return Promise.resolve(); }
  loadMetadata() { return Promise.resolve(); }
  _load() { return Promise.resolve(); }
  _loadPropertiesDefinitions() { return Promise.resolve(); }
  _loadRelationalFieldsDefinitions() { return Promise.resolve(); }
  assertIsValid() {}
  isValid() { return true; }
  isModelValid() { return true; }
  areDomainArgsFieldsValid() { return true; }
  parseArgsToPivotDomain() { return []; }
  parseGroupField(name) { return { fieldName: String(name).split(":")[0] }; }
  getDomainWithGlobalFilters() { return "[]"; }
  addGlobalFilterDomain() {}
  getCollapsedTableStructure() { return { cols: [], rows: [] }; }
  getExpandedTableStructure() { return { cols: [], rows: [] }; }
  getPivotCellValueAndFormat() { return { value: 0, format: undefined }; }
  getPivotHeaderValueAndFormat() { return { value: "", format: undefined }; }
  getPivotCellValue() { return 0; }
  getPivotCellDomain() { return []; }
  getPivotMeasureValue() { return 0; }
  getLastPivotGroupValue() { return ""; }
  // The engine reads measure.computedBy off whatever this returns, so it has to
  // be a measure-shaped object rather than undefined.
  getMeasure(name) {
    return this.measures.find((measure) => measure.id === name)
      ?? { id: name, fieldName: name, computedBy: undefined };
  }
  getFields() { return {}; }
  getModelLabel() { return ""; }
  getPossibleFieldValues() { return []; }
  getDefinitionForPivotModel() { return { searchParams: {}, metaData: {} }; }
  createModelAndDefinition() { return { model: undefined, definition: undefined }; }
  copyModelWithOriginalDomain() {}
  isMeasuresChangesRequireRPC() { return false; }
  _getPivotFieldFormat() { return undefined; }
  onDefinitionChange() {}
  markAsDirtyForEvaluation() {}
}
pivotRegistry.add("ODOO", { ui: OdooPivot, externalData: true, onIterationEndEvaluation: () => {}, dateGranularities: ["year","quarter","month","week","day"], isMeasureCandidate: () => true, canHaveCustomGroup: () => true, isGroupable: () => true, isPivotUnused: () => false });
for (const name of ["PIVOT.VALUE","PIVOT.HEADER","PIVOT.TOTAL","ODOO.CURRENCY.RATE","_t"]) { try { functionRegistry.add(name, { description:"", args:[], compute: () => ({value:0}), returns:["NUMBER"], hidden:true }); } catch {} }
`);

const { default: spreadsheet } = await import(pathToFileURL(path.join(pkg, "index.js")).href);
const { Model } = spreadsheet;
await import(pathToFileURL(path.join(pkg, "odoo_env.mjs")).href);

for (const m of ["log", "info", "debug", "warn"]) console[m] = () => {};

function renderProbe(model) {
  const g = model.getters;
  const out = { figures: 0, charts: 0 };
  for (const sheetId of g.getSheetIds()) {
    for (const figure of g.getFigures(sheetId)) {
      g.getFigureUI(sheetId, figure);
      out.figures += 1;
      if (figure.tag === "chart") {
        g.getChartDefinition(figure.data.chartId);
        g.getChartRuntime(figure.data.chartId);
        out.charts += 1;
      }
    }
  }
  return out;
}

const DIR = process.argv[2] ?? path.join(HERE, "custom-addons", "cognify_management_reports", "data", "files");

let failures = 0;
for (const file of fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).sort()) {
  const payload = JSON.parse(fs.readFileSync(path.join(DIR, file), "utf8"));
  const doc = payload.snapshot ?? payload.data ?? payload;
  try {
    const model = new Model(doc);
    const probe = renderProbe(model);
    console.error(`  ok    ${file.padEnd(26)} ${probe.figures} figures, ${probe.charts} charts rendered`);
  } catch (error) {
    failures += 1;
    const where = (error.stack || "").split("\n").filter((l) => l.includes("o_spreadsheet.cjs")).slice(0, 5).map((l) => l.trim().replace(/.*o_spreadsheet\.cjs:/, "")).join(" <- ");
    console.error(`  FAIL  ${file.padEnd(26)} ${error.message}`);
    console.error(`        ${where}`);
  }
}
console.error(failures ? `\n${failures} dashboard(s) broke on the render path` : `\nall dashboards survive the render path`);
fs.rmSync(root, { recursive: true, force: true });
process.exit(failures ? 1 : 0);