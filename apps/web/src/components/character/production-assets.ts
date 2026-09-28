import type { LottieAsset } from "./lottie-stage";
import { poseId, type Pose } from "./pose-catalog";

export const productionVersion = "2.0.0-review";
const root = `/characters/${productionVersion}`;
function asset(id: string, file: string, end: number, loop: boolean, width = 360, representativeFrame = 0): LottieAsset {
  return { id, animation: `${root}/${file}.json`, poster: `${root}/${file}.png`, fps: 24, start: 0, end, loop, representativeFrame, width, height: 400 };
}
/** Only real editor exports belong here. Missing poses never borrow another expression. */
export const productionAssets: Readonly<Record<string, LottieAsset>> = {
  "observation-CALM": asset("observation-CALM", "observation-calm", 96, true),
  "inference-hungry": asset("inference-hungry", "ai-hungry", 96, true),
  "inference-tired": asset("inference-tired", "ai-tired", 96, true, 360, 34),
  "inference-burping": asset("inference-burping", "ai-burping", 96, true),
  "inference-belly_pain": asset("inference-belly_pain", "ai-belly-pain", 96, true),
  "inference-discomfort": asset("inference-discomfort", "ai-discomfort", 96, true),
  "inference-uncertain": { ...asset("inference-uncertain", "ai-uncertain", 24, false), staticPose: true },
  "action-holding": asset("action-holding", "action-holding", 53, false, 400),
};
/** Source-only poses can be listed here while editor export is pending. */
export const sourcePosters: Readonly<Record<string, string>> = {};
export function productionAsset(pose: Pose): LottieAsset | undefined { return productionAssets[poseId(pose)]; }
