import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";

// e2e 전용 프로젝트 이름. 정리할 때 볼륨까지 지우므로 실제 스택과 절대 같으면 안 된다.
const project = "module-guard-e2e";
const compose = `docker compose -p ${project} -f docker-compose.e2e.yml`;

function realProjectName() {
  const config = execSync("docker compose config --format json", {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
  return JSON.parse(config).name as string;
}

export default function globalSetup() {
  if (realProjectName() === project)
    throw new Error(
      `e2e project name "${project}" matches the real stack; refusing to run.`,
    );
  execSync(`${compose} up -d --build --wait`, { stdio: "inherit" });
  return () => {
    // -p로 e2e 프로젝트만 지정했으므로 실제 스택의 컨테이너와 볼륨은 건드리지 않는다.
    execSync(`${compose} down -v`, { stdio: "inherit" });
    // e2e 빌드가 next-env.d.ts를 자기 distDir로 바꿔 쓰므로 되돌린다.
    const nextEnv = "next-env.d.ts";
    writeFileSync(
      nextEnv,
      readFileSync(nextEnv, "utf8").replace(".next-e2e/", ".next-dev/"),
    );
  };
}
