import type { LottieAsset } from "./lottie-stage";
import { poseId, type Pose } from "./pose-catalog";

export const productionVersion = "2.0.0-review";
const root = `/characters/${productionVersion}`;
function asset(id: string, file: string, end: number, loop: boolean, width = 360): LottieAsset {
  return { id, animation: `${root}/${file}.json`, poster: `${root}/${file}.png`, fps: 24, start: 0, end, loop, representativeFrame: 0, width, height: 400 };
}
/** Only real editor exports belong here. Missing poses never borrow another expression. */
export const productionAssets: Readonly<Record<string, LottieAsset>> = {
  "observation-CALM": asset("observation-CALM", "observation-calm", 96, true),
  "inference-hungry": asset("inference-hungry", "ai-hungry", 96, true),
};
/** Figma source render only; no animation export exists for this pose yet. */
export const sourcePosters: Readonly<Record<string, string>> = {
  "action-holding": `${root}/action-holding-source.png`,
};
export function productionAsset(pose: Pose): LottieAsset | undefined { return productionAssets[poseId(pose)]; }
