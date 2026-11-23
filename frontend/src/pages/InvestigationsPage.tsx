import { useState } from "react";

import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardBody, CardHeader, EmptyState, Input, QueryState, Select } from "@/components/ui";
import { useInvestigations } from "@/features/investigations/api";
import { AskBox, InvestigationTable } from "@/features/investigations/components";
import { STATUS_LABELS } from "@/lib/status";

export function InvestigationsPage() {
  const investigations = useInvestigations();
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");

  return (
    <>
      <PageHeader
        title="Investigations"
        description="Ask a question about the production environment. The agent plans, gathers evidence with MCP tools, ranks hypotheses and reports with citations."
      />
      <Card className="mb-4">
        <CardHeader title="New investigation" />
        <CardBody>
          <AskBox />
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          title="History"
          actions={
            <div className="flex gap-2">
              <label htmlFor="inv-filter" className="sr-only">
                Filter investigations
              </label>
              <Input
                id="inv-filter"
                placeholder="Filter by question or root cause"
                value={text}
                onChange={(e) => {
                  setText(e.target.value);
                }}
                className="h-8 w-56"
              />
              <label htmlFor="inv-status" className="sr-only">
                Status
              </label>
              <Select
                id="inv-status"
                value={status}
                onChange={(e) => {
                  setStatus(e.target.value);
                }}
                className="h-8 w-40"
              >
                <option value="">All statuses</option>
                {Object.entries(STATUS_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </Select>
            </div>
          }
        />
        <CardBody>
          <QueryState
            query={investigations}
            isEmpty={(d) => !d.investigations.length}
            empty={<EmptyState title="No investigations yet" description="Start one above." />}
          >
            {(d) => {
              const needle = text.trim().toLowerCase();
              const rows = d.investigations.filter(
                (i) =>
                  (!status || i.status === status) &&
                  (!needle ||
                    i.question.toLowerCase().includes(needle) ||
                    (i.root_cause?.title.toLowerCase().includes(needle) ?? false)),
              );
              return rows.length ? (
                <InvestigationTable items={rows} />
              ) : (
                <EmptyState title="No matching investigations" />
              );
            }}
          </QueryState>
        </CardBody>
      </Card>
    </>
  );
}
