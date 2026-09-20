import { BabyFigure, type BabyMood } from "./baby-figure";
import { CaregiverFigure } from "./caregiver-figure";
import { type ObservationCode, type Pose, palette, poseId, poseLabel } from "./pose-catalog";

const moods: Record<ObservationCode, BabyMood> = {
  CRYING: "crying", FUSSING: "fussing", CALM: "calm", CHEERFUL_APPEARING: "cheerful",
  AWAKE: "awake", SLEEPY_APPEARING: "sleepy", ASLEEP: "asleep", UNKNOWN: "neutral",
};

/** Static by default: the caller must explicitly enable idle motion. No SVG IDs to collide. */
export function CharacterArt({ pose, decorative = false, animated = false }: {
  pose: Pose; decorative?: boolean; animated?: boolean;
}) {
  const mood: BabyMood = pose.kind === "observation" ? moods[pose.code]
    : pose.kind === "inference" && pose.code !== "uncertain" ? pose.code : "neutral";
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 300" fill="none"
    role={decorative ? undefined : "img"} aria-hidden={decorative || undefined}
    aria-label={decorative ? undefined : poseLabel(pose)} focusable="false"
    className="character-art" data-pose={poseId(pose)} data-idle={animated ? "true" : "false"}
    style={{ display: "block", width: "100%", height: "auto", background: palette.ivory }}>
    {pose.kind === "action" ? <CaregiverFigure action={pose.code} />
      : <BabyFigure mood={mood} lying={mood === "asleep"} />}
    {pose.kind === "observation" && pose.code === "UNKNOWN" && <g fill={palette.ink}>
      <circle cx="249" cy="96" r="17" fill="none" stroke={palette.sage} strokeWidth="3" />
      <path d="M244 92 C244 84 257 84 256 92 Q256 95 250 97 L250 100" stroke={palette.ink} strokeWidth="3" strokeLinecap="round" />
      <circle cx="250" cy="105" r="1.8" />
    </g>}
  </svg>;
}
