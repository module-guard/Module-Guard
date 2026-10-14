import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 사용자가 쓴 글(README)을 그린다. HTML은 렌더링하지 않고, 주소는 아래 규칙만 통과한다.
const LINK = /^https?:\/\//i;
const IMAGE = /^https:\/\//i;

/** 링크는 http(s), 이미지는 https만. 그 밖의 주소(javascript:, data:, 상대 경로 등)는 버린다. */
export function safeUrl(url: string, kind: "link" | "image"): string {
  const value = url.trim();
  // 제어문자나 공백을 섞어 스킴을 숨기는 주소는 받지 않는다.
  if (/[\u0000- \u007f]/.test(value)) return "";
  if (!(kind === "image" ? IMAGE : LINK).test(value)) return "";
  try {
    const parsed = new URL(value);
    const allowed = kind === "image" ? ["https:"] : ["http:", "https:"];
    return allowed.includes(parsed.protocol) && parsed.hostname ? value : "";
  } catch {
    return "";
  }
}

export function Markdown({ children }: { children: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 원시 HTML은 건너뛴다(rehype-raw를 쓰지 않으므로 어차피 요소가 되지 않는다).
        skipHtml
        urlTransform={(url, key) =>
          safeUrl(url, key === "src" ? "image" : "link")
        }
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
          img: ({ src, alt }) =>
            typeof src === "string" && src ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={src}
                alt={alt || ""}
                loading="lazy"
                referrerPolicy="no-referrer"
              />
            ) : (
              <span>{alt}</span>
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
