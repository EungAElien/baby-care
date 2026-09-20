import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ResolvedAnalysis } from "../src/components/analysis-screen";
import type { Analysis } from "../src/lib/analysis/analysis-flow";

// A-06 ①: the resolved (COMPLETE/ABSTAIN/FAILED) rendering must preserve
// model/preprocess/label-mapping versions and never show a percentage
// derived from an internal candidate score. The REAL/STUB × USER/DEMO source
// badge is rendered by the parent AnalysisScreen, not this subcomponent.
const base: Analysis = {
  analysis_id: "analysis-1", baby_id: "baby", episode_id: "episode", audio_id: "audio", created_by_user_id: "user",
  status: "RUNNING", stage: "INFERENCE", attempt_no: 1, lease_expires_at: null, quality_status: "PASS",
  quality_reasons: [], cry_detected: true, audio_candidates: [], abstain_reason: null, failure: null,
  model_version: "v1", preprocess_version: "pre-v1", label_mapping_version: "label-v1", context_snapshot: null,
  recommendation: null, inference_mode: "STUB", inference_executed: false, data_origin: "DEMO",
  recorded_at: "2026-09-20T00:00:00Z", completed_at: null,
};

describe("A-06 ① resolved analysis display", () => {
  it("shows COMPLETE candidates as plain labels, never as a percentage", () => {
    const analysis: Analysis = {
      ...base, status: "COMPLETE", stage: "FINISHED", completed_at: "2026-09-20T00:01:00Z",
      audio_candidates: [{ code: "hungry", label: "배고픔 신호", rank: 1 }],
    };
    const html = renderToStaticMarkup(<ResolvedAnalysis babyId="baby" analysis={analysis} onRetryFailed={() => {}} />);
    expect(html).toContain("배고픔 신호");
    expect(html).not.toMatch(/%/);
    expect(html).toContain("v1");
    expect(html).toContain("pre-v1");
    expect(html).toContain("label-v1");
  });

  it("shows ABSTAIN with the server's reason and no candidates", () => {
    const analysis: Analysis = {
      ...base, status: "ABSTAIN", stage: "FINISHED", completed_at: "2026-09-20T00:01:00Z",
      abstain_reason: "LOW_QUALITY", audio_candidates: [],
    };
    const html = renderToStaticMarkup(<ResolvedAnalysis babyId="baby" analysis={analysis} onRetryFailed={() => {}} />);
    expect(html).toContain("음질이 낮아 판단할 수 없어요");
    expect(html).toContain("후보를 만들지 않았어요");
  });

  it("only offers a retry action for a retryable FAILED result, and never for a non-retryable one", () => {
    const retryable: Analysis = {
      ...base, status: "FAILED", stage: "FINISHED", completed_at: "2026-09-20T00:01:00Z",
      failure: { code: "INFERENCE_ERROR", message: "crashed", retryable: true },
    };
    const notRetryable: Analysis = { ...retryable, failure: { code: "SOURCE_DELETED", message: "gone", retryable: false } };

    const retryHtml = renderToStaticMarkup(<ResolvedAnalysis babyId="baby" analysis={retryable} onRetryFailed={() => {}} />);
    expect(retryHtml).toContain("같은 분석 다시 시도하기");

    const noRetryHtml = renderToStaticMarkup(<ResolvedAnalysis babyId="baby" analysis={notRetryable} onRetryFailed={() => {}} />);
    expect(noRetryHtml).not.toContain("같은 분석 다시 시도하기");
    expect(noRetryHtml).toContain("원본 음원이 삭제됐어요");
  });
});
