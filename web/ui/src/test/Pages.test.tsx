import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LoginPage } from "../LoginPage";
import { ConnectionsPage } from "../ConnectionsPage";
import { api } from "../api";

vi.mock("../api", () => ({
  api: { me: vi.fn(), login: vi.fn(), connections: vi.fn(), syncConnectorGet: vi.fn() },
}));

// stub window.location so redirects are observable, not executed
function stubLocation() {
  const loc: any = { href: "" };
  Object.defineProperty(window, "location", { value: loc, writable: true });
  return loc;
}

beforeEach(() => vi.clearAllMocks());

describe("LoginPage", () => {
  it("renders the sign-in button and redirects to dev login", async () => {
    (api.me as any).mockResolvedValue({
      logged_in: false, name: null, user_id: null, meta: {},
      options: [{ name: "Alex Rivera", user_id: "1" }], auth_mode: "dev",
    });
    (api.login as any).mockResolvedValue({ logged_in: true });
    const loc = stubLocation();
    render(<LoginPage />);
    const btn = await screen.findByRole("button", { name: /sign in/i });
    fireEvent.click(btn);
    await waitFor(() => expect(api.login).toHaveBeenCalled());
    await waitFor(() => expect(loc.href).toBe("/"));
  });

  it("redirects an already-logged-in user straight to /", async () => {
    (api.me as any).mockResolvedValue({ logged_in: true, name: "Alex", user_id: "1", meta: {}, options: [] });
    const loc = stubLocation();
    render(<LoginPage />);
    await waitFor(() => expect(loc.href).toBe("/"));
  });
});

describe("ConnectionsPage", () => {
  const rows = [
    { name: "box", label: "Box", emoji: "📦", configured: true, connected: true, indexed: 259, connect_url: null, setup_steps: [] },
    { name: "slack", label: "Slack", emoji: "💬", configured: false, connected: false, indexed: 0, connect_url: null, setup_steps: ["Create a Slack app", "Add redirect", "Set creds"] },
    { name: "github", label: "GitHub", emoji: "🐙", configured: true, connected: false, indexed: 3, connect_url: "/connect/github", setup_steps: [] },
  ];

  it("lists each connector with its status", async () => {
    (api.connections as any).mockResolvedValue({ logged_in: true, connectors: rows });
    render(<ConnectionsPage />);
    expect(await screen.findByText("Box")).toBeInTheDocument();
    expect(screen.getByText("Slack")).toBeInTheDocument();
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    // a configured-but-not-connected connector shows a Connect button
    expect(screen.getByRole("button", { name: /^connect$/i })).toBeInTheDocument();
  });

  it("reveals setup steps for an unconfigured connector", async () => {
    (api.connections as any).mockResolvedValue({ logged_in: true, connectors: rows });
    render(<ConnectionsPage />);
    const setupBtn = await screen.findByRole("button", { name: /setup/i });
    fireEvent.click(setupBtn);
    expect(await screen.findByText(/create a slack app/i)).toBeInTheDocument();
  });

  it("surfaces a not_configured sync result instead of a silent success", async () => {
    const connectedRow = { ...rows[0], name: "github", label: "GitHub", connected: true, indexed: 3 };
    (api.connections as any).mockResolvedValue({ logged_in: true, connectors: [connectedRow] });
    (api.syncConnectorGet as any).mockResolvedValue({ ok: false, reason: "not_configured", error: "github is not configured — connect it first" });
    render(<ConnectionsPage />);
    const syncBtn = await screen.findByRole("button", { name: /^sync$/i });
    fireEvent.click(syncBtn);
    expect(await screen.findByText(/not configured/i)).toBeInTheDocument();
  });
});
