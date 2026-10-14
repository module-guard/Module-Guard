"use client";
import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Download } from "lucide-react";
import { api, ApiError, unwrap } from "@/api/client";
import type { components } from "@/api/generated/schema";
import { Button } from "./ui/button";
type Detail = components["schemas"]["RepositoryDetail"];
type Version = components["schemas"]["VersionDetail"];
type Preview = components["schemas"]["UsagePreview"];
type Variable = {
  name: string;
  type?: string | null;
  default?: string | null;
  required?: boolean;
  description?: string | null;
  sensitive?: boolean;
};
type Values = Record<string, string | boolean>;
type Problem = { variable?: string; text: string };

// 서버(app/usage/hcl.py)와 같은 기준: string·number·bool 말고는 JSON으로 받는다.
function kindOf(type?: string | null) {
  const head = (type || "").trim();
  return head === "string" || head === "number" || head === "bool"
    ? head
    : "json";
}
const placeholder = (type?: string | null) =>
  /^(map|object)\b/.test((type || "").trim())
    ? '{"key": "value"}'
    : /^(list|set|tuple)\b/.test((type || "").trim())
      ? '["a", "b"]'
      : "JSON 값";

function Field({
  variable,
  value,
  problem,
  passed,
  onChange,
  onPass,
}: {
  variable: Variable;
  value: string | boolean | undefined;
  problem?: string;
  passed: boolean;
  onChange: (value: string | boolean | undefined) => void;
  onPass: (pass: boolean) => void;
}) {
  const id = `use-${variable.name}`;
  const kind = kindOf(variable.type);
  const text = typeof value === "string" ? value : "";
  return (
    <div>
      <label
        className="label flex flex-wrap items-baseline gap-x-2"
        htmlFor={id}
      >
        <span className="font-mono text-[13px] text-ink">{variable.name}</span>
        <span className="ml-2 font-mono text-[11px] font-normal text-slate-500">
          {variable.type || "any"}
        </span>
        {variable.required && (
          <span className="ml-2 text-[11px] font-semibold text-brand">
            필수
          </span>
        )}
      </label>
      {variable.description && (
        <p className="mb-1.5 text-xs text-slate-500">{variable.description}</p>
      )}
      {variable.sensitive ? (
        // 민감한 값은 폼으로 받지 않는다. 코드에는 var.<이름>만 들어간다.
        <div
          id={id}
          className="rounded-lg border border-line bg-[#fafbfe] px-3 py-2.5 text-xs text-slate-600"
        >
          <p>
            민감한 값이라 여기에 입력하지 않습니다. 실행할 때 환경 변수로
            넣으세요.
          </p>
          <code className="mt-1 block break-all font-mono text-[12px] text-ink">
            export TF_VAR_{variable.name}=…
          </code>
          {!variable.required && (
            <label className="mt-2 flex items-center gap-2">
              <input
                type="checkbox"
                checked={passed}
                onChange={(e) => onPass(e.target.checked)}
              />
              이 값을 직접 넣겠습니다 (기본값 대신)
            </label>
          )}
        </div>
      ) : kind === "bool" ? (
        <select
          id={id}
          className="input"
          value={value === undefined ? "" : String(value)}
          onChange={(e) =>
            onChange(
              e.target.value === "" ? undefined : e.target.value === "true",
            )
          }
        >
          <option value="">
            {variable.required ? "선택…" : `기본값 사용 (${variable.default})`}
          </option>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      ) : kind === "json" ? (
        <textarea
          id={id}
          className="input min-h-[72px] py-2 font-mono text-[13px]"
          spellCheck={false}
          placeholder={placeholder(variable.type)}
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      ) : (
        <input
          id={id}
          className="input"
          inputMode={kind === "number" ? "decimal" : undefined}
          spellCheck={false}
          autoComplete="off"
          value={text}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {!variable.sensitive && !variable.required && kind !== "bool" && (
        <p className="mt-1 break-all font-mono text-[11px] text-slate-500">
          기본값: {variable.default ?? "null"}
        </p>
      )}
      {kind === "json" && !variable.sensitive && (
        <p className="mt-1 text-[11px] text-slate-500">
          JSON으로 입력합니다. 값은 코드가 아니라 데이터로만 들어갑니다.
        </p>
      )}
      {problem && (
        <p role="alert" className="mt-1 text-xs text-red-700">
          {problem}
        </p>
      )}
    </div>
  );
}

function CodeBlock({ name, code }: { name: string; code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <figure className="overflow-hidden rounded-lg border border-line bg-white">
      <figcaption className="flex items-center justify-between border-b border-line px-4 py-2">
        <span className="font-mono text-xs text-slate-600">{name}</span>
        <button
          type="button"
          className="flex items-center gap-1 text-xs font-semibold text-slate-600 hover:text-brand"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(code);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            } catch {
              setCopied(false);
            }
          }}
        >
          {copied ? <Check size={13} /> : <Copy size={13} />}
          {copied ? "복사됨" : `${name} 복사`}
        </button>
      </figcaption>
      <pre
        aria-label={name}
        className="overflow-x-auto p-4 font-mono text-[12.5px] leading-relaxed text-ink"
      >
        {code}
      </pre>
    </figure>
  );
}

