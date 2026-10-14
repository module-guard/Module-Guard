import { NextResponse, type NextRequest } from "next/server";
export function middleware(request: NextRequest) {
  if (process.env.NEXT_PUBLIC_ENABLE_TEST_UI === "true")
    return NextResponse.next();
  // 로그인 없이 바로 검사하는 화면은 개발 모드 전용이다. 운영에서는 올리기로 보낸다.
  if (request.nextUrl.pathname === "/scan")
    return NextResponse.redirect(new URL("/new", request.url));
  return new NextResponse("Not found", { status: 404 });
}
export const config = { matcher: ["/dev/:path*", "/scan"] };
