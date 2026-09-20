// A-06 ①: real analysis request/result/recovery screen. Reached from the
// READY stage of A-05's audio intake (components/audio-intake.tsx), which
// passes the same episode/audio that upload just produced. This is the real
// path — the SC04 fixture preview at /babies/{babyId}/results/{scenario}
// stays separate and never feeds this screen's state.
import Link from "next/link";
import { EmptyState } from "@/components/screen-state";
import { AnalysisScreen } from "@/components/analysis-screen";

export default async function AnalysisPage({
  params, searchParams,
}: Readonly<{
  params: Promise<{ babyId: string; episodeId: string }>;
  searchParams: Promise<{ audioId?: string }>;
}>) {
  const { babyId, episodeId } = await params;
  const { audioId } = await searchParams;

  if (!audioId) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold text-foreground">분석 결과</h1>
        <EmptyState
          label="분석할 음원을 찾을 수 없어요. 녹음 화면에서 다시 시작해 주세요."
          action={
            <Link href={`/babies/${babyId}/detect`} className="flex min-h-11 items-center rounded-md border border-border px-4 text-sm text-foreground">
              녹음 화면으로 이동
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-foreground">분석 결과</h1>
      <AnalysisScreen babyId={babyId} episodeId={episodeId} audioId={audioId} />
    </div>
  );
}
