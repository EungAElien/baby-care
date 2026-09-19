// Public shell only. Private baby data must be fetched in the browser with the user's Bearer token.
export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6 py-12">
      <p className="text-sm font-medium text-primary">아기 돌봄 도우미</p>
      <h1 className="text-3xl font-semibold tracking-tight">웹 기반 준비 중</h1>
      <p className="text-muted-foreground">
        이 페이지는 공개 화면 구조만 담고 있습니다. 로그인·아기별 화면과 실제 자료 연결은 다음 작업에서 진행합니다.
      </p>
    </main>
  );
}
