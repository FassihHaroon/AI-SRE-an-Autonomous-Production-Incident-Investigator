import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  LoadingBlock,
  QueryState,
} from "@/components/ui";
import { useIncident } from "@/features/knowledge/api";
import { titleCase } from "@/lib/format";
import { severityTone } from "@/lib/status";

export function IncidentDetailPage() {
  const { documentId = "" } = useParams();
  const incident = useIncident(documentId);
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Incident history", to: "/incidents" }, { label: documentId }]}
        title={incident.data?.incident.title ?? documentId}
        description={
          incident.data ? (
            <span className="flex flex-wrap items-center gap-2">
              {incident.data.incident.severity ? (
                <Badge tone={severityTone(incident.data.incident.severity)}>
                  {incident.data.incident.severity}
                </Badge>
              ) : null}
              {incident.data.incident.category ? (
                <Badge>{titleCase(incident.data.incident.category)}</Badge>
              ) : null}
              <span>{incident.data.incident.date}</span>
              <span className="font-mono text-faint">{incident.data.incident.source}</span>
            </span>
          ) : undefined
        }
      />
      <QueryState
        query={incident}
        loading={<LoadingBlock rows={8} />}
        errorTitle="Incident not available"
      >
        {(d) => (
          <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
            <Card>
              <CardHeader title="Postmortem" />
              <CardBody className="space-y-4">
                {d.sections.map((s) => {
                  const heading = s.section.split(" > ").slice(1).join(" › ");
                  return (
                    <section key={s.section}>
                      {heading ? (
                        <h3 className="mb-1 text-sm font-semibold text-ink">{heading}</h3>
                      ) : null}
                      <p className="text-sm leading-relaxed whitespace-pre-line text-ink/85">
                        {s.content}
                      </p>
                    </section>
                  );
                })}
              </CardBody>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader
                  title="Similar incidents"
                  description="Nearest neighbours by embedding similarity"
                />
                <CardBody>
                  {d.similar.length ? (
                    <ul className="space-y-2">
                      {d.similar.map((s) => (
                        <li key={s.document_id}>
                          <Link
                            to={`/incidents/${s.document_id}`}
                            className="text-sm text-ink hover:text-brand-strong"
                          >
                            {s.title}
                          </Link>
                          <div className="text-xs text-muted">
                            <span className="font-mono">{s.document_id}</span>
                            {s.category ? ` · ${titleCase(s.category)}` : ""}
                            {s.similarity !== undefined
                              ? ` · similarity ${s.similarity.toFixed(2)}`
                              : ""}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <EmptyState title="No similar incidents" />
                  )}
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Metadata" />
                <CardBody className="space-y-2 text-xs">
                  <div className="flex flex-wrap gap-1">
                    {d.incident.services.map((s) => (
                      <Link key={s} to={`/services/${s}`}>
                        <Badge>{s}</Badge>
                      </Link>
                    ))}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {d.incident.tags.map((t) => (
                      <Badge key={t} tone="info">
                        {t}
                      </Badge>
                    ))}
                  </div>
                </CardBody>
              </Card>
            </div>
          </div>
        )}
      </QueryState>
    </>
  );
}
