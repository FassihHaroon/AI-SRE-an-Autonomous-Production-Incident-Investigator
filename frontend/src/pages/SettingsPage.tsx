import { CheckCircle2, XCircle } from "lucide-react";
import { useState, type SyntheticEvent } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  ErrorState,
  Field,
  Input,
  KeyValue,
  LoadingBlock,
  QueryState,
  Select,
} from "@/components/ui";
import { useSaveAgentSettings, useSettings } from "@/features/knowledge/api";
import type { AgentSettings, PlatformSettings } from "@/lib/types";

const LIMITS: {
  key: Exclude<keyof AgentSettings, "reasoner">;
  label: string;
  min: number;
  max: number;
  step: number;
  hint: string;
}[] = [
  {
    key: "max_rounds",
    label: "Max investigation rounds",
    min: 1,
    max: 6,
    step: 1,
    hint: "Plan → evidence → hypotheses loops",
  },
  {
    key: "max_tool_calls",
    label: "Max tool calls",
    min: 4,
    max: 80,
    step: 1,
    hint: "Across all rounds",
  },
  {
    key: "time_budget_s",
    label: "Time budget (s)",
    min: 30,
    max: 1800,
    step: 10,
    hint: "Wall-clock limit for one run",
  },
  {
    key: "sufficient_confidence",
    label: "Stop at confidence",
    min: 0.5,
    max: 0.95,
    step: 0.05,
    hint: "Stop early once the leading hypothesis reaches this",
  },
  {
    key: "verification_wait_s",
    label: "Verification wait (s)",
    min: 0,
    max: 600,
    step: 5,
    hint: "Time to wait after a remediation before re-checking signals",
  },
];

function AgentForm({ settings }: { settings: PlatformSettings }) {
  const [form, setForm] = useState<AgentSettings>(settings.agent);
  const save = useSaveAgentSettings();
  const dirty = JSON.stringify(form) !== JSON.stringify(settings.agent);

  const invalid = LIMITS.find((l) => {
    const v = form[l.key];
    return Number.isNaN(v) || v < l.min || v > l.max;
  });

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (invalid) return;
    save.mutate(form);
  };

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Agent limits">
      <Field
        label="Reasoner"
        htmlFor="reasoner"
        hint={
          form.reasoner === "llm"
            ? `Uses ${settings.llm.provider} (${settings.llm.model ?? "default model"}) for planning and hypotheses; confidence stays rubric-based.`
            : "Deterministic rule-based planner; no LLM tokens are used."
        }
      >
        <Select
          id="reasoner"
          value={form.reasoner}
          onChange={(e) => {
            setForm({ ...form, reasoner: e.target.value as AgentSettings["reasoner"] });
          }}
        >
          <option value="rules">Rules (deterministic)</option>
          <option value="llm" disabled={!settings.llm.api_key_configured}>
            LLM{settings.llm.api_key_configured ? "" : " (API key missing)"}
          </option>
        </Select>
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        {LIMITS.map((l) => {
          const v = form[l.key];
          const bad = Number.isNaN(v) || v < l.min || v > l.max;
          return (
            <Field
              key={l.key}
              label={l.label}
              htmlFor={l.key}
              hint={`${l.hint} (${l.min}–${l.max})`}
              error={bad ? `Must be between ${l.min} and ${l.max}` : null}
            >
              <Input
                id={l.key}
                type="number"
                min={l.min}
                max={l.max}
                step={l.step}
                value={Number.isNaN(v) ? "" : v}
                aria-invalid={bad}
                onChange={(e) => {
                  setForm({ ...form, [l.key]: e.target.valueAsNumber });
                }}
              />
            </Field>
          );
        })}
      </div>
      {save.error ? <ErrorState error={save.error} title="Settings were not saved" /> : null}
      <div className="flex items-center gap-3">
        <Button type="submit" size="sm" disabled={!dirty || Boolean(invalid) || save.isPending}>
          {save.isPending ? "Saving…" : "Save limits"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={!dirty}
          onClick={() => {
            setForm(settings.agent);
            save.reset();
          }}
        >
          Reset
        </Button>
        {save.isSuccess && !dirty ? (
          <span role="status" className="text-xs text-ok">
            Saved. New investigations use these limits.
          </span>
        ) : null}
      </div>
    </form>
  );
}

