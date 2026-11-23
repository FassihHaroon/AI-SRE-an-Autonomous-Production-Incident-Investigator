import type { ReactNode } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  JsonView,
  KeyValue,
  LoadingBlock,
  QueryState,
} from "@/components/ui";
import { useToolCall } from "@/features/investigations/api";

export function ToolCallPage() {
  const { id = "", evidenceId = "" } = useParams();
  const call = useToolCall(id, evidenceId);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Investigations", to: "/investigations" },
          { label: id, to: `/investigations/${id}` },
          { label: evidenceId },
        ]}
        title={call.data ? `${call.data.server}.${call.data.tool}` : `Tool call ${evidenceId}`}
        description="One MCP tool call made by the agent: what it asked, what came back."
      />
      <QueryState
        query={call}
        loading={<LoadingBlock rows={8} />}
        errorTitle="Tool call not available"
      >
        {(c) => (
          <div className="space-y-4">
            <Card>
              <CardHeader
                title="Execution"
                actions={
                  c.ok ? <Badge tone="ok">Succeeded</Badge> : <Badge tone="danger">Failed</Badge>
                }
              />
              <CardBody>
                <KeyValue
                  items={[
                    ["Evidence id", <span className="font-mono">{c.id}</span>],
                    ["MCP server", <span className="font-mono">{c.server}</span>],
                    ["Tool", <span className="font-mono">{c.tool}</span>],
                    ["Round", c.round],
                    ["Started", new Date(c.at).toLocaleString([], { hour12: false })],
                    ["Duration", c.duration_ms !== null ? `${Math.round(c.duration_ms)} ms` : "—"],
                    ["Summary", c.summary],
                    ...(c.ok
                      ? []
                      : ([
                          [
                            "Error code",
                            <span className="font-mono text-danger">{c.error_code}</span>,
                          ],
                          ["Error", <span className="text-danger">{c.error_message}</span>],
                        ] as [string, ReactNode][])),
                    [
                      "Investigation",
                      <Link
                        to={`/investigations/${id}`}
                        className="font-mono text-brand-strong hover:underline"
                      >
                        {id}
                      </Link>,
                    ],
                  ]}
                />
              </CardBody>
            </Card>
            <div className="grid gap-4 xl:grid-cols-[1fr_2fr]">
              <Card>
                <CardHeader title="Arguments" />
                <CardBody>
                  <JsonView value={c.arguments} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Response" description="Structured result returned by the tool" />
                <CardBody>
                  {c.data === null || c.data === undefined ? (
                    <p className="text-xs text-muted">No structured data was returned.</p>
                  ) : (
                    <JsonView value={c.data} />
                  )}
                </CardBody>
              </Card>
            </div>
          </div>
        )}
      </QueryState>
    </>
  );
}
