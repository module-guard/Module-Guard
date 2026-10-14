import Image, { type StaticImageData } from "next/image";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { Logo } from "@/components/logo";
import { LiquidShield } from "./liquid-shield";
import { Reveal } from "./reveal";
import find from "./shots/find.png";
import check from "./shots/check.png";
import take from "./shots/take.png";

const steps: {
  label: string;
  line: string;
  shot: StaticImageData;
  alt: string;
}[] = [
  {
    label: "찾기",
    line: "Terraform 모듈을 이름과 클라우드로 찾습니다.",
    shot: find,
    alt: "모듈 탐색 화면: 모듈 카드마다 보안 배지, 별 수, 가져간 수",
  },
  {
    label: "확인",
    line: "쓰기 전에 보안 검사 결과와 라이선스를 봅니다.",
    shot: check,
    alt: "모듈 페이지의 보안 탭: 심각도별 탐지 수와 검사 시각",
  },
  {
    label: "가져가기",
    line: "변수를 채우면 바로 쓸 코드와 zip이 나옵니다.",
    shot: take,
    alt: "사용하기 탭: 변수 입력 폼과 만들어진 main.tf",
  },
];

function StartButton({ large = false }: { large?: boolean }) {
  return (
    <Link
      href="/explore"
      className={
        large
          ? "inline-flex h-[52px] items-center gap-2 rounded-full bg-brand px-8 text-[17px] font-semibold text-white hover:bg-[#7d6ffc]"
          : "inline-flex h-8 items-center rounded-full bg-brand px-4 text-xs font-semibold text-white hover:bg-[#7d6ffc]"
      }
    >
      시작하기
      {large && <ArrowRight size={18} />}
    </Link>
  );
}

export function Landing() {
  return (
    <div className="min-h-screen bg-[#05060f] text-white [word-break:keep-all]">
      <header className="fixed inset-x-0 top-0 z-50 border-b border-white/10 bg-[#05060f]/70 backdrop-blur-xl">
        <div className="mx-auto flex h-12 max-w-[1080px] items-center justify-between px-5">
          <Link href="/" aria-label="ModuleGuard" className="origin-left scale-[.8]">
            <Logo dark />
          </Link>
          <StartButton />
        </div>
      </header>

      <main>
        <section className="relative flex min-h-[100svh] flex-col items-center justify-center overflow-hidden px-5 pb-16 pt-20 text-center">
          <div
            className="pointer-events-none absolute left-1/2 top-[38%] h-[640px] w-[640px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-brand/25 blur-[140px]"
            aria-hidden="true"
          />
          <LiquidShield className="relative h-[min(46svh,440px)] w-[min(46svh,440px)]" />
          <h1 className="relative mt-6 text-balance text-[clamp(36px,6.4vw,80px)] font-bold leading-[1.08] tracking-[-.04em]">
            쓰기 전에, 확인하세요.
          </h1>
          <p className="relative mt-5 text-[clamp(16px,2vw,22px)] text-white/60">
            Terraform 모듈을 찾고, 검사 결과를 보고, 내 코드로 가져갑니다.
          </p>
          <div className="relative mt-9">
            <StartButton large />
          </div>
        </section>

        {steps.map((s, i) => (
          <section key={s.label} className="px-5 py-[clamp(72px,12vw,160px)]">
            <Reveal className="mx-auto max-w-[1080px] text-center">
              <p className="text-sm font-semibold tracking-wide text-[#a79bff]">
                {String(i + 1).padStart(2, "0")} · {s.label}
              </p>
              <h2 className="mx-auto mt-4 max-w-[16em] text-balance text-[clamp(28px,5vw,60px)] font-bold leading-[1.15] tracking-[-.035em]">
                {s.line}
              </h2>
              <div className="mt-[clamp(36px,6vw,72px)] overflow-hidden rounded-[clamp(10px,1.6vw,20px)] border border-white/10 shadow-[0_40px_120px_-20px_#6d5dfb66]">
                <Image
                  src={s.shot}
                  alt={s.alt}
                  sizes="(max-width: 1120px) 100vw, 1080px"
                  placeholder="blur"
                  className="h-auto w-full"
                />
              </div>
            </Reveal>
          </section>
        ))}

        <section className="px-5 pb-[clamp(96px,14vw,200px)] pt-[clamp(72px,10vw,140px)] text-center">
          <Reveal>
            <h2 className="text-balance text-[clamp(32px,5.6vw,68px)] font-bold leading-[1.1] tracking-[-.04em]">
              모듈을 둘러보세요.
            </h2>
            <div className="mt-10">
              <StartButton large />
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="border-t border-white/10 px-5 py-6 text-center text-xs text-white/40">
        © 2026 ModuleGuard · Terraform Module Intelligence
      </footer>
    </div>
  );
}