function Check({ ok, label }: { ok: boolean; label?: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 text-ok">
      <CheckCircle2 className="size-3.5" aria-hidden="true" />
      {label ?? "Yes"}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-danger">
      <XCircle className="size-3.5" aria-hidden="true" />
      {label ?? "No"}
    </span>
  );
}

export function SettingsPage() {
  const settings = useSettings();
  return (
    <>
      <PageHeader
        title="Settings"
        description="LLM provider, integrations, environment and agent limits."
      />
      <QueryState query={settings} loading={<LoadingBlock rows={10} />}>
        {(s) => (
          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader
                title="Agent limits"
                description="Applied to investigations started after saving"
              />
              <CardBody>
                <AgentForm key={JSON.stringify(s.agent)} settings={s} />
              </CardBody>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader title="Environment" />
                <CardBody>
                  <KeyValue
                    items={[
                      ["Name", s.environment.name],
                      [
                        "Mode",
                        s.environment.simulated ? (
                          <Badge tone="warn">Simulated production</Badge>
                        ) : (
                          "Production"
                        ),
                      ],
                      ["Version", s.environment.version],
                      ["Graph checkpoints", s.integrations.checkpointer],
                      ["Event store", s.integrations.event_store],
                    ]}
                  />
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="LLM provider" />
                <CardBody>
                  <KeyValue
                    items={[
                      ["Provider", s.llm.provider],
                      ["Model", s.llm.model ?? "—"],
                      [
                        "API key",
                        <Check
                          ok={s.llm.api_key_configured}
                          label={s.llm.api_key_configured ? "Configured (server-side)" : "Missing"}
                        />,
                      ],
                    ]}
                  />
                </CardBody>
              </Card>
            </div>
            <Card className="xl:col-span-2">
              <CardHeader
                title="Integrations"
                description="MCP tool servers and the telemetry sources behind them"
                actions={
                  <Check
                    ok={s.integrations.mcp.reachable}
                    label={s.integrations.mcp.reachable ? "MCP reachable" : "MCP unreachable"}
                  />
                }
              />
              <CardBody className="space-y-4">
                <KeyValue
                  items={[
                    ["MCP endpoint", <span className="font-mono">{s.integrations.mcp.url}</span>],
                    ["Prometheus", <span className="font-mono">{s.integrations.prometheus}</span>],
                    ["Loki", <span className="font-mono">{s.integrations.loki}</span>],
                    ["Deployer", <span className="font-mono">{s.integrations.deployer}</span>],
                    [
                      "Remediation",
                      <Check
                        ok={s.integrations.remediation_enabled}
                        label={
                          s.integrations.remediation_enabled
                            ? "Enabled (approval required)"
                            : "Disabled"
                        }
                      />,
                    ],
                  ]}
                />
                {s.integrations.mcp.error ? (
                  <ErrorState
                    error={new Error(s.integrations.mcp.error)}
                    title="MCP registry unreachable"
                  />
                ) : null}
                {s.integrations.mcp.servers.length ? (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line text-left text-xs text-muted">
                        <th className="py-1.5 pr-3 font-medium">Server</th>
                        <th className="py-1.5 pr-3 font-medium">Enabled</th>
                        <th className="py-1.5 pr-3 font-medium">Access</th>
                        <th className="py-1.5 text-right font-medium">Tools</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.integrations.mcp.servers.map((srv) => (
                        <tr key={srv.name} className="border-b border-line/60 last:border-0">
                          <td className="py-1.5 pr-3 font-mono text-xs">{srv.name}</td>
                          <td className="py-1.5 pr-3 text-xs">
                            <Check ok={srv.enabled} />
                          </td>
                          <td className="py-1.5 pr-3">
                            {srv.read_only ? (
                              <Badge>read-only</Badge>
                            ) : (
                              <Badge tone="warn">writes (gated)</Badge>
                            )}
                          </td>
                          <td className="py-1.5 text-right tabular">{srv.tools}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : null}
              </CardBody>
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}
