"use client";
import { useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import * as Menu from "@radix-ui/react-dropdown-menu";
import {
  Github,
  House,
  LogOut,
  Menu as MenuIcon,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { loginUrl, useAuth, type Me } from "./auth";
import { Logo } from "./logo";
import { StatusDot } from "./status-dot";
import { Tooltip } from "./ui/tooltip";
const devMode = process.env.NEXT_PUBLIC_ENABLE_TEST_UI === "true";
const items = (signedIn: boolean) => [
  { href: "/explore", title: "탐색", match: ["/explore"] },
  ...(signedIn ? [{ href: "/home", title: "내 홈", match: ["/home"] }] : []),
  // 로그인 없는 직접 검사와 개발 검증 화면은 개발 모드에서만 보인다.
  ...(devMode
    ? [
        { href: "/scan", title: "새 검사", match: ["/scan", "/scans"] },
        { href: "/dev/analysis-test", title: "개발 검증", match: ["/dev"] },
      ]
    : []),
];
function Avatar({ user }: { user: Me }) {
  return user.avatar_url ? (
    <Image
      src={user.avatar_url}
      alt=""
      width={32}
      height={32}
      className="h-8 w-8 rounded-full"
    />
  ) : (
    <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[#eeebff] text-xs font-bold uppercase text-brand">
      {user.login.slice(0, 2)}
    </span>
  );
}
function Account() {
  const { user, loginAvailable, logout } = useAuth();
  const path = usePathname();
  // 확인 중에는 자리를 비워 두어 버튼이 깜빡이지 않게 한다.
  if (user === undefined) return <span className="h-8 w-8" />;
  if (user === null && !loginAvailable)
    return (
      <Tooltip content="서버에 GitHub 로그인 설정(GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, SESSION_SECRET)이 없습니다.">
        <span tabIndex={0} className="whitespace-nowrap text-xs text-slate-400">
          로그인을 사용할 수 없습니다
        </span>
      </Tooltip>
    );
  if (user === null)
    return (
      <a
        href={loginUrl(path)}
        className="flex h-9 items-center gap-2 whitespace-nowrap rounded-lg bg-navy px-3.5 text-sm font-semibold text-white hover:bg-[#22346b]"
      >
        <Github size={16} />
        <span>
          <span className="max-sm:hidden">GitHub로 </span>로그인
        </span>
      </a>
    );
  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={`계정 메뉴: ${user.login}`}
        className="rounded-full ring-1 ring-line"
      >
        <Avatar user={user} />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Content
          align="end"
          sideOffset={8}
          className="z-50 min-w-48 rounded-lg border border-line bg-white p-1 shadow-lg"
        >
          <Menu.Label className="px-3 py-2">
            <span className="block font-semibold">
              {user.name || user.login}
            </span>
            <span className="block text-xs text-slate-500">@{user.login}</span>
          </Menu.Label>
          <Menu.Separator className="my-1 h-px bg-line" />
          <Menu.Item asChild>
            <Link
              href="/home"
              className="flex cursor-pointer items-center gap-2 rounded px-3 py-2 outline-none data-[highlighted]:bg-slate-50"
            >
              <House size={15} />내 홈
            </Link>
          </Menu.Item>
          <Menu.Item asChild>
            <Link
              href={`/@${user.login}`}
              className="flex cursor-pointer items-center gap-2 rounded px-3 py-2 outline-none data-[highlighted]:bg-slate-50"
            >
              <UserRound size={15} />내 프로필
            </Link>
          </Menu.Item>
          <Menu.Item
            onSelect={() => void logout()}
            className="flex cursor-pointer items-center gap-2 rounded px-3 py-2 outline-none data-[highlighted]:bg-slate-50"
          >
            <LogOut size={15} />
            로그아웃
          </Menu.Item>
        </Menu.Content>
      </Menu.Portal>
    </Menu.Root>
  );
}
function PublishLink() {
  const { user } = useAuth();
  return user ? (
    <Link
      href="/new"
      className="flex h-9 items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand px-3.5 text-sm font-semibold text-white hover:bg-[#5e4ee6]"
    >
      <Upload size={15} />
      올리기
    </Link>
  ) : null;
}
export function TopNav() {
  const pathname = usePathname();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const links = (mobile: boolean) =>
    items(Boolean(user)).map((item) => {
      const active = item.match.some(
        (m) => pathname === m || pathname.startsWith(`${m}/`),
      );
      return (
        <Link
          key={item.href}
          href={item.href}
          onClick={() => setOpen(false)}
          aria-current={active ? "page" : undefined}
          className={cn(
            "whitespace-nowrap rounded-lg px-3 text-sm transition-colors",
            mobile ? "block py-3" : "py-2",
            active
              ? "bg-[#eeebff] font-semibold text-brand"
              : "text-[#68718a] hover:bg-slate-50 hover:text-navy",
          )}
        >
          {item.title}
        </Link>
      );
    });
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-white">
      <div className="mx-auto flex h-14 max-w-[1280px] items-center gap-2 px-4 sm:px-8">
        <button
          className="-ml-1 p-1 lg:hidden"
          onClick={() => setOpen(!open)}
          aria-label={open ? "메뉴 닫기" : "메뉴 열기"}
          aria-expanded={open}
        >
          {open ? <X size={20} /> : <MenuIcon size={20} />}
        </button>
        <Link
          href={user ? "/home" : "/"}
          aria-label="ModuleGuard home"
          className="mr-4 origin-left scale-[.85]"
        >
          <Logo />
        </Link>
        <nav className="hidden items-center gap-1 lg:flex" aria-label="주 메뉴">
          {links(false)}
        </nav>
        <div className="ml-auto flex items-center gap-3">
          <StatusDot />
          <PublishLink />
          <Account />
        </div>
      </div>
      {open && (
        <nav
          className="space-y-1 border-t border-line px-4 py-2 lg:hidden"
          aria-label="주 메뉴"
        >
          {links(true)}
        </nav>
      )}
    </header>
  );
}
