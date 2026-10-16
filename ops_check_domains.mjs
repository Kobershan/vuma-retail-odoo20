// Evaluate every dashboard domain with Odoo's own py interpreter.
//
// A pivot's and a chart's domain is stored as a *string* that the browser parses
// and evaluates with @web/core/py_js. Nothing else in the stack touches it: the
// document imports, the action resolves, the model has the fields, and the
// read_group behind it works -- and the tile or graph still renders an error,
// because the one line that decides it never ran until the page did.
//
// So this copies Odoo's py_js out of the addon, runs it under node, and
// evaluates every domain in data/files/*.json exactly as the browser would,
// with the engine's own builtins and none of my own.
//
//     node ops_check_domains.mjs

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const HERE = "/home/kob/Documents/vuma-retail";
const FILES = path.join(
  HERE, "custom-addons", "cognify_management_reports", "data", "files");
const PY_SRC = path.join(HERE, "odoo", "addons", "web", "static", "src", "core", "py_js");

// py_js is authored for a bundler, so its relative imports carry no extension.
// node needs them, and copying rather than editing keeps the addon untouched.
const root = fs.mkdtempSync(path.join(os.tmpdir(), "py-domain-check-"));
for (const file of fs.readdirSync(PY_SRC)) {
  if (!file.endsWith(".js")) continue;
  const source = fs
    .readFileSync(path.join(PY_SRC, file), "utf8")
    .replace(/from "\.\/(py_[a-z_]+)"/g, 'from "./$1.js"');
  fs.writeFileSync(path.join(root, file), source);
}
fs.writeFileSync(path.join(root, "package.json"), '{"type":"module"}');

const { evaluateExpr } = await import(
  path.join(root, "py.js").replace(/^/, "file://"));

let checked = 0;
let failures = 0;

for (const file of fs.readdirSync(FILES).filter((f) => f.endsWith(".json")).sort()) {
  const data = JSON.parse(fs.readFileSync(path.join(FILES, file), "utf8"));
  const cases = [];

  for (const [id, pivot] of Object.entries(data.pivots || {})) {
    cases.push([`pivot ${id} "${pivot.name ?? ""}"`, pivot.domain ?? "[]"]);
  }
  for (const sheet of data.sheets || []) {
    for (const figure of sheet.figures ?? []) {
      const source = figure?.data?.dataSource;
      if (source?.type === "odoo") {
        cases.push([
          `chart ${figure.id} "${figure.data?.title?.text ?? ""}"`,
          source.searchParams?.domain ?? "[]",
        ]);
      }
    }
  }

  console.log(`\n[${file}]`);
  for (const [where, domain] of cases) {
    checked += 1;
    try {
      const value = evaluateExpr(domain, {});
      // The value has to be a usable domain, not merely something that ran.
      if (!Array.isArray(value)) {
        failures += 1;
        console.log(`  FAIL  ${where} -> not a domain list: ${JSON.stringify(value)}`);
      } else {
        console.log(`  ok    ${where} -> ${JSON.stringify(value).slice(0, 90)}`);
      }
    } catch (error) {
      failures += 1;
      console.log(`  FAIL  ${where} -> ${String(error.message).replace(/\n/g, " | ")}`);
      console.log(`        ${domain}`);
    }
  }
}

console.log(
  `\n${checked - failures}/${checked} domains evaluate, ${failures} fail`);
fs.rmSync(root, { recursive: true, force: true });
process.exit(failures ? 1 : 0);