export function ModuleUse({
  detail,
  version,
}: {
  detail: Detail;
  version: Version;
}) {
  const variables = useMemo(
    () => ((version.variables || []) as Variable[]).filter((v) => v.name),
    [version.variables],
  );
  const required = variables.filter((v) => v.required);
  const optional = variables.filter((v) => !v.required);
  const [values, setValues] = useState<Values>({});
  const [passed, setPassed] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview>();
  const [problem, setProblem] = useState<Problem>();
  const [busy, setBusy] = useState(false);
  const path = { owner: detail.owner, name: detail.name };
  // 빈 칸은 보내지 않는다(기본값 사용).
  const body = useMemo(
    () => ({
      version: version.version || undefined,
      values: Object.fromEntries(
        Object.entries(values).filter(
          ([, value]) => typeof value === "boolean" || value.trim() !== "",
        ),
      ),
      pass_sensitive: passed,
    }),
    [values, passed, version.version],
  );
  const explain = (e: unknown, fallback: string): Problem => ({
    variable:
      e instanceof ApiError
        ? ((e.details as { variable?: string } | undefined)?.variable ??
          undefined)
        : undefined,
    text: e instanceof ApiError ? e.message : fallback,
  });

  useEffect(() => {
    const controller = new AbortController();
    // 입력이 멈춘 뒤에 한 번만 다시 만든다.
    const timer = setTimeout(
      async () => {
        try {
          const next = await unwrap(
            api.POST("/api/repositories/{owner}/{name}/usage", {
              signal: controller.signal,
              params: { path },
              body,
            }),
          );
          if (controller.signal.aborted) return;
          setPreview(next);
          setProblem(undefined);
        } catch (e) {
          if (!controller.signal.aborted)
            setProblem(explain(e, "코드를 만들지 못했습니다."));
        }
      },
      preview ? 400 : 0,
    );
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [body, detail.owner, detail.name]);

  const field = (variable: Variable) => (
    <Field
      key={variable.name}
      variable={variable}
      value={values[variable.name]}
      problem={problem?.variable === variable.name ? problem.text : undefined}
      passed={passed.includes(variable.name)}
      onChange={(value) =>
        setValues((current) => {
          const next = { ...current };
          if (value === undefined) delete next[variable.name];
          else next[variable.name] = value;
          return next;
        })
      }
      onPass={(pass) =>
        setPassed((current) =>
          pass
            ? [...current, variable.name]
            : current.filter((name) => name !== variable.name),
        )
      }
    />
  );

  async function download() {
    setBusy(true);
    try {
      const { data, response } = await api.POST(
        "/api/repositories/{owner}/{name}/download",
        { params: { path }, body, parseAs: "blob" },
      );
      if (!data) throw new ApiError(0, "내려받지 못했습니다.");
      // 파일 이름은 서버가 안전한 글자로만 만들어 준 것을 쓴다.
      const named = /filename="([A-Za-z0-9_.-]+)"/.exec(
        response.headers.get("content-disposition") || "",
      );
      const url = URL.createObjectURL(data as Blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = named?.[1] || preview?.filename || "module.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setProblem(undefined);
    } catch (e) {
      setProblem(explain(e, "내려받지 못했습니다."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <form
        aria-label="변수 입력"
        className="space-y-5"
        onSubmit={(e) => e.preventDefault()}
      >
        {variables.length === 0 && (
          <p className="text-slate-500">
            {version.variables
              ? "이 모듈은 입력 변수가 없습니다."
              : "변수 정보를 읽지 못해 폼을 만들 수 없습니다. 코드는 그대로 쓸 수 있습니다."}
          </p>
        )}
        {required.map(field)}
        {optional.length > 0 && (
          <details
            className="rounded-lg border border-line bg-white"
            open={required.length === 0}
          >
            <summary className="cursor-pointer px-4 py-3 font-semibold">
              고급 설정{" "}
              <span className="font-normal text-slate-500">
                (기본값이 있는 변수 {optional.length}개)
              </span>
            </summary>
            <div className="space-y-5 border-t border-line p-4">
              {optional.map(field)}
            </div>
          </details>
        )}
      </form>
      <section
        aria-label="사용 코드"
        className="space-y-4 lg:sticky lg:top-20 lg:self-start"
      >
        {problem && !problem.variable && (
          <p role="alert" className="rounded bg-red-50 px-4 py-3 text-red-800">
            {problem.text}
          </p>
        )}
        {preview ? (
          <>
            {preview.missing_required.length > 0 && (
              <p
                role="status"
                className="rounded bg-amber-50 px-4 py-3 text-amber-900"
              >
                필수 값 {preview.missing_required.length}개가 비어 있습니다:{" "}
                <span className="font-mono text-[12px]">
                  {preview.missing_required.join(", ")}
                </span>
                . 채우기 전에는 <code>terraform validate</code>가 통과하지
                않습니다.
              </p>
            )}
            <CodeBlock name="main.tf" code={preview.main_tf} />
            {preview.variables_tf && (
              <>
                <CodeBlock name="variables.tf" code={preview.variables_tf} />
                <p className="text-xs text-slate-600">
                  민감한 값 {preview.sensitive.length}개는 코드에 쓰지 않고{" "}
                  {preview.sensitive.map((name) => (
                    <code key={name} className="mr-1 font-mono text-[12px]">
                      TF_VAR_{name}
                    </code>
                  ))}
                  환경 변수로 넣습니다.
                </p>
              </>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" disabled={busy} onClick={download}>
                <Download size={15} />
                {busy ? "만드는 중…" : "zip 내려받기"}
              </Button>
              <span className="text-xs text-slate-500">
                {preview.filename} · <code>modules/</code>에 모듈 파일(
                {preview.version}), 루트에 위 코드
              </span>
            </div>
            <p className="text-xs text-slate-500">
              압축을 풀고 그 폴더에서 <code>terraform init</code>을 실행하면
              됩니다. 입력한 값은 서버에 저장되지 않습니다.
            </p>
            {preview.git_source && (
              <div className="rounded-lg border border-line bg-[#fafbfe] px-4 py-3 text-xs text-slate-600">
                <p>
                  내려받지 않고 GitHub에서 바로 쓰려면 <code>source</code>를
                  이렇게 바꿉니다(검사한 커밋에 고정).
                </p>
                <code className="mt-1 block break-all font-mono text-[12px] text-ink">
                  source = &quot;{preview.git_source}&quot;
                </code>
              </div>
            )}
          </>
        ) : (
          !problem && <p className="text-slate-500">코드를 만드는 중…</p>
        )}
      </section>
    </div>
  );
}
