import { ChatWidget } from "./ChatWidget";
import { LoginPage } from "./LoginPage";
import { ConnectionsPage } from "./ConnectionsPage";

/**
 * Shadow is a floating chat popup plus two full web pages:
 *   /login        — sign in with Box (OAuth)
 *   /connections  — plug sources (Slack, Confluence, GitHub, Calendar) into Shadow
 *   /             — the empty canvas carrying the floating chat popup
 * Simple path-based routing keeps the bundle dependency-free.
 */
export default function App() {
  const path = window.location.pathname.replace(/\/+$/, "");
  if (path === "/login") return <LoginPage />;
  if (path === "/connections") return <ConnectionsPage />;
  return (
    <main className="sw-canvas">
      <ChatWidget />
    </main>
  );
}
