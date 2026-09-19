// Copies the team contract's synthetic fixture verbatim into the web app so
// mock screens can import it without reaching outside apps/web at build time.
// Source of truth stays contracts/목 응답과 시험 사용자 배치.json; re-run this
// script after B updates that file.
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const sourcePath = resolve(process.cwd(), "../../contracts/목 응답과 시험 사용자 배치.json");
const targetPath = resolve(process.cwd(), "src/lib/mock/fixtures.json");

const raw = readFileSync(sourcePath, "utf8");
JSON.parse(raw); // fail fast on malformed contract fixture
writeFileSync(targetPath, raw, "utf8");

console.log(`Copied ${sourcePath} -> ${targetPath}`);
