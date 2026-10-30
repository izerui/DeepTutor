import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SimpleMarkdownRenderer from "@/components/common/SimpleMarkdownRenderer";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

describe("Markdown reading direction", () => {
  it("lets each paragraph take the direction of its own text", () => {
    render(<SimpleMarkdownRenderer content={"مرحبا بالعالم\n\nHello world"} variant="prose" />);
    expect(screen.getByText("مرحبا بالعالم")).toHaveAttribute("dir", "auto");
    expect(screen.getByText("Hello world")).toHaveAttribute("dir", "auto");
  });

  it("covers headings, list items, quotes and table cells", () => {
    const { container } = render(
      <SimpleMarkdownRenderer
        content={"# عنوان\n\n- عنصر\n\n> اقتباس\n\n| أ |\n| - |\n| ب |"}
        variant="prose"
      />,
    );
    for (const selector of ["h1", "li", "blockquote", "th", "td"]) {
      expect(container.querySelector(selector)).toHaveAttribute("dir", "auto");
    }
    expect(container.querySelector("table")).not.toHaveAttribute("dir");
    expect(container.querySelector(".md-renderer")).not.toHaveAttribute("dir");
  });
});
