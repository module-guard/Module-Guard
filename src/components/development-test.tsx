"use client";
import { useState } from "react";
import Link from "next/link";
import { api, unwrap } from "@/api/client";
import { Panel, Badge } from "./live-shared";
import { SystemStatus } from "./system-status";
import { ScanResult } from "./scan-result";
import { TraceForm } from "./analysis-tabs";
export function DevelopmentTest() {
  const [source, setSource] = useState("");
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checks, setChecks] = useState<Record<string, string>>({});
  async function start(demo = false) {
    setBusy(true);
    setError("");
    try {
      const result = demo
        ? await unwrap(api.POST("/api/dev/demo/correlation"))
        : await unwrap(api.POST("/api/scans", { body: { source } }));
      setId(result.scan_id);
      setChecks({});
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function check() {
    setChecks({});
    const params = { params: { path: { scan_id: id } } };
    const calls = {
      Scan: () => unwrap(api.GET("/api/scans/{scan_id}", params)),
      Findings: () => unwrap(api.GET("/api/scans/{scan_id}/findings", params)),
      Graph: () => unwrap(api.GET("/api/scans/{scan_id}/graph", params)),
      Dependencies: () =>
        unwrap(api.GET("/api/scans/{scan_id}/dependencies", params)),
      Flows: () => unwrap(api.GET("/api/scans/{scan_id}/flows", params)),
      Correlations: () =>
        unwrap(api.GET("/api/scans/{scan_id}/correlations", params)),
    };
    await Promise.all(
      Object.entries(calls).map(async ([name, call]) => {
        try {
          await call();
          setChecks((c) => ({ ...c, [name]: "성공" }));
        } catch (e) {
          setChecks((c) => ({ ...c, [name]: (e as Error).message }));
        }
      }),
    );
  }
  return (
    <>
      <h1>개발 검증 모드</h1>
      <p className="my-4 rounded bg-amber-50 px-4 py-3 text-amber-800">
        개발 모드 · Fixture 시나리오는 실제 GitHub 검사가 아님
      </p>
      <SystemStatus />
      <Panel title="저장소 검사">
        <button
          className="mb-3 text-brand"
          onClick={() =>
            setSource(
              "https://github.com/terraform-aws-modules/terraform-aws-vpc",
            )
          }
        >
          terraform-aws-vpc 예제 주소 입력
        </button>
        <label className="label">
          저장소 주소
          <input
            className="input mt-2"
            value={source}
            onChange={(e) => setSource(e.target.value)}
          />
        </label>
        <div className="mt-4 flex gap-4">
          <button
            className="rounded bg-brand px-4 py-2 text-white"
            disabled={busy || !source}
            onClick={() => start()}
          >
            검사 시작
          </button>
          <button
            className="rounded border border-violet-200 px-4 py-2 text-brand"
            disabled={busy}
            onClick={() => start(true)}
          >
            상관 위험 데모 실행
          </button>
        </div>
        {error && (
          <p role="alert" className="mt-3 text-red-700">
            {error}
          </p>
        )}
      </Panel>
      <Panel title="검사 · API 결과">
        <label className="label">
          검사 ID
          <input
            className="input mt-2"
            value={id}
            onChange={(e) => setId(e.target.value)}
          />
        </label>
        <button className="my-3 text-brand" disabled={!id} onClick={check}>
          API 결과 확인
        </button>
        <div className="flex flex-wrap gap-3">
          {Object.entries(checks).map(([k, v]) => (
            <span key={k}>
              {k} <Badge value={v} />
            </span>
          ))}
        </div>
      </Panel>
      {id && (
        <>
          <Link className="text-brand" href={`/scans/${id}`}>
            검사 결과 페이지 열기 →
          </Link>
          <ScanResult key={id} id={id} />
          <TraceForm key={`trace:${id}`} id={id} />
        </>
      )}
    </>
  );
}
