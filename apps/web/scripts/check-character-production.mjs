import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, access } from 'node:fs/promises';

const publicRoot = new URL('../public/characters/2.0.0-review/', import.meta.url);
const sourceRoot = new URL('../../../docs/design/character-motion/production-v2/', import.meta.url);
const read = async (url) => JSON.parse(await readFile(url, 'utf8'));
const manifest = await read(new URL('manifest.json', publicRoot));
assert.equal(manifest.assets.length, 22);
assert.equal(new Set(manifest.assets.map((asset) => asset.id)).size, 22);
assert.deepEqual(manifest, await read(new URL('manifest.json', sourceRoot)));
let exported = 0;
for (const asset of manifest.assets) {
  if (asset.source) {
    const source = await readFile(new URL(asset.source, sourceRoot));
    assert.equal(createHash('sha256').update(source).digest('hex'), asset.sourceSha256);
  }
  if (asset.poster) {
    await access(new URL(asset.poster, publicRoot));
    for (const size of asset.logSizes) await access(new URL(`log/${asset.file}-${size}.png`, publicRoot));
  }
  if (!asset.animation) continue;
  exported += 1;
  const bytes = await readFile(new URL(asset.animation, publicRoot));
  assert.equal(createHash('sha256').update(bytes).digest('hex'), asset.sha256);
  assert.deepEqual(bytes, await readFile(new URL(`exports/${asset.animation}`, sourceRoot)));
  const animation = JSON.parse(bytes.toString());
  assert.equal(animation.fr, asset.fps);
  assert.deepEqual([animation.ip, animation.op], asset.segment);
  assert.deepEqual([animation.w, animation.h], asset.canvas);
  assert.ok(animation.layers.length > 0);
  const compositions = new Map(animation.assets.map((item) => [item.id, item]));
  let animatedLayers = 0;
  const visited = new Set();
  function visit(layers) {
    for (const layer of layers) {
      if (Object.values(layer.ks ?? {}).some((property) => property?.a === 1)) animatedLayers += 1;
      if (layer.refId && !visited.has(layer.refId)) {
        visited.add(layer.refId);
        const composition = compositions.get(layer.refId);
        assert.ok(composition, `Missing composition ${layer.refId}`);
        assert.ok(!composition.p && !composition.u, 'Unexpected external image asset');
        visit(composition.layers ?? []);
      }
    }
  }
  visit(animation.layers);
  if (asset.motionPolicy === 'static-neutral') {
    assert.equal(asset.id, 'inference-uncertain');
    assert.equal(animatedLayers, 0, 'The uncertain pose must remain neutral and still');
    assert.equal(asset.loop, false);
  } else assert.ok(animatedLayers > 1, 'Expected body-part animation, not a single scene transform');
  console.log(`${asset.id}: ${animatedLayers} animated layers; editor export hash verified`);
}
assert.equal(exported, manifest.exported);
console.log(`${exported} actual exports verified; visual review remains a separate check.`);
