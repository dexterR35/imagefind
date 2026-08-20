import { useState } from "react";
import type { FormEvent } from "react";

interface LoginScreenProps {
  configured: boolean;
  onLogin: (password: string) => Promise<void>;
}

export function LoginScreen({ configured, onLogin }: LoginScreenProps) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await onLogin(password);
      setPassword("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-mark" aria-hidden="true">IF</div>
        <h1 id="auth-title">ImageFind</h1>
        <p className="auth-subtitle">Private image library</p>
        {configured ? (
          <form onSubmit={handleSubmit}>
            <label htmlFor="shared-password">Shared password</label>
            <input
              id="shared-password"
              name="password"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              maxLength={1024}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button type="submit" disabled={submitting || !password}>
              {submitting ? "Signing in…" : "Sign in"}
            </button>
          </form>
        ) : (
          <div className="auth-setup" role="status">
            <p>Authentication has not been configured.</p>
            <p>On the host PC, stop the app and run:</p>
            <code>npm run auth:set-password</code>
          </div>
        )}
      </section>
    </main>
  );
}
