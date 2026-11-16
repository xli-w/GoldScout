import { readFileSync, writeFileSync } from "node:fs";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import standaloneCode from "ajv/dist/standalone/index.js";
import { _ } from "ajv/dist/compile/codegen/index.js";

for (const [path, output] of [
  ["../../dashboard/response.schema.json", "../src/response-validator.js"],
  ["../../investment/response.schema.json", "../src/investment-validator.js"],
]) {
  const schema = JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8"));
  const ajv = new Ajv2020({ strict: false, code: { source: true, esm: true, formats: _`formats` } });
  addFormats(ajv);
  const source = 'import { fullFormats as formats } from "ajv-formats/dist/formats.js";\n' + standaloneCode(ajv, ajv.compile(schema));
  writeFileSync(new URL(output, import.meta.url), source);
}