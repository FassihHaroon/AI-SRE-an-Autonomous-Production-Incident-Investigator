import { Search } from "lucide-react";
import { useState, type SyntheticEvent } from "react";
import { Link, useSearchParams } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Input,
  LoadingBlock,
  QueryState,
  Select,
  Spinner,
  Tabs,
} from "@/components/ui";
import { useInvestigations } from "@/features/investigations/api";
import { InvestigationTable } from "@/features/investigations/components";
import { useIncidentSearch } from "@/features/knowledge/api";
import { titleCase } from "@/lib/format";
import { severityTone } from "@/lib/status";
import type { IncidentDoc } from "@/lib/types";

const SERVICES = ["api-gateway", "orders-service", "fulfilment-worker"];
const SEVERITIES = ["SEV1", "SEV2", "SEV3"];
const CATEGORIES = [
  "bad_deploy",
  "failed_deploy",
  "config_change",
  "database_degradation",
  "cache_failure",
  "dependency_failure",
  "capacity",
  "infrastructure",
];

function IncidentRow({ doc }: { doc: IncidentDoc }) {
  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <Link
          to={`/incidents/${doc.document_id}`}
          className="font-medium text-ink hover:text-brand-strong"
        >
          {doc.title}
        </Link>
        {doc.severity ? <Badge tone={severityTone(doc.severity)}>{doc.severity}</Badge> : null}
        {doc.score !== undefined ? <Badge tone="brand">score {doc.score.toFixed(3)}</Badge> : null}
      </div>
      <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted">
        <span className="font-mono">{doc.incident_id ?? doc.document_id}</span>
        {doc.date ? <span>{doc.date}</span> : null}
        {doc.category ? <span>{titleCase(doc.category)}</span> : null}
        <span>{doc.services.join(", ")}</span>
      </div>
      {doc.snippet ? <p className="mt-1 line-clamp-2 text-xs text-ink/75">{doc.snippet}</p> : null}
    </li>
  );
}

function KnowledgeBase() {
  const [params, setParams] = useSearchParams();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const filters = {
    q: params.get("q") ?? undefined,
    service: params.get("service") ?? undefined,
    severity: params.get("severity") ?? undefined,
    category: params.get("category") ?? undefined,
  };
  const search = useIncidentSearch(filters);

  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    set("q", draft.trim());
  };

  return (
    <Card>
      <CardHeader
        title="Postmortem knowledge base"
        description="Hybrid semantic + keyword search over past incidents (the same retrieval the agent uses)"
        actions={search.isFetching && !search.isPending ? <Spinner label="Searching" /> : null}
      />
      <CardBody className="space-y-3">
        <form onSubmit={submit} className="flex flex-wrap gap-2" role="search">
          <label htmlFor="kb-q" className="sr-only">
            Search incidents
          </label>
          <Input
            id="kb-q"
            value={draft}
            placeholder="e.g. connection pool exhausted after deploy"
            onChange={(e) => {
              setDraft(e.target.value);
            }}
            className="min-w-64 flex-1"
          />
          <Button type="submit" size="md" variant="secondary">
            <Search className="size-4" aria-hidden="true" />
            Search
          </Button>
          {(["service", "severity", "category"] as const).map((key) => (
            <div key={key}>
              <label htmlFor={`kb-${key}`} className="sr-only">
                {titleCase(key)}
              </label>
              <Select
                id={`kb-${key}`}
                value={params.get(key) ?? ""}
                onChange={(e) => {
                  set(key, e.target.value);
                }}
                className="w-44"
              >
                <option value="">Any {key}</option>
                {(key === "service" ? SERVICES : key === "severity" ? SEVERITIES : CATEGORIES).map(
                  (v) => (
                    <option key={v} value={v}>
                      {key === "category" ? titleCase(v) : v}
                    </option>
                  ),
                )}
              </Select>
            </div>
          ))}
        </form>
        <QueryState
          query={search}
          loading={<LoadingBlock rows={6} />}
          isEmpty={(d) => !d.incidents.length}
          empty={
            <EmptyState
              title="No incidents match"
              description="Try a broader query or clear the filters."
            />
          }
        >
          {(d) => (
            <>
              {d.query ? (
                <p className="text-xs text-muted">
                  {d.incidents.length} result(s) for “{d.query}”
                  {d.confident === false ? (
                    <Badge tone="warn" className="ml-2">
                      low-confidence match
                    </Badge>
                  ) : null}
                </p>
              ) : null}
              <ul className="divide-y divide-line">
                {d.incidents.map((doc) => (
                  <IncidentRow key={`${doc.document_id}-${doc.section ?? ""}`} doc={doc} />
                ))}
              </ul>
            </>
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

export function IncidentsPage() {
  const [tab, setTab] = useState<"kb" | "ai">("kb");
  const investigations = useInvestigations();
  return (
    <>
      <PageHeader
        title="Incident history"
        description="Past postmortems from the knowledge base and investigations run by the agent."
      />
      <Tabs
        className="mb-4"
        value={tab}
        onChange={setTab}
        items={[
          { key: "kb", label: "Postmortems" },
          {
            key: "ai",
            label: "Agent investigations",
            count: investigations.data?.investigations.length,
          },
        ]}
      />
      {tab === "kb" ? (
        <KnowledgeBase />
      ) : (
        <Card>
          <CardBody>
            <QueryState
              query={investigations}
              isEmpty={(d) => !d.investigations.length}
              empty={<EmptyState title="No investigations yet" />}
            >
              {(d) => <InvestigationTable items={d.investigations} />}
            </QueryState>
          </CardBody>
        </Card>
      )}
    </>
  );
}
