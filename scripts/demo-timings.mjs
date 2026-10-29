#!/usr/bin/env node
/**
 * e2e/demo.spec.ts 가 남긴 회차별 소요 시간(JSONL)을 모아 보여 준다.
 *
 *   node scripts/demo-timings.mjs            집계: 회차별 단계 시간과 전체 시간을 출력
 *   node scripts/demo-timings.mjs --reset    이전 기록 파일을 지운다(test:demo 시작 전에 실행)
 *
 * 옵션: --file <경로>(기본 test-results/demo-timings.jsonl), --runs <회차 수>(기본 5)
 *
 * 한 줄 형식: {"run": <repeatEachIndex>, "at": "<ISO 시각>", "timings": {"<단계>": <ms>, ...}}
 * spec은 모든 단계가 통과한 회차만 기록하므로, 빠진 회차는 실패(또는 미실행)한 회차다.
 * 빈 파일, 잘못된 줄, 빠진·중복·범위 밖 회차, 단계가 어긋난 회차가 있으면 exit 1.
 */
import { existsSync, readFileSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const DEFAULT_FILE = "test-results/demo-timings.jsonl";
export const DEFAULT_RUNS = 5;

/** JSONL 본문을 검사해 회차 목록과 오류를 돌려준다. 오류가 하나라도 있으면 성공이 아니다. */
export function parseTimings(text, expectedRuns = DEFAULT_RUNS) {
  const errors = [];
  const byRun = new Map();
  const duplicated = new Set();
  const lines = text.split(/\r?\n/);
  let records = 0;

  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    records += 1;
    const where = `${index + 1}번째 줄`;
    let entry;
    try {
      entry = JSON.parse(line);
    } catch {
      errors.push(`${where}: JSON이 아닙니다`);
      return;
    }
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      errors.push(`${where}: 객체가 아닙니다`);
      return;
    }
    const { run, at, timings } = entry;
    if (!Number.isInteger(run)) {
      errors.push(`${where}: run이 정수가 아닙니다`);
      return;
    }
    if (run < 0 || run >= expectedRuns) {
      errors.push(
        `${where}: run ${run}은 0~${expectedRuns - 1} 범위 밖입니다(다른 실행의 기록이 섞였을 수 있습니다)`,
      );
      return;
    }
    if (typeof at !== "string" || Number.isNaN(Date.parse(at))) {
      errors.push(`${where}: at이 올바른 시각이 아닙니다`);
      return;
    }
    if (!timings || typeof timings !== "object" || Array.isArray(timings)) {
      errors.push(`${where}: timings가 객체가 아닙니다`);
      return;
    }
    const steps = Object.entries(timings);
    if (steps.length === 0) {
      errors.push(`${where}: timings가 비어 있습니다`);
      return;
    }
    const bad = steps.find(
      ([, ms]) => typeof ms !== "number" || !Number.isFinite(ms) || ms < 0,
    );
    if (bad) {
      errors.push(`${where}: 단계 "${bad[0]}"의 시간이 올바르지 않습니다`);
      return;
    }
    if (byRun.has(run)) {
      errors.push(
        `${where}: run ${run}이 ${byRun.get(run).line}번째 줄과 중복됩니다(이전 실행의 기록이 섞였습니다)`,
      );
      duplicated.add(run);
      return;
    }
    byRun.set(run, { run, at, timings, line: index + 1 });
  });

  if (records === 0) {
    return { runs: [], steps: [], errors: ["기록이 비어 있습니다"] };
  }

  // 중복된 회차는 어느 쪽이 이번 실행인지 알 수 없으므로 표에서 뺀다.
  for (const run of duplicated) byRun.delete(run);

  const missing = [];
  for (let run = 0; run < expectedRuns; run += 1) {
    if (!byRun.has(run) && !duplicated.has(run)) missing.push(run);
  }
  if (missing.length) {
    errors.push(
      `빠진 회차: run ${missing.join(", ")} (실패했거나 실행되지 않았습니다)`,
    );
  }

  const runs = [...byRun.values()].sort((a, b) => a.run - b.run);
  // 단계 순서는 처음 나온 순서(spec의 실행 순서)를 따른다.
  const steps = [];
  for (const { timings } of runs) {
    for (const name of Object.keys(timings)) {
      if (!steps.includes(name)) steps.push(name);
    }
  }
  for (const { run, timings } of runs) {
    const absent = steps.filter((name) => !(name in timings));
    if (absent.length) {
      errors.push(
        `run ${run}: 빠진 단계 ${absent.map((n) => `"${n}"`).join(", ")}`,
      );
    }
  }

  return {
    runs: runs.map(({ run, at, timings }) => ({
      run,
      at,
      timings,
      total: Object.values(timings).reduce((sum, ms) => sum + ms, 0),
    })),
    steps,
    errors,
  };
}

const seconds = (ms) => `${(ms / 1000).toFixed(2)}s`;

/** 단계 이름은 폭이 제각각(한글)이라 맨 끝 열에 두고 숫자 열만 맞춘다. */
export function formatReport({ runs, steps }, expectedRuns = DEFAULT_RUNS) {
  const width = 10;
  const cell = (text) => String(text).padStart(width);
  const average = (values) =>
    values.length ? values.reduce((a, b) => a + b, 0) / values.length : NaN;
  const header = [...runs.map(({ run }) => cell(`run ${run}`)), cell("평균")];
  const rows = steps.map((name) => {
    const values = runs.map(({ timings }) => timings[name]);
    const shown = values.map((ms) =>
      cell(ms === undefined ? "-" : seconds(ms)),
    );
    const present = values.filter((ms) => ms !== undefined);
    return [...shown, cell(seconds(average(present))), `  ${name}`].join("");
  });
  const totals = runs.map(({ total }) => total);
  const lines = [
    `데모 소요 시간: ${runs.length}/${expectedRuns}회`,
    "",
    [...header, "  단계"].join(""),
    ...rows,
    [
      ...totals.map((ms) => cell(seconds(ms))),
      cell(seconds(average(totals))),
      "  전체",
    ].join(""),
  ];
  if (totals.length) {
    lines.push(
      "",
      `전체 최소 ${seconds(Math.min(...totals))} / 평균 ${seconds(average(totals))} / 최대 ${seconds(Math.max(...totals))}`,
    );
  }
  return lines.join("\n");
}

function parseArgs(argv) {
  const options = { file: DEFAULT_FILE, runs: DEFAULT_RUNS, reset: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--reset") options.reset = true;
    else if (arg === "--file") options.file = argv[++i];
    else if (arg === "--runs") options.runs = Number(argv[++i]);
    else throw new Error(`알 수 없는 인자: ${arg}`);
  }
  if (!options.file) throw new Error("--file 경로가 필요합니다");
  if (!Number.isInteger(options.runs) || options.runs < 1) {
    throw new Error("--runs는 1 이상의 정수여야 합니다");
  }
  return options;
}

export function main(argv = process.argv.slice(2), io = console) {
  let options;
  try {
    options = parseArgs(argv);
  } catch (error) {
    io.error(error.message);
    return 2;
  }
  const file = resolve(options.file);
  if (options.reset) {
    rmSync(file, { force: true });
    io.log(`이전 기록을 지웠습니다: ${options.file}`);
    return 0;
  }
  if (!existsSync(file)) {
    io.error(
      `기록 파일이 없습니다: ${options.file} (데모 테스트가 한 번도 통과하지 않았습니다)`,
    );
    return 1;
  }
  const result = parseTimings(readFileSync(file, "utf8"), options.runs);
  if (result.runs.length) io.log(formatReport(result, options.runs));
  if (result.errors.length) {
    io.error(
      [
        "",
        `집계 실패 (${options.file}):`,
        ...result.errors.map((e) => `  - ${e}`),
      ].join("\n"),
    );
    return 1;
  }
  return 0;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  process.exitCode = main();
}
