import { renderToStaticMarkup } from "react-dom/server";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import Sidebar from "@/components/sidebar";

const nav = vi.hoisted(() => ({
  usePathname: vi.fn(() => "/windows"),
}));

vi.mock("next/navigation", () => ({
  usePathname: nav.usePathname,
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { children?: ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("Sidebar navigation", () => {
  it("presents the v2 operator navigation options", () => {
    const markup = renderToStaticMarkup(<Sidebar />);
    expect(markup).toContain('href="/"');
    expect(markup).toContain('href="/windows"');
    expect(markup).toContain('href="/sms"');
    expect(markup).toContain('href="/data"');
    expect(markup).toContain('href="/settings"');
  });

  it("no longer presents the legacy Statistics page as a v2 navigation option", () => {
    const markup = renderToStaticMarkup(<Sidebar />);
    expect(markup).toContain(">Windows<");
    expect(markup).not.toContain('/href="/statistics"');
    expect(markup).not.toContain(">Statistics<");
  });
});