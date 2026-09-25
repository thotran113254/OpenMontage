import React, { useState } from "react";
import { api } from "../api/client";

/** Full-screen gate shown when the server requires a session and the browser
 * doesn't have one yet. Login sets an HttpOnly cookie server-side — the token
 * itself is never stored or held in the client after this form submits. */
export const LoginScreen: React.FC<{ onLoggedIn: () => void }> = ({ onLoggedIn }) => {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const value = token.trim();
    if (!value || busy) return;
    setBusy(true);
    setError("");
    try {
      await api.authLogin(value);
      onLoggedIn();
    } catch {
      setError("Sai mã truy cập.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="card login-card" onSubmit={(event) => void submit(event)}>
        <h1>OpenMontage</h1>
        <label className="field">
          Nhập mã truy cập
          <input
            type="password"
            autoFocus
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="Mã truy cập"
          />
        </label>
        {error && <p className="error-text small">{error}</p>}
        <button className="primary" type="submit" disabled={busy || !token.trim()}>
          {busy ? "Đang đăng nhập…" : "Đăng nhập"}
        </button>
      </form>
    </div>
  );
};
