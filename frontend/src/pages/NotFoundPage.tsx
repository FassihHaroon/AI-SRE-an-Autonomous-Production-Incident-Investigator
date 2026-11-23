import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Page not found" description="That page does not exist." />
      <Link to="/" className="text-sm font-medium text-brand hover:underline">
        Back to overview
      </Link>
    </>
  );
}
