import { mkdtemp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { createHash } from "node:crypto";
import ts from "typescript";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

// Compile the same source used by the player; do not maintain a second drawing implementation.
const root = resolve(import.meta.dirname, "..");
const temporary = await mkdtemp(join(root, ".character-export-"));
const check = process.argv.includes("--check");
try {
  for (const name of ["pose-catalog.ts", "baby-figure.tsx", "caregiver-figure.tsx", "character-art.tsx"]) {
    const source = await readFile(join(root, "src/components/character", name), "utf8");
    const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.ReactJSX } }).outputText;
    await writeFile(join(temporary, name.replace(/\.tsx?$/, ".mjs")), compiled.replace(/from "\.\/([^".]+)"/g, 'from "./$1.mjs"'));
  }
  const { CharacterArt } = await import(pathToFileURL(join(temporary, "character-art.mjs")));
  const { poseCatalog, poseId, poseLabel, assetVersion } = await import(pathToFileURL(join(temporary, "pose-catalog.mjs")));
  const output = join(root, "public/characters", assetVersion);
  if (!check) await mkdir(output, { recursive: true });
  const assets = [];
  const save = async (name, content) => {
    const path = join(output, name);
    if (check) {
      if (await readFile(path, "utf8") !== content) throw new Error(`Stale character artifact: ${name}`);
    } else await writeFile(path, content);
  };
  for (const pose of poseCatalog) {
    const svg = `${renderToStaticMarkup(createElement(CharacterArt, { pose }))}\n`;
    const id = poseId(pose);
    await save(`${id}.svg`, svg);
    assets.push({ id, version: assetVersion, pose, poster: `/characters/${assetVersion}/${id}.svg`, alt: poseLabel(pose),
      source: "src/components/character/character-art.tsx", motion: "src/components/character/character.css + character-scene.tsx",
      representativePose: "base SVG before animation", allowedInput: pose.kind === "action" ? "Explicitly confirmed completed action; sleeping/burped also require observed result" : pose.kind === "inference" ? "Current COMPLETE supported cause; uncertain for abstention only" : "Confirmed observation, preserving source and time",
      sha256: createHash("sha256").update(svg).digest("hex") });
  }
  await save("manifest.json", `${JSON.stringify({ version: assetVersion, status: "design prototype; no production API mapping", origin: "Original SVG paths reconstructed from repository concept sheets; no external artwork copied", assets }, null, 2)}\n`);
  console.log(`${check ? "Verified" : "Exported"} ${assets.length} static SVG posters and manifest.`);
} finally {
  await rm(temporary, { recursive: true, force: true });
}
