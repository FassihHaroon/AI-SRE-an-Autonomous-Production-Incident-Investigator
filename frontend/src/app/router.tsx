import { createBrowserRouter } from "react-router";

import { AppShell } from "@/components/layout/AppShell";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { OverviewPage } from "@/pages/OverviewPage";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
]);
