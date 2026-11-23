import { createBrowserRouter } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { ApprovalsPage } from "@/pages/ApprovalsPage";
import { IncidentDetailPage } from "@/pages/IncidentDetailPage";
import { IncidentsPage } from "@/pages/IncidentsPage";
import { InvestigationPage } from "@/pages/InvestigationPage";
import { InvestigationsPage } from "@/pages/InvestigationsPage";
import { MetricsPage } from "@/pages/MetricsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { OverviewPage } from "@/pages/OverviewPage";
import { ServicePage } from "@/pages/ServicePage";
import { ServicesPage } from "@/pages/ServicesPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { ToolCallPage } from "@/pages/ToolCallPage";

export const routes = [
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "investigations", element: <InvestigationsPage /> },
      { path: "investigations/:id", element: <InvestigationPage /> },
      { path: "investigations/:id/tools/:evidenceId", element: <ToolCallPage /> },
      { path: "approvals", element: <ApprovalsPage /> },
      { path: "services", element: <ServicesPage /> },
      { path: "services/:name", element: <ServicePage /> },
      { path: "metrics", element: <MetricsPage /> },
      { path: "incidents", element: <IncidentsPage /> },
      { path: "incidents/:documentId", element: <IncidentDetailPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routes);
