// Display-only text for the real A-06 analysis screen. Never derives a label
// from an internal candidate score — 개발계약 §6 "내부 후보 점수는 실제 원인
// 확률처럼 화면에 백분율로 표시하지 않는다".
export const analysisStageLabel: Record<string, string> = {
  READY: "대기 중",
  QUALITY_CHECK: "음질 확인 중",
  INFERENCE: "모델 분석 중",
  CONTEXT: "맥락 반영 중",
  PERSISTING: "결과 저장 중",
  FINISHED: "완료",
};

export const abstainReasonLabel: Record<string, string> = {
  NO_CRY: "울음이 확인되지 않았어요",
  LOW_QUALITY: "음질이 낮아 판단할 수 없어요",
  INSUFFICIENT_AUDIO: "입력이 너무 짧아요",
  LOW_CONFIDENCE: "확신할 수 있는 후보가 없어요",
  UNSUPPORTED_SCOPE: "아직 지원하지 않는 상황이에요",
};

export const failureCodeLabel: Record<string, string> = {
  ANALYSIS_TIMEOUT: "분석이 제한 시간 안에 끝나지 않았어요",
  ANALYSIS_LEASE_EXPIRED: "분석 실행이 만료됐어요",
  INFERENCE_ERROR: "모델 실행 중 오류가 발생했어요",
  CLEANUP_FAILED: "후처리 중 오류가 발생했어요",
  SOURCE_DELETED: "원본 음원이 삭제됐어요",
  ACCESS_REVOKED: "이 아기에 대한 접근 권한이 사라졌어요",
};

export const qualityReasonLabel: Record<string, string> = {
  TOO_SHORT: "길이가 너무 짧아요",
  SILENCE: "소리가 거의 없어요",
  CLIPPING: "소리가 잘렸을 수 있어요",
  HIGH_NOISE: "잡음이 많아요",
  NO_CRY: "울음으로 확인되지 않았어요",
  UNSUPPORTED_CODEC: "지원하지 않는 형식이에요",
  DECODE_ERROR: "음원을 읽을 수 없었어요",
  TOO_LONG: "길이가 너무 길어요",
  TOO_LARGE: "파일이 너무 커요",
};
