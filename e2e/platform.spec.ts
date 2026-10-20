import { test, expect } from "@playwright/test";
const backend = process.env.PLAYWRIGHT_API_URL || "http://localhost:8000";
test("scan form → actual fixture pipeline → results, graph, trace and attack path", async ({
  page,
  request,
}) => {
  const renderErrors: string[] = [];
  page.on("console", (message) => {
    if (
      message.type() === "error" &&
      /Maximum update|React Flow|hydration/i.test(message.text())
    )
      renderErrors.push(message.text());
  });
  // Route only the collector entry to the development fixture. Responses come from the real backend.
  await page.route("**/api/scans", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    const response = await request.post(`${backend}/api/dev/demo/correlation`);
    expect(response.status()).toBe(202);
    await route.fulfill({
      status: 202,
      contentType: "application/json",
      body: await response.text(),
    });
  });
  await page.goto("/scan");
  await page
    .getByLabel("GitHub 저장소 주소")
    .fill("https://github.com/example/fixture");
  await page.getByRole("button", { name: "검사 시작", exact: true }).click();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  await expect(
    page.getByText("개발용 Fixture 시나리오 · Checkov 입력", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: /^보안 설정/ }).click();
  await expect(page.getByText("CKV_AWS_62", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^공급망/ }).click();
  await expect(page.getByText("TFSC004", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /^의존성/ }).click();
  await expect(page.getByRole("img", { name: "의존성 그래프" })).toBeVisible();
  await expect(page.getByLabel("그래프 깊이")).toHaveValue("1");
  await page.getByLabel("그래프 깊이").selectOption("2");
  await page.getByLabel("그래프 방향").selectOption("out");
  const dependencyGraph = page.getByRole("img", { name: "의존성 그래프" });
  const firstNode = dependencyGraph.locator(".react-flow__node-entity").first();
  await firstNode.click();
  await expect(
    page.getByRole("complementary", { name: "그래프 상세" }),
  ).toContainText(
    (await firstNode.getAttribute("aria-label")) || "ROOT_MODULE",
  );
  await firstNode.dblclick();
  await expect(page.getByLabel("중심 주소", { exact: true })).toHaveValue(
    (await firstNode.getAttribute("aria-label")) || "root",
  );

  const scanId = page.url().split("/").pop()!;
  const fullGraph = await (
    await request.get(`${backend}/api/scans/${scanId}/graph`)
  ).json();
  await page
    .getByRole("button", { name: "전체 그래프 (고립 노드 포함)", exact: true })
    .click();
  await expect(dependencyGraph.locator(".react-flow__node-entity")).toHaveCount(
    fullGraph.nodes.length,
  );
  await expect(dependencyGraph.locator(".react-flow__edge")).toHaveCount(
    fullGraph.edges.length,
  );
  await expect(
    dependencyGraph.locator(".react-flow__minimap-node").first(),
  ).toBeVisible();
  await page.getByRole("button", { name: /^데이터 흐름/ }).click();
  await page
    .getByRole("button", { name: "전체 연결 불러오기", exact: true })
    .click();
  const flows = await (
    await request.get(`${backend}/api/scans/${scanId}/flows`)
  ).json();
  const flowCanvas = page.getByRole("img", {
    name: "데이터 흐름 그래프",
    exact: true,
  });
  await expect(flowCanvas.locator(".react-flow__node-entity")).toHaveCount(
    flows.nodes.length,
  );
  await expect(flowCanvas.locator(".react-flow__edge")).toHaveCount(
    flows.edges.length,
  );
  await page
    .getByLabel("데이터 흐름 그래프 노드 찾기", { exact: true })
    .selectOption("local.policy");
  await page
    .getByRole("button", { name: "인접 흐름 보기", exact: true })
    .click();
  const adjacent = await (
    await request.get(`${backend}/api/scans/${scanId}/flows/local.policy`)
  ).json();
  const adjacentCanvas = page.getByRole("img", {
    name: "인접 흐름 그래프",
    exact: true,
  });
  await expect(adjacentCanvas.locator(".react-flow__node-entity")).toHaveCount(
    adjacent.nodes.length,
  );
  await expect(adjacentCanvas.locator(".react-flow__edge")).toHaveCount(
    new Set(
      [...adjacent.upstream, ...adjacent.downstream].map(
        (e: { id: string }) => e.id,
      ),
    ).size,
  );
  await page.getByRole("button", { name: "경로 추적", exact: true }).click();
  await page
    .getByLabel("Source address", { exact: true })
    .fill("module.policy_source.policy");
  await page
    .getByLabel("Target address", { exact: true })
    .fill("aws_iam_role_policy.admin.policy");
  await page.getByRole("button", { name: "흐름 추적", exact: true }).click();
  await expect(
    page
      .getByRole("img", { name: "흐름 추적 그래프" })
      .getByText("local.policy", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: /^상관 위험/ }).click();
  await expect(page.getByText("TFCR001", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "근거 및 영향 경로 보기" }).click();
  await expect(
    page.getByRole("region", { name: "상관 분석 근거" }),
  ).toContainText("MEDIUM");
  await expect(
    page.getByRole("region", { name: "상관 분석 근거" }),
  ).toContainText("local.policy");
  await expect(
    page.getByRole("img", { name: "상관 공격 경로 그래프" }),
  ).toBeVisible();
  const attack = page.getByRole("img", { name: "상관 공격 경로 그래프" });
  await expect(attack.locator(".react-flow__node-entity")).toHaveCount(6);
  await expect(attack.locator(".react-flow__edge")).toHaveCount(5);
  await page.getByRole("button", { name: "크게 보기", exact: true }).click();
  await expect(attack.locator(".react-flow__edge-path")).toHaveCount(5);
  await page.screenshot({
    path: "test-results/correlation-ui.png",
    fullPage: true,
  });
  expect(renderErrors).toEqual([]);
  // A development scan has no module page; in development mode its old address stays.
  await page.reload();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+$/);
  await expect(
    page.getByText("개발용 Fixture 시나리오 · Checkov 입력", { exact: false }),
  ).toBeVisible();
});
test("development page and actual health", async ({ page }) => {
  await page.goto("/dev/analysis-test");
  await expect(page.getByText("연결됨", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "terraform-aws-vpc 예제 주소 입력" })
    .click();
  await expect(page.getByLabel("저장소 주소", { exact: true })).toHaveValue(
    "https://github.com/terraform-aws-modules/terraform-aws-vpc",
  );
  await expect(page.getByLabel("검사 ID", { exact: true })).toHaveValue("");
  await page
    .getByRole("button", { name: "상관 위험 데모 실행", exact: true })
    .click();
  await expect(page.getByText("완료", { exact: true }).first()).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("button", { name: "API 결과 확인" }).click();
  await expect(page.getByText("성공", { exact: true })).toHaveCount(6);
});
test("backend failure is visible to users", async ({ page }) => {
  await page.route("**/api/repositories?*", (route) => route.abort());
  await page.goto("/explore");
  await expect(
    page.getByRole("alert").filter({ hasText: "백엔드에 연결" }),
  ).toContainText("백엔드에 연결");
  await expect(page.getByRole("button", { name: "다시 시도" })).toBeVisible();
});

