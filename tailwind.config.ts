import type { Config } from "tailwindcss";
export default { content: ["./src/**/*.{js,ts,jsx,tsx,mdx}"], theme: { extend: { colors: { brand: "#6D5DFB", navy: "#15234A", canvas: "#F7F8FC", line: "#E7E9F2" }, fontFamily: { sans: ["Inter", "Arial", "Helvetica", "sans-serif"] } } }, plugins: [] } satisfies Config;
