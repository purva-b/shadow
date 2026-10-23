import { useEffect, useState } from "react";
import type { ActingAs } from "./types";
import { api } from "./api";
import { GhostMark } from "./ui";

/**
 * Full-page login (/login). Identity comes from Box OAuth; logout returns here.
 * "Sign in with Box" redirects to /auth/login when a Box OAuth app is
 * configured, else uses the dev login so local runs work. Once signed in, it
 * bounces to "/" where the chat popup lives.
 */
export function LoginPage() {
  const [me, setMe] = useState<ActingAs | null>(null);

  useEffect(() => {
    api.me().then((r) => {
      if (r.logged_in) { window.location.href = "/"; return; }
      setMe(r);
    }).catch(() => {});
  }, []);

  const signIn = async () => {
    if (me?.auth_mode === "oauth" && me.login_url) {
      window.location.href = me.login_url;       // → /auth/login → Box → "/"
      return;
    }
    if (me?.options?.length) {                     // dev fallback
      await api.login(me.options[0].name);
      window.location.href = "/";
    }
  };

  const oauth = me?.auth_mode === "oauth";

  return (
    <div className="lp">
      <div className="lp-card">
        <GhostMark size={76} />
        <h1 className="lp-title">👻 I'm Shadow — here to un-haunt you.</h1>
        <p className="lp-sub">
          Sign in to ask across your team's memory — meetings, chats, issues and docs.
          You'll only ever see what you're allowed to.
        </p>
        <button className="lp-btn" onClick={signIn}>
          <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <rect x="3" y="7" width="18" height="12" rx="2" /><path d="M3 10h18" />
          </svg>
          {oauth ? "Sign in with Box" : "Sign in (dev)"}
        </button>
        <div className="lp-foot">🔒 Your identity comes from Box — Shadow never sees your password.</div>
      </div>
    </div>
  );
}