test("live public repository through the browser", async ({
  page,
  request,
}, testInfo) => {
  test.skip(
    process.env.RUN_LIVE_SCAN !== "true",
    "Opt-in network + Checkov integration",
  );
  test.setTimeout(600000);
  await page.goto("/scan");
  await page
    .getByLabel("GitHub 저장소 주소")
    .fill("https://github.com/terraform-aws-modules/terraform-aws-vpc");
  await page.getByRole("button", { name: "검사 시작", exact: true }).click();
  await expect(page).toHaveURL(/\/scans\/[a-f0-9-]+/);
  const scanId = page.url().split("/").pop()!;
  await expect
    .poll(
      async () =>
        (await (await request.get(`${backend}/api/scans/${scanId}`)).json())
          .status,
      { timeout: 580000, intervals: [2000] },
    )
    .toMatch(/COMPLETED|FAILED/);
  const scan = await (
    await request.get(`${backend}/api/scans/${scanId}`)
  ).json();
  expect(scan.status).toBe("COMPLETED");
  console.log(
    "LIVE_REPOSITORY_RESULT",
    JSON.stringify({
      id: scan.id,
      commit: scan.commit_hash,
      duration_ms: scan.duration_ms,
      entities: scan.analysis.entities,
      dependency_edges: scan.analysis.dependency_edges,
      data_flow: scan.analysis.data_flow,
      scanner_breakdown: scan.analysis.scanner_breakdown,
    }),
  );
  await testInfo.attach("live-scan.json", {
    body: JSON.stringify(scan, null, 2),
    contentType: "application/json",
  });
  await page.getByRole("button", { name: /^상관 위험/ }).click();
  await expect(
    page.getByText("연결된 고위험 경로가 발견되지 않았습니다.", {
      exact: true,
    }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/live-repository-ui.png",
    fullPage: true,
  });
});

