import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { SidebarShell } from "@/components/sidebar/SidebarShell";
import * as sessionApi from "@/lib/session-api";
import { registerWorkspaceNavigator } from "@/lib/workspace-scope";

const fixture = vi.hoisted(() => ({ push: vi.fn(), close: vi.fn() }));

vi.mock("@/hooks/useAuthStatus", () => ({
  useAuthStatus: () => ({
    enabled: false,
    authenticated: false,
    isAdmin: false,
    userId: null,
    statusAvailable: true,
    loading: false,
  }),
}));

vi.mock("@/hooks/useChatWorkspaces", () => ({ useChatWorkspaces: () => ({ workspaces: [], error: "" }) }));

vi.mock("next/navigation", () => ({
  usePathname: () => "/co-writer",
  useRouter: () => ({ push: fixture.push }),
}));
vi.mock("next/image", () => ({ default: () => null }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("@/context/AppShellContext", () => ({
  useAppShell: () => ({
    sidebarCollapsed: false,
    setSidebarCollapsed: vi.fn(),
  }),
}));
vi.mock("@/components/layout/AppShell", () => ({
  useSidebarDrawer: () => ({ close: fixture.close }),
}));
vi.mock("@/hooks/useDevice", () => ({
  useDevice: () => ({ isMobile: false }),
}));
vi.mock("@/components/sidebar/VersionBadge", () => ({
  VersionBadge: () => null,
}));

it("loads conversation history while navigation remains usable, then preserves session actions", async () => {
  const onSelect = vi.fn();
  const onRename = vi.fn();
  const onOrganize = vi.fn();
  const { container } = render(
    <SidebarShell
      showSessions
      sessions={[
        {
          id: "session-1",
          session_id: "session-1",
          title: "Retained session",
          created_at: 1,
          updated_at: 1,
          message_count: 1,
          last_message: "Hello",
        },
      ]}
      onSelectSession={onSelect}
      onRenameSession={onRename}
      onDeleteSession={vi.fn()}
      onOrganizeSession={onOrganize}
    />,
  );
  expect(container.querySelector('[aria-busy="true"]')).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Co-Writer" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /More/ }));
  expect(screen.getByRole("link", { name: "Co-Writer" })).toHaveAttribute(
    "href",
    "/co-writer",
  );

  const history = await screen.findByText("Retained session");
  const scroller = history.closest(".overflow-y-auto");
  expect(scroller).toContainElement(
    screen.getByRole("link", { name: "Partners" }),
  );
  expect(scroller).not.toContainElement(
    screen.getByRole("link", { name: "Home" }),
  );
  expect(scroller).not.toContainElement(
    screen.getByRole("link", { name: "Settings" }),
  );

  fireEvent.click(await screen.findByText("Retained session"));
  expect(onSelect).toHaveBeenCalledWith("session-1");
  expect(fixture.close).toHaveBeenCalled();
  expect(container.querySelector('[aria-busy="true"]')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Conversation actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Pin" }));
  expect(onOrganize).toHaveBeenCalledWith("session-1", { pinned: true });

  fireEvent.click(screen.getByRole("button", { name: "Conversation actions" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "Rename chat" }));
  const input = screen.getByDisplayValue("Retained session");
  fireEvent.change(input, { target: { value: "Renamed session" } });
  await act(async () => {
    fireEvent.keyDown(input, { key: "Enter" });
  });
  expect(onRename).toHaveBeenCalledWith("session-1", "Renamed session");
});

it("keeps Home pinned even when a previous layout folded it into More", () => {
  localStorage.setItem(
    "deeptutor.sidebar.navLayout",
    JSON.stringify({
      order: ["/partners", "/chat", "/space"],
      collapsed: ["/chat"],
    }),
  );
  render(<SidebarShell />);
  // Spec 2026-11-03 batch 1 §2: Home (/start) and Chat are both pinned above the modules.
  expect(screen.getAllByRole("link", { name: "Home" })).toHaveLength(1);
  expect(screen.getAllByRole("link", { name: "Chat" })).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Arrange Chat" })).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Chat" }));
  expect(fixture.push).toHaveBeenCalledWith("/chat");
});

