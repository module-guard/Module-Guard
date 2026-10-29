import { execFileSync, spawnSync } from "node:child_process";
import {
  copyFileSync,
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { parseTimings } from "../demo-timings.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const script = join(here, "..", "demo-timings.mjs");
const fixture = (name) => join(here, "fixtures", name);
const read = (name) => readFileSync(fixture(name), "utf8");

// 실제 CLI를 돌려 exit 코드와 출력을 본다(데모 자체는 실행하지 않는다).
function cli(...args) {
  const result = spawnSync(process.execPath, [script, ...args], {
    encoding: "utf8",
  });
  return { code: result.status, stdout: result.stdout, stderr: result.stderr };
}

const temps = [];
afterEach(() => {
  for (const dir of temps.splice(0))
    rmSync(dir, { recursive: true, force: true });
});

describe("parseTimings", () => {
  it("aggregates five complete runs with per-run totals", () => {
    const result = parseTimings(read("valid-5.jsonl"));
    expect(result.errors).toEqual([]);
    expect(result.runs.map((r) => r.run)).toEqual([0, 1, 2, 3, 4]);
    expect(result.steps).toHaveLength(7);
    expect(result.steps[0]).toBe("1 탐색에서 vpc 검색");
    // run 0: 1000 + 1100 + ... + 1600
    expect(result.runs[0].total).toBe(9100);
    expect(result.runs[4].total).toBe(9100 + 7 * 40);
  });

  it.each([
    ["empty.jsonl", /기록이 비어 있습니다/],
    ["blank-lines.jsonl", /기록이 비어 있습니다/],
    ["invalid-json.jsonl", /3번째 줄: JSON이 아닙니다/],
    ["missing-run.jsonl", /빠진 회차: run 2/],
    ["mixed-previous.jsonl", /run 0이 1번째 줄과 중복됩니다/],
    ["out-of-range.jsonl", /run 5은 0~4 범위 밖입니다/],
    ["missing-step.jsonl", /run 2: 빠진 단계 "7 내 프로필/],
    ["bad-timing.jsonl", /2번째 줄: 단계 "1 탐색에서 vpc 검색"의 시간이/],
  ])("rejects %s", (name, message) => {
    const { errors } = parseTimings(read(name));
    expect(errors.join("\n")).toMatch(message);
  });

  it("drops duplicated runs instead of showing stale timings", () => {
    const { runs, errors } = parseTimings(read("mixed-previous.jsonl"));
    expect(runs.map((r) => r.run)).toEqual([2, 3, 4]);
    expect(errors.join("\n")).not.toMatch(/빠진 회차/);
  });

  it("reports a broken line also as a missing run", () => {
    const { errors } = parseTimings(read("invalid-json.jsonl"));
    expect(errors.join("\n")).toMatch(/빠진 회차: run 2/);
  });

  it("rejects non-object records and invalid fields", () => {
    const cases = [
      ["[1,2]", /객체가 아닙니다/],
      [
        '{"run":"0","at":"2026-10-29T00:00:00Z","timings":{"a":1}}',
        /run이 정수가/,
      ],
      ['{"run":0,"at":"yesterday","timings":{"a":1}}', /at이 올바른/],
      ['{"run":0,"at":"2026-10-29T00:00:00Z","timings":{}}', /timings가 비어/],
      [
        '{"run":0,"at":"2026-10-29T00:00:00Z","timings":{"a":-1}}',
        /시간이 올바르지/,
      ],
    ];
    for (const [line, message] of cases) {
      expect(parseTimings(line, 1).errors.join("\n")).toMatch(message);
    }
  });

  it("honours a different expected run count", () => {
    expect(parseTimings(read("valid-5.jsonl"), 5).errors).toEqual([]);
    expect(parseTimings(read("valid-5.jsonl"), 3).errors.join("\n")).toMatch(
      /run 3은 0~2 범위 밖/,
    );
    expect(parseTimings(read("valid-5.jsonl"), 6).errors.join("\n")).toMatch(
      /빠진 회차: run 5/,
    );
  });
});

describe("demo-timings CLI", () => {
  it("prints per-run step times and totals, exit 0", () => {
    const { code, stdout, stderr } = cli("--file", fixture("valid-5.jsonl"));
    expect(code).toBe(0);
    expect(stderr).toBe("");
    expect(stdout).toContain("데모 소요 시간: 5/5회");
    expect(stdout).toMatch(
      /run 0\s+run 1\s+run 2\s+run 3\s+run 4\s+평균\s+단계/,
    );
    expect(stdout).toMatch(
      /1\.00s\s+1\.01s\s+1\.02s\s+1\.03s\s+1\.04s\s+1\.02s\s+1 탐색에서 vpc 검색/,
    );
    expect(stdout).toMatch(
      /9\.10s\s+9\.17s\s+9\.24s\s+9\.31s\s+9\.38s\s+9\.24s\s+전체/,
    );
    expect(stdout).toContain("전체 최소 9.10s / 평균 9.24s / 최대 9.38s");
  });

  it.each([
    "empty.jsonl",
    "blank-lines.jsonl",
    "invalid-json.jsonl",
    "missing-run.jsonl",
    "mixed-previous.jsonl",
    "out-of-range.jsonl",
    "missing-step.jsonl",
    "bad-timing.jsonl",
  ])("fails on %s with exit 1", (name) => {
    const { code, stderr } = cli("--file", fixture(name));
    expect(code).toBe(1);
    expect(stderr).toContain("집계 실패");
  });

  it("still shows the runs it could read when failing", () => {
    const { code, stdout } = cli("--file", fixture("missing-run.jsonl"));
    expect(code).toBe(1);
    expect(stdout).toContain("데모 소요 시간: 4/5회");
  });

  it("fails when the timings file does not exist", () => {
    const dir = mkdtempSync(join(tmpdir(), "mg-timings-"));
    temps.push(dir);
    const { code, stderr } = cli("--file", join(dir, "none.jsonl"));
    expect(code).toBe(1);
    expect(stderr).toContain("기록 파일이 없습니다");
  });

  it("--reset removes previous records so they cannot mix in", () => {
    const dir = mkdtempSync(join(tmpdir(), "mg-timings-"));
    temps.push(dir);
    const file = join(dir, "demo-timings.jsonl");
    copyFileSync(fixture("valid-5.jsonl"), file);
    expect(cli("--reset", "--file", file).code).toBe(0);
    expect(existsSync(file)).toBe(false);
    // 지운 뒤 아무 회차도 통과하지 않았다면 집계는 실패한다.
    expect(cli("--file", file).code).toBe(1);
    // 파일이 없어도 reset은 성공한다.
    expect(cli("--reset", "--file", file).code).toBe(0);
  });

  it("rejects bad arguments with exit 2", () => {
    expect(cli("--runs", "0").code).toBe(2);
    expect(cli("--unknown").code).toBe(2);
  });

  it("is wired into npm run test:demo", () => {
    const pkg = JSON.parse(
      readFileSync(join(here, "..", "..", "package.json"), "utf8"),
    );
    const command = pkg.scripts["test:demo"];
    expect(command).toMatch(
      /^node scripts\/demo-timings\.mjs --reset && .*playwright test e2e\/demo\.spec\.ts --repeat-each=5 .*&& node scripts\/demo-timings\.mjs$/,
    );
    expect(existsSync(join(here, "..", "demo-timings.mjs"))).toBe(true);
    // 스크립트 기본 경로가 spec이 기록하는 경로와 같다.
    const spec = readFileSync(
      join(here, "..", "..", "e2e", "demo.spec.ts"),
      "utf8",
    );
    expect(spec).toContain('const TIMINGS = "test-results/demo-timings.jsonl"');
    execFileSync(process.execPath, ["--check", script]);
  });
});