test("graph audit preserves parallel, missing and isolated endpoints", async ({
  page,
  request,
}) => {
  const created = await (
    await request.post(`${backend}/api/dev/demo/correlation`)
  ).json();
  await expect
    .poll(
      async () =>
        (
          await (
            await request.get(`${backend}/api/scans/${created.scan_id}`)
          ).json()
        ).status,
    )
    .toBe("COMPLETED");
  const nodes = [
    {
      id: "root",
      address: "root",
      type: "ROOT_MODULE",
      metadata: {},
      module_path: [],
    },
    {
      id: "a",
      address: "module.network.aws_vpc.main",
      type: "RESOURCE",
      metadata: {},
      module_path: ["network"],
    },
    {
      id: "b",
      address: "module.security.aws_security_group.main",
      type: "RESOURCE",
      metadata: {},
      module_path: ["security"],
    },
    {
      id: "isolated",
      address: "var.unused",
      type: "VARIABLE",
      metadata: {},
      module_path: [],
    },
  ];
  const edges = [
    {
      id: "p1",
      source: "a",
      target: "b",
      type: "REFERENCES",
      expression: "security.id",
      metadata: {},
    },
    {
      id: "p2",
      source: "a",
      target: "b",
      type: "EXPLICIT_DEPENDS_ON",
      expression: "security",
      metadata: {},
    },
    {
      id: "missing",
      source: "b",
      target: null,
      target_address: "module.external.output",
      type: "CONSUMES_MODULE_OUTPUT",
      expression: null,
      metadata: { resolved: false },
    },
  ];
  await page.route(`**/api/scans/${created.scan_id}/graph`, (route) =>
    route.fulfill({ json: { schema_version: 1, nodes, edges } }),
  );
  await page.goto(`/scans/${created.scan_id}`);
  await page.getByRole("button", { name: /^의존성/ }).click();
  await page
    .getByRole("button", { name: "전체 그래프 (고립 노드 포함)", exact: true })
    .click();
  const graph = page.getByRole("img", { name: "의존성 그래프", exact: true });
  await expect(graph.locator(".react-flow__node-entity")).toHaveCount(5);
  await expect(graph.locator(".react-flow__edge")).toHaveCount(3);
  await expect(page.getByText(/상세가 없는 노드 1개/)).toBeVisible();
  const curves = await graph
    .locator(".react-flow__edge-path")
    .evaluateAll((es) => es.map((e) => e.getAttribute("d")));
  expect(new Set(curves).size).toBe(3);
  await expect(
    graph.locator(".react-flow__minimap-node").first(),
  ).toBeVisible();
  await page
    .getByLabel("의존성 그래프 연결 그룹", { exact: true })
    .selectOption("a");
  await expect(graph.locator(".react-flow__node-entity")).toHaveCount(3);
  await expect(graph.locator(".react-flow__edge")).toHaveCount(3);
  await page.getByRole("button", { name: "크게 보기", exact: true }).click();
  await expect(graph.locator(".react-flow__edge-path")).toHaveCount(3);
  await page.screenshot({ path: "test-results/graph-audit-groups.png" });
  await page
    .getByRole("button", { name: "크게 보기 닫기", exact: true })
    .click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
});

test("existing VPC scan: full flow count and grouped attribute edges", async ({
  page,
  request,
}) => {
  const id = process.env.GRAPH_AUDIT_SCAN_ID;
  test.skip(!id, "Use an existing completed VPC scan for the visual audit");
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const first = await (
    await request.get(`${backend}/api/scans/${id}/flows?limit=50`)
  ).json();
  await page.goto(`/scans/${id}`);
  await page.getByRole("button", { name: /^데이터 흐름/ }).click();
  await page
    .getByRole("button", { name: "전체 연결 불러오기", exact: true })
    .click();
  await expect(
    page.getByText(
      `전체 일치 흐름 ${first.summary.total_flows}개 · 불러온 연결 ${first.summary.total_flows}개`,
      { exact: true },
    ),
  ).toBeVisible();
  const source = "var.outpost_inbound_acl_rules";
  const expected = await (
    await request.get(
      `${backend}/api/scans/${id}/flows?source=${encodeURIComponent(source)}&limit=50`,
    )
  ).json();
  expect(expected.edges.length).toBeGreaterThan(1);
  await page.getByLabel("Flow source", { exact: true }).fill(source);
  const graph = page.getByRole("img", {
    name: "데이터 흐름 그래프",
    exact: true,
  });
  await expect(graph.locator(".react-flow__node-entity")).toHaveCount(
    new Set(
      expected.nodes.map(
        (n: { entity_address: string; address: string }) =>
          n.entity_address || n.address,
      ),
    ).size,
  );
  await expect(graph.locator(".react-flow__edge-path")).toHaveCount(
    expected.edges.length,
  );
  await page
    .getByLabel("선언별 묶기 (속성 연결 유지)", { exact: true })
    .uncheck();
  await expect(graph.locator(".react-flow__node-entity")).toHaveCount(
    expected.nodes.length,
  );
  await expect(graph.locator(".react-flow__edge-path")).toHaveCount(
    expected.edges.length,
  );
  await page
    .getByLabel("선언별 묶기 (속성 연결 유지)", { exact: true })
    .check();
  await page.getByRole("button", { name: "크게 보기", exact: true }).click();
  await expect(graph.locator(".react-flow__edge-path")).toHaveCount(
    expected.edges.length,
  );
  await page.screenshot({ path: "test-results/vpc-flow-readability.png" });
  expect(errors).toEqual([]);
});
