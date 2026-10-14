"use client";
import { useEffect, useState } from "react";
import { LiquidMetal } from "@paper-design/shaders-react";

// 리퀴드 메탈은 단색 실루엣에서 베벨이 잘 잡힌다. 체크 표시는 마스크로 뚫는다.
const shield = `<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><mask id="m"><rect width="1000" height="1000" fill="#fff"/><path d="m345 500 110 112 205-232" fill="none" stroke="#000" stroke-width="74" stroke-linecap="round" stroke-linejoin="round"/></mask><path mask="url(#m)" d="M500 70c-12 0-24 2-35 7L205 180c-33 13-55 45-55 81v215c0 214 152 352 318 444 20 11 44 11 64 0 166-92 318-230 318-444V261c0-36-22-68-55-81L535 77c-11-5-23-7-35-7Z" fill="#000"/></svg>`;
const shieldUrl = `data:image/svg+xml;utf8,${encodeURIComponent(shield)}`;

export function LiquidShield({ className }: { className?: string }) {
  const [image, setImage] = useState("");
  const [still, setStill] = useState(false);
  useEffect(() => {
    setStill(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (!document.createElement("canvas").getContext("webgl2")) return;
    // SVG를 그대로 넘기면 처리 결과가 비어서 PNG로 한 번 그려서 넘긴다.
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1000;
      canvas.getContext("2d")?.drawImage(img, 0, 0, 1000, 1000);
      setImage(canvas.toDataURL("image/png"));
    };
    img.src = shieldUrl;
  }, []);
  return (
    <div className={className} aria-hidden="true">
      {image ? (
        <LiquidMetal
          image={image}
          colorBack="#00000000"
          colorTint="#b9aeff"
          repetition={2}
          softness={0.1}
          shiftRed={0.3}
          shiftBlue={0.3}
          distortion={0.07}
          contour={0.4}
          angle={70}
          scale={0.9}
          speed={still ? 0 : 0.8}
          style={{ width: "100%", height: "100%" }}
        />
      ) : (
        // WebGL을 쓸 수 없을 때 보이는 정적 방패
        <div
          className="h-full w-full bg-gradient-to-b from-[#cfc8ff] to-brand"
          style={{
            maskImage: `url("${shieldUrl}")`,
            WebkitMaskImage: `url("${shieldUrl}")`,
            maskSize: "90%",
            WebkitMaskSize: "90%",
            maskRepeat: "no-repeat",
            WebkitMaskRepeat: "no-repeat",
            maskPosition: "center",
            WebkitMaskPosition: "center",
          }}
        />
      )}
    </div>
  );
}
