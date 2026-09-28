import Link from "next/link";

// Public shell only. Private baby data must be fetched in the browser with the user's Bearer token.
export default function HomePage() {
  return (
    <main className="mx-auto flex min-h-svh max-w-xl flex-col justify-center gap-4 px-6 py-12">
      <p className="text-sm font-medium text-primary">아기 돌봄 도우미</p>
      <h1 className="text-3xl font-semibold tracking-tight">웹 기반 준비 중</h1>
      <p className="text-muted-foreground">
        이 페이지는 공개 화면 구조만 담고 있습니다. 실제 로그인·API 연동은 이후 작업에서 진행하며, 지금은 합성
        fixture로 화면 이동만 확인할 수 있습니다.
      </p>
      <Link
        href="/login"
        className="inline-flex h-11 w-fit items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
      >
        시작하기
      </Link>
    </main>
  );
}
