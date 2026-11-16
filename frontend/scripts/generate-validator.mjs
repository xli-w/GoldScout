import { readFileSync, writeFileSync } from "node:fs";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import standaloneCode from "ajv/dist/standalone/index.js";
import { _ } from "ajv/dist/compile/codegen/index.js";

for (const [path, output] of [
  ["../../dashboard/response.schema.json", "../src/response-validator.js"],
  ["../../investment/response.schema.json", "../src/investment-validator.js"],
  ["../../investment/response-v2.schema.json", "../src/investment-validator-v2.js"],
]) {
  const schema = JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
  const ajv = new Ajv2020({ strict: false, code: { source: true, esm: true, formats: _`formats` } });
  addFormats(ajv);
  const generated = standaloneCode(ajv, ajv.compile(schema))
    .replaceAll('require("ajv/dist/runtime/ucs2length").default', "ucs2length")
    .replaceAll('require("ajv/dist/runtime/equal").default', "equal");
  if (/\brequire\s*\(/.test(generated)) throw new Error("Unsupported CommonJS helper in generated validator");
  const source = [
    'import { fullFormats as formats } from "ajv-formats/dist/formats.js";',
    'import ucs2Module from "ajv/dist/runtime/ucs2length.js";',
    'import equalModule from "ajv/dist/runtime/equal.js";',
    "const ucs2length = ucs2Module.default || ucs2Module;",
    "const equal = equalModule.default || equalModule;",
    generated,
  ].join("\n");
  writeFileSync(new URL(output, import.meta.url), source);
}