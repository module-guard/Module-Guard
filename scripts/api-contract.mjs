#!/usr/bin/env node
/**
 * FastAPI OpenAPI → TypeScript API contract.
 *
 *   node scripts/api-contract.mjs generate  openapi.json, src/api/generated/schema.d.ts 를 다시 만든다
 *   node scripts/api-contract.mjs sync      generate + typecheck + test (백엔드 API를 바꾼 뒤 실행)
 *   node scripts/api-contract.mjs check     커밋된 파일이 최신인지 비교만 한다(파일을 바꾸지 않음) + typecheck + test
 *
 * 백엔드 파이썬: 환경 변수 BACKEND_PYTHON, 없으면 backend/.venv 의 python, 그것도 없으면 python3.
 * 모든 외부 명령은 execFileSync(인자 배열)로 실행한다. 셸을 거치지 않는다.
 */
import { execFileSync } from "node:child_process";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  mkdirSync,
  copyFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const backend = join(root, "backend");
const require = createRequire(join(root, "package.json"));

const OPENAPI = join(root, "openapi.json");
const SCHEMA = join(root, "src", "api", "generated", "schema.d.ts");

function backendPython() {
  if (process.env.BACKEND_PYTHON) return process.env.BACKEND_PYTHON;
  const candidates = [
    join(backend, ".venv", "bin", "python"),
    join(backend, ".venv", "Scripts", "python.exe"),
  ];
  return (
    candidates.find((p) => existsSync(p)) ??
    (process.platform === "win32" ? "python" : "python3")
  );
}

/** node_modules 패키지의 bin 스크립트 경로 (npx/셸 없이 node로 직접 실행하기 위해). */
function packageBin(pkg, name = pkg) {
  const manifestPath = require.resolve(`${pkg}/package.json`);
  const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
  const bin =
    typeof manifest.bin === "string" ? manifest.bin : manifest.bin?.[name];
  if (!bin)
    throw new Error(
      `${pkg} 패키지에서 실행 파일 ${name}을 찾지 못했습니다. npm ci를 먼저 실행하세요.`,
    );
  return join(dirname(manifestPath), bin);
}

function run(file, args, options = {}) {
  execFileSync(file, args, { stdio: "inherit", ...options });
}

/** outDir에 openapi.json, schema.d.ts를 만든다. */
function generateInto(outDir) {
  const openapi = join(outDir, "openapi.json");
  const schema = join(outDir, "schema.d.ts");
  // backend/.env를 읽도록 backend/에서 실행한다 (backend/README.md의 로컬 실행 규칙과 같음).
  run(
    backendPython(),
    [join(backend, "scripts", "export_openapi.py"), openapi],
    { cwd: backend },
  );
  run(
    process.execPath,
    [packageBin("openapi-typescript"), openapi, "-o", schema],
    { cwd: root },
  );
  return { openapi, schema };
}

function typecheckAndTest() {
  run(process.execPath, [packageBin("typescript", "tsc"), "--noEmit"], {
    cwd: root,
  });
  run(process.execPath, [packageBin("vitest"), "run"], { cwd: root });
}

/** 줄바꿈(CRLF/LF) 차이는 무시한다. Windows에서 git autocrlf로 받은 파일 때문. */
function sameText(a, b) {
  if (!existsSync(a) || !existsSync(b)) return false;
  const norm = (p) => readFileSync(p, "utf8").replace(/\r\n/g, "\n");
  return norm(a) === norm(b);
}

function withTempDir(fn) {
  const dir = mkdtempSync(join(tmpdir(), "api-contract-"));
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

function generate() {
  withTempDir((dir) => {
    const out = generateInto(dir);
    mkdirSync(dirname(SCHEMA), { recursive: true });
    copyFileSync(out.openapi, OPENAPI);
    copyFileSync(out.schema, SCHEMA);
  });
  console.log(
    "API 계약 생성 완료: openapi.json, src/api/generated/schema.d.ts",
  );
}

function check() {
  const stale = withTempDir((dir) => {
    const out = generateInto(dir);
    return [
      [out.openapi, OPENAPI, "openapi.json"],
      [out.schema, SCHEMA, "src/api/generated/schema.d.ts"],
    ]
      .filter(([fresh, committed]) => !sameText(fresh, committed))
      .map(([, , label]) => label);
  });
  if (stale.length) {
    console.error(`\nAPI 계약이 백엔드와 다릅니다: ${stale.join(", ")}`);
    console.error("npm run api:sync 를 실행하고 바뀐 파일을 커밋하세요.");
    process.exit(1);
  }
  console.log("API 계약이 최신입니다.");
}

const command = process.argv[2];
try {
  if (command === "generate") generate();
  else if (command === "sync") {
    generate();
    typecheckAndTest();
  } else if (command === "check") {
    check();
    typecheckAndTest();
  } else {
    console.error(
      "사용법: node scripts/api-contract.mjs <generate|sync|check>",
    );
    process.exit(2);
  }
} catch (error) {
  // execFileSync 실패는 자식 프로세스 출력이 이미 화면에 나왔으므로 요약만 남긴다.
  console.error(
    `\napi-contract ${command} 실패: ${error.message.split("\n")[0]}`,
  );
  process.exit(typeof error.status === "number" ? error.status : 1);
}
