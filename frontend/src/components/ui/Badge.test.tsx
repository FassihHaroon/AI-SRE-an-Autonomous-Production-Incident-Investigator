import { render, screen } from "@testing-library/react";

import { Badge } from "./Badge";

describe("Badge", () => {
  it("renders children with the tone's classes", () => {
    render(<Badge tone="danger">Down</Badge>);
    const badge = screen.getByText("Down");
    expect(badge).toHaveClass("bg-danger-soft", "text-danger");
  });

  it("defaults to the neutral tone and merges extra classes", () => {
    render(<Badge className="ml-2">Idle</Badge>);
    expect(screen.getByText("Idle")).toHaveClass("bg-neutral-soft", "ml-2");
  });
});
