import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Markdown, safeUrl } from "../markdown";

const render = (source: string) =>
  renderToStaticMarkup(<Markdown>{source}</Markdown>);

describe("README rendering", () => {
  it("renders ordinary markdown", () => {
    const html = render(
      "# Title\n\nSome **bold** text and `code`.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
    );
    expect(html).toContain("<h1>Title</h1>");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<table>");
  });

  it.each([
    "<script>window.__xss = 1</script>",
    '<img src=x onerror="window.__xss=1">',
    '<iframe src="https://evil.example"></iframe>',
    '<a href="javascript:alert(1)">click</a>',
    '<svg onload="alert(1)"><circle /></svg>',
    '<div style="background:url(javascript:alert(1))">x</div>',
    "<object data='https://evil.example/x.swf'></object>",
    '<form action="https://evil.example"><input name=q></form>',
    "text <b onmouseover=alert(1)>bold</b> text",
    "<!-- comment --><style>body{display:none}</style>",
    "<math><mtext><img src=x onerror=alert(1)></mtext></math>",
  ])("never turns raw HTML into elements: %s", (payload) => {
    const html = render(`before\n\n${payload}\n\nafter`);
    expect(html).not.toMatch(
      /<(script|img|iframe|svg|object|form|input|style|b|a|math|div style)/i,
    );
    expect(html).not.toMatch(/on(error|load|mouseover)\s*=/i);
    expect(html).not.toContain("javascript:");
    expect(html).toContain("before");
    expect(html).toContain("after");
  });

  it.each([
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](  javascript:alert(1))",
    "[x](java\tscript:alert(1))",
    "[x](&#106;avascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](file:///etc/passwd)",
    "[x](//evil.example/path)",
    "[x](/relative/path)",
    "[x](./docs/a.md)",
    "[x](#anchor)",
    "[x](mailto:a@example.com)",
    "[x]: javascript:alert(1)\n\n[x]",
  ])("drops links that are not http(s): %s", (source) => {
    const html = render(source);
    expect(html).not.toContain("<a ");
    expect(html).not.toMatch(/href=/i);
    expect(html).not.toMatch(/javascript:|vbscript:|data:text/i);
  });

  it("shows an autolinked script address as plain text only", () => {
    expect(render("<javascript:alert(1)>")).toBe(
      '<div class="markdown"><p><span>javascript:alert(1)</span></p></div>',
    );
  });

  it("keeps http and https links and hardens them", () => {
    const html = render(
      "[docs](https://example.com/a?b=1) and [plain](http://example.org)",
    );
    expect(html).toContain(
      '<a href="https://example.com/a?b=1" target="_blank" rel="noopener noreferrer nofollow">docs</a>',
    );
    expect(html).toContain('href="http://example.org"');
  });

  it.each([
    "![x](http://example.com/a.png)",
    "![x](data:image/svg+xml,<svg onload=alert(1)>)",
    "![x](javascript:alert(1))",
    "![x](./docs/diagram.png)",
    "![x](//example.com/a.png)",
    "![x](blob:https://example.com/1)",
  ])("only loads images over https: %s", (source) => {
    const html = render(source);
    expect(html).not.toContain("<img");
    expect(html).not.toMatch(/src=/i);
    expect(html).toContain("x");
  });

  it("renders https images without leaking the referrer", () => {
    const html = render("![diagram](https://example.com/a.png)");
    expect(html).toContain(
      '<img src="https://example.com/a.png" alt="diagram" loading="lazy" referrerPolicy="no-referrer"/>',
    );
  });

  it("cannot be tricked into injecting attributes", () => {
    const html = render(
      '[x](https://example.com/" onmouseover="alert(1)) ![y](https://example.com/a.png" onerror="alert(1))',
    );
    // The injected text survives only as escaped text, never inside a tag.
    expect(html).not.toMatch(/<[^>]*\son\w+\s*=/i);
    expect(html).toContain("&quot; onmouseover=&quot;");
    expect(html).not.toContain("<img");
  });

  it("validates addresses strictly", () => {
    expect(safeUrl("https://example.com", "link")).toBe("https://example.com");
    expect(safeUrl("http://example.com", "image")).toBe("");
    expect(safeUrl("https://", "link")).toBe("");
    expect(safeUrl("https://exa mple.com", "link")).toBe("");
    expect(safeUrl("\u0000https://example.com", "link")).toBe("");
    expect(safeUrl("HTTPS://EXAMPLE.COM/a", "image")).toBe(
      "HTTPS://EXAMPLE.COM/a",
    );
  });
});
