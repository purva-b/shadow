import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ChatWidget } from "../ChatWidget";
import { api } from "../api";

vi.mock("../api", () => ({
  api: {
    me: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    ask: vi.fn(),
  },
}));

const loggedIn = {
  logged_in: true, name: "Alex Rivera", user_id: "1",
  meta: { initials: "AR" }, options: [], auth_mode: "dev",
};
const loggedOut = {
  logged_in: false, name: null, user_id: null, meta: {},
  options: [{ name: "Alex Rivera", user_id: "1" }], auth_mode: "dev",
};

const answer = {
  answer_markdown: "Idempotency keys on the charge endpoint.",
  citations: [], decision_status: null, who_to_ask: null, restricted_note: null,
};

beforeEach(() => vi.clearAllMocks());

describe("ChatWidget", () => {
  it("shows the launcher and opens the popup on click", async () => {
    (api.me as any).mockResolvedValue(loggedIn);
    render(<ChatWidget />);
    const launcher = screen.getByLabelText("Open Shadow");
    expect(launcher).toBeInTheDocument();
    await waitFor(() => expect(api.me).toHaveBeenCalled());
    fireEvent.click(launcher);
    // the greeting (pun tagline) appears once open + logged in
    expect(await screen.findByText(/here to un-haunt you/i, { exact: false }))
      .toBeInTheDocument();
  });

  it("shows the login gate when signed out (no composer)", async () => {
    (api.me as any).mockResolvedValue(loggedOut);
    render(<ChatWidget />);
    fireEvent.click(screen.getByLabelText("Open Shadow"));
    expect(await screen.findByText(/please sign in/i)).toBeInTheDocument();
    // signed-out: no ask composer rendered
    expect(screen.queryByPlaceholderText(/ask about an issue/i)).toBeNull();
  });

  it("asks a question and renders the streamed answer", async () => {
    (api.me as any).mockResolvedValue(loggedIn);
    (api.ask as any).mockResolvedValue(answer);
    render(<ChatWidget />);
    fireEvent.click(screen.getByLabelText("Open Shadow"));
    // start a new chat so the composer is present
    fireEvent.click(await screen.findByLabelText("New chat"));
    const input = await screen.findByPlaceholderText(/ask about an issue/i);
    fireEvent.change(input, { target: { value: "retry fix?" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(api.ask).toHaveBeenCalledWith("retry fix?", []));
    // the user's question renders as a message bubble (not just the header)
    await waitFor(() =>
      expect(document.querySelector(".sw-me")?.textContent).toBe("retry fix?"));
    // the streamed answer text eventually appears
    await waitFor(
      () => expect(screen.getByText(/idempotency keys/i)).toBeInTheDocument(),
      { timeout: 3000 },
    );
  });
});
