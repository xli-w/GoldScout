import { createHash } from "node:crypto";
import { cpSync, existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const output = join(frontend, "dist");
const packaged = join(frontend, "..", "dashboard", "static");
const hash = value => createHash("sha256").update(value).digest("hex");
const files = {};
function collect(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) collect(path);
    else if (entry.isFile()) files[relative(output, path).split(sep).join("/")] = hash(readFileSync(path));
    else throw new Error("Frontend build must not contain symbolic links");
  }
}
if (!existsSync(join(output, "index.html")) || !existsSync(join(output, "assets"))) throw new Error("Frontend build is incomplete");
collect(join(output, "assets"));
files["index.html"] = hash(readFileSync(join(output, "index.html")));
writeFileSync(join(output, "dashboard-build.json"), JSON.stringify({
  version: 1, schema_sha256: hash(readFileSync(join(frontend, "..", "investment", "response-v2.schema.json"))), files,
  setup_schema_sha256: hash(readFileSync(join(frontend, "..", "dashboard", "setup.schema.json"))),
  awareness_schema_sha256: hash(readFileSync(join(frontend, "..", "investment", "awareness.schema.json"))),
  monitoring_schema_sha256: hash(readFileSync(join(frontend, "..", "investment", "monitoring.schema.json"))),
}, null, 2) + "\n");
rmSync(packaged, { recursive: true, force: true });
cpSync(output, packaged, { recursive: true });
console.log("Packaged read-only dashboard frontend with schema and asset checksums.");