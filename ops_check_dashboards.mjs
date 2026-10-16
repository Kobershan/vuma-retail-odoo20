#!/usr/bin/env node
/**
 * Import every dashboard through the real o-spreadsheet engine, headless.
 *
 * Odoo only validates a spreadsheet's models and fields server-side. Anything
 * about the document's *shape* -- an unknown chart type, a malformed cell range,
 * a style map keyed by something that is not a range -- is only caught when a
 * browser builds the model, and it takes the whole dashboard down when it
 * does. This runs that same code path before the dashboards are ever deployed.
 *
 *     node ops_check_dashboards.mjs
 *
 * Pass a directory to check something other than the module's own dashboards --
 * a dump of spreadsheet_dashboard.spreadsheet_data, for instance, to confirm
 * what the database is really serving.
 *
 * It builds a throwaway module tree around the o-spreadsheet bundle that ships
 * with this Odoo checkout, so the engine under test is the one the server
 * actually serves.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OSPREADSHEET =
  path.join(HERE, "odoo", "addons", "spreadsheet", "static", "src", "o_spreadsheet");
const DASHBOARDS = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(
      HERE, "custom-addons", "cognify_management_reports", "data", "files");

if (!fs.existsSync(OSPREADSHEET)) {
  console.error(`cannot find the o-spreadsheet bundle at ${OSPREADSHEET}`);
  process.exit(2);
}

// -- scaffold a module tree around the bundle -------------------------------

const root = fs.mkdtempSync(path.join(os.tmpdir(), "sheet-check-"));
const pkg = path.join(root, "node_modules", "@odoo", "o-spreadsheet");
fs.mkdirSync(pkg, { recursive: true });

fs.copyFileSync(
  path.join(OSPREADSHEET, "o_spreadsheet.js"),
  path.join(pkg, "o_spreadsheet.cjs")
);

fs.writeFileSync(
  path.join(pkg, "package.json"),
  JSON.stringify({ name: "@odoo/o-spreadsheet", type: "module", main: "index.js" })
);

// o-spreadsheet is shipped as an AMD module that takes a `require` injected as
// its first factory argument, so the loader shim has to pass one through.
fs.writeFileSync(
  path.join(pkg, "index.js"),
  `
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
`
);

// o-spreadsheet destructures owl at module scope and calls a few of its exports
// while loading. None of it renders, so generic stand-ins are enough.
fs.writeFileSync(
  path.join(pkg, "owl_stub.cjs"),
  `
const noop = () => {};
const TYPE_NAMES = [
  "string", "number", "boolean", "any", "array", "object", "optional",
  "or", "union", "enum", "lazy", "instanceOf", "function",
  "validateObject", "validateFunction", "validateArrayOf", "validateRecordOf",
  "validateString", "validateNumber", "validateBoolean", "validateDate",
  "validateTime", "validateInteger", "validateColor", "validateSet",
];
// Validators chain (.optional(), .or(), ...), so hand back a chainable stub.
const typeStub = Object.fromEntries(
  TYPE_NAMES.map((name) => [
    name,
    () => new Proxy(function check() {}, { get: (t, k) => (k in t ? t[k] : () => t) }),
  ])
);
const real = {
  whenReady: (fn) => { if (typeof fn === "function") fn(); return Promise.resolve(); },
  status: () => ({}),
  types: typeStub,
  useConfig: () => ({}),
  useEffect: noop, useListener: noop, usePlugin: noop, useProps: () => ({}),
  onMounted: noop, onPatched: noop, onWillPatch: noop, onWillStart: noop,
  onWillUnmount: noop, onWillUpdateProps: noop, providePlugins: noop,
  blockDom: Object.assign((node) => node, { text: () => new (class {})() }),
  markRaw: (v) => v, toRaw: (v) => v, reactive: (v) => v,
  signal: (v) => ({ value: v }), props: () => ({}), field: () => undefined,
  proxy: {},
};
const fallback = new Map();
const anyKey = (k) => {
  if (!fallback.has(k)) fallback.set(k, /^[A-Z]/.test(k) ? class {} : noop);
  return fallback.get(k);
};
module.exports = new Proxy(real, {
  get: (t, k) => (typeof k === "string" && k in t ? t[k] : anyKey(k)),
  has: () => true,
});
`
);

// -- the Odoo-side registrations the import path needs ----------------------

fs.writeFileSync(
  path.join(pkg, "odoo_env.mjs"),
  `
import spreadsheet from "./index.js";
const { registries } = spreadsheet;

// Mirrors the parts of Odoo's pivot and chart registrations that the import
// path touches. Copied from addons/spreadsheet/static/src/pivot/odoo_pivot.js
// and .../chart/odoo_chart/odoo_chart_datasource.js; only the data-fetching
// halves are dropped, since those only run when a chart is drawn.
const { chartDataSourceRegistry, pivotRegistry, functionRegistry } = registries;

const CHART_TYPES = [
  "bar", "combo", "line", "pie", "scorecard", "gauge", "scatter",
  "waterfall", "pyramid", "radar", "geo", "funnel", "sunburst",
  "treemap", "calendar", "bubble",
];
const EXCLUDED_CHART_TYPES = ["scorecard", "gauge", "calendar", "bubble"];

chartDataSourceRegistry.add("odoo", {
  supportedChartTypes: CHART_TYPES.filter((t) => !EXCLUDED_CHART_TYPES.includes(t)),
  fromExternalDefinition: (dataSource) => dataSource,
  validate: () => "Success",
  transform: (dataSource) => dataSource,
  adaptRanges: (dataSource) => dataSource,
  getDefinition: (dataSource) => dataSource,
});

class OdooPivot {
  constructor(custom, params) { this.custom = custom; this.params = params; }
  onDefinitionChange() {}
  markAsDirtyForEvaluation() {}
}

pivotRegistry.add("ODOO", {
  ui: OdooPivot,
  externalData: true,
  onIterationEndEvaluation: () => {},
  dateGranularities: ["year", "quarter", "month", "week", "day"],
  isMeasureCandidate: () => true,
  canHaveCustomGroup: () => true,
  isGroupable: () => true,
  isPivotUnused: () => false,
});

for (const name of ["PIVOT.VALUE", "PIVOT.HEADER", "PIVOT.TOTAL", "ODOO.CURRENCY.RATE", "_t"]) {
  try {
    functionRegistry.add(name, {
      description: "", args: [], compute: () => ({ value: 0 }),
      returns: ["NUMBER"], hidden: true,
    });
  } catch {
    // already registered by the bundle
  }
}
`
);

// -- check ------------------------------------------------------------------

const { default: spreadsheet } = await import(
  pathToFileURL(path.join(pkg, "index.js")).href
);
const { Model, __info__ } = spreadsheet;
await import(pathToFileURL(path.join(pkg, "odoo_env.mjs")).href);

// o-spreadsheet narrates its import on stdout; keep the report readable.
const report = console.log;
for (const method of ["log", "info", "debug", "warn"]) console[method] = () => {};

const files = fs
  .readdirSync(DASHBOARDS)
  .filter((f) => f.endsWith(".json"))
  .sort();

let failures = 0;
report(`o-spreadsheet ${__info__.version}`);

for (const file of files) {
  const data = JSON.parse(fs.readFileSync(path.join(DASHBOARDS, file), "utf8"));
  const charts = data.sheets.flatMap((s) => s.figures || []).filter((f) => f.tag === "chart");
  try {
    new Model(data);
    report(`  ok    ${file.padEnd(26)} ${String(charts.length).padStart(2)} charts`);
  } catch (error) {
    failures += 1;
    const where = (error.stack || "")
      .split("\n")
      .filter((l) => l.includes("o_spreadsheet.cjs"))
      .slice(0, 4)
      .map((l) => l.trim().replace(/.*o_spreadsheet\.cjs:/, ""))
      .join(" <- ");
    report(`  FAIL  ${file.padEnd(26)} ${error.message}`);
    report(`        ${where}`);
  }
}

report(
  failures
    ? `\n${failures} of ${files.length} dashboard(s) cannot be imported`
    : `\nall ${files.length} dashboards import cleanly`
);

fs.rmSync(root, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
