/** Presentation-only identifiers. Never send these values as API action enums. */
export const observations = {
  CRYING: "우는 중", FUSSING: "칭얼거림", CALM: "편안해 보임",
  CHEERFUL_APPEARING: "즐거워 보임", AWAKE: "깨어 있음",
  SLEEPY_APPEARING: "졸려 보임", ASLEEP: "잠듦", UNKNOWN: "상태 확인 못함",
} as const;
export const inferences = {
  hungry: "배고픔 가능성", tired: "졸림·피곤함 가능성", burping: "트림 필요 가능성",
  belly_pain: "배 불편함 가능성", discomfort: "일반적인 불편함 가능성", uncertain: "판단 어려움",
} as const;
export const actions = {
  feeding: "수유함", diaper: "기저귀 갈아줌", holding: "안아줌", patting: "토닥여줌",
  burped: "트림시킴", sleeping: "재워줌", environment: "환경 바꿔줌", other: "기타 완료 조치",
} as const;
export type ObservationCode = keyof typeof observations;
export type InferenceCode = keyof typeof inferences;
export type ActionCode = keyof typeof actions;
export type Pose =
  | { kind: "observation"; code: ObservationCode }
  | { kind: "inference"; code: InferenceCode }
  | { kind: "action"; code: ActionCode };
export const assetVersion = "1.0.0-prototype";
export const neutralPose: Pose = { kind: "observation", code: "UNKNOWN" };
export function poseLabel(pose: Pose): string {
  switch (pose.kind) {
    case "observation": return observations[pose.code];
    case "inference": return inferences[pose.code];
    case "action": return actions[pose.code];
  }
}
export function poseId(pose: Pose): string { return `${pose.kind}-${pose.code}`; }
export const poseCatalog: readonly Pose[] = [
  ...Object.keys(observations).map((code) => ({ kind: "observation" as const, code: code as ObservationCode })),
  ...Object.keys(inferences).map((code) => ({ kind: "inference" as const, code: code as InferenceCode })),
  ...Object.keys(actions).map((code) => ({ kind: "action" as const, code: code as ActionCode })),
];
export function posterPath(pose: Pose): string { return `/characters/${assetVersion}/${poseId(pose)}.svg`; }
export const palette = { ivory: "#FFF6E7", ink: "#232B43", cobalt: "#4169C6", skin: "#F1BE96", ochre: "#E8BA63", sage: "#8AAB9A", coral: "#E9786B" };