it("finds Little Tutor by thread or parent title and opens the matching workspace's child", async () => {
  const parent = {
    id: "p", session_id: "p", title: "Operating systems", content_workspace_id: "course",
    created_at: 1, updated_at: 1, message_count: 1, last_message: "",
  };
  const thread = {
    ...parent, id: "t", session_id: "t", title: "Fork returns twice",
    preferences: { parent_session_id: "p", session_kind: "selection_tutor" as const },
  };
  render(<SidebarShell showSessions sessions={[
    { ...thread, title: "Other thread", content_workspace_id: "" },
    parent, thread,
    { ...thread, id: "arch", session_id: "arch", title: "Archived fork",
      preferences: { ...thread.preferences, archived: true } },
  ]} onSelectSession={vi.fn()} onRenameSession={vi.fn()} onDeleteSession={vi.fn()} />);
  expect(screen.queryByText(thread.title)).toBeNull();
  const input = screen.getByRole("searchbox", { name: "Search conversations..." });
  fireEvent.change(input, { target: { value: " FORK " } });
  const link = screen.getByRole("link", { name: /Fork returns twice/ });
  expect(link).toHaveAttribute("href", "/chat/t?dt_workspace=course");
  expect(screen.queryByText("Archived fork")).toBeNull();
  fireEvent.change(input, { target: { value: "systems" } });
  expect(screen.getByRole("link", { name: /Fork returns twice/ })).toBeInTheDocument();
  expect(screen.queryByText("Other thread")).toBeNull();
  fireEvent.change(input, { target: { value: "" } });
  expect(screen.queryByText(thread.title)).toBeNull();
  expect(screen.getByText(parent.title)).toBeInTheDocument();
});

it("resizes with the keyboard, persists across mounts, and resets on double click", () => {
  const first = render(<SidebarShell />);
  const handle = screen.getByRole("separator", { name: "Resize sidebar" });
  fireEvent.keyDown(handle, { key: "ArrowRight" });
  expect(handle).toHaveAttribute("aria-valuenow", "230");
  expect(
    first.container
      .querySelector("aside")!
      .style.getPropertyValue("--sidebar-width"),
  ).toBe("230px");
  first.unmount();
  render(<SidebarShell />);
  const restored = screen.getByRole("separator", { name: "Resize sidebar" });
  expect(restored).toHaveAttribute("aria-valuenow", "230");
  fireEvent.keyDown(restored, { key: "Home" });
  fireEvent.keyDown(restored, { key: "ArrowLeft" });
  expect(restored).toHaveAttribute("aria-valuenow", "220");
  fireEvent.keyDown(restored, { key: "End" });
  fireEvent.keyDown(restored, { key: "ArrowRight" });
  expect(restored.getAttribute("aria-valuenow")).toBe(
    restored.getAttribute("aria-valuemax"),
  );
  fireEvent.doubleClick(restored);
  expect(restored).toHaveAttribute("aria-valuenow", "220");
});

it("opens a transcript match absent from the recent page in its owning workspace", async () => {
  const result: sessionApi.SessionSearchResult = {
    id: "old", session_id: "old", title: "Earlier follow-up", content_workspace_id: "course",
    preferences: { session_kind: "selection_tutor" as const, parent_session_id: "p" },
    created_at: 1, updated_at: 1, message_count: 2, last_message: "Thanks",
    match_excerpt: "posterior", match_role: "user", match_message_id: 1, match_created_at: 1,
  };
  const search = vi.spyOn(sessionApi, "searchAllSessions").mockResolvedValue([result]);
  const push = vi.fn();
  const unregister = registerWorkspaceNavigator(push);
  try {
    render(<SidebarShell showSessions sessions={[]} onSelectSession={vi.fn()}
      onRenameSession={vi.fn()} onDeleteSession={vi.fn()} />);
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "posterior" } });
    const link = await screen.findByRole("link", { name: /Earlier follow-up/ });
    expect(search).toHaveBeenCalledWith("posterior", expect.any(AbortSignal), { allWorkspaces: true });
    await act(async () => { fireEvent.click(link); });
    expect(push).toHaveBeenCalledWith("/chat/old?dt_workspace=course");
  } finally {
    unregister();
    search.mockRestore();
  }
});
