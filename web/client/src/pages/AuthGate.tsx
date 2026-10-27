/**
 * Auth gate. Shown before the app loads when the user isn't authenticated.
 *
 * Two modes:
 *   - "setup": no passcode exists yet (first run). User picks one.
 *   - "login": a passcode is set. User enters it.
 *
 * On success the server sets the httpOnly session cookie and we invalidate
 * the auth-status query, which lets the parent re-render the real app.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  authLogin,
  authSetup,
  fetchAuthStatus,
  type AuthStatus,
} from "@/lib/api";

const AUTH_QUERY_KEY = ["/api/auth/status"] as const;

interface PasscodeFormProps {
  mode: "setup" | "login";
  locked: boolean;
  lockedUntil: string | null;
  onSuccess: () => void;
}

function PasscodeForm({ mode, locked, lockedUntil, onSuccess }: PasscodeFormProps) {
  const [code, setCode] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    codeRef.current?.focus();
  }, []);

  const lockedFor = (() => {
    if (!locked || !lockedUntil) return null;
    const ms = new Date(lockedUntil).getTime() - Date.now();
    if (ms <= 0) return null;
    const mins = Math.ceil(ms / 60000);
    return `${mins} minute${mins === 1 ? "" : "s"}`;
  })();

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!/^\d+$/.test(code)) {
      setError("digits only.");
      return;
    }
    if (code.length < 4) {
      setError("at least 4 digits.");
      return;
    }
    if (mode === "setup" && code !== confirm) {
      setError("the two entries don't match.");
      return;
    }

    setSubmitting(true);
    try {
      if (mode === "setup") {
        await authSetup(code);
      } else {
        await authLogin(code);
      }
      onSuccess();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "something went wrong.";
      setError(msg);
      setSubmitting(false);
    }
  }

  const title = mode === "setup" ? "pick a passcode." : "passcode.";
  const sub =
    mode === "setup"
      ? "six digits is the recommended shape. write it down somewhere you'd actually find it."
      : "the one you set when you first opened this.";

  return (
    <div className="min-h-dvh w-full flex items-center justify-center bg-background text-foreground">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm px-6 py-10 flex flex-col gap-6"
        data-testid={`form-auth-${mode}`}
      >
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-medium tracking-tight">daycatcher.</h1>
          <p className="text-lg">{title}</p>
          <p className="text-sm text-muted-foreground">{sub}</p>
        </div>

        <div className="flex flex-col gap-3">
          <input
            ref={codeRef}
            type="password"
            inputMode="numeric"
            autoComplete={mode === "setup" ? "new-password" : "current-password"}
            pattern="[0-9]*"
            maxLength={12}
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            disabled={submitting || locked}
            placeholder="passcode"
            className="bg-card border border-border rounded-md px-4 py-3 text-2xl tracking-[0.4em] text-center font-mono focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
            data-testid="input-passcode"
          />
          {mode === "setup" && (
            <input
              type="password"
              inputMode="numeric"
              autoComplete="new-password"
              pattern="[0-9]*"
              maxLength={12}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value.replace(/\D/g, ""))}
              disabled={submitting || locked}
              placeholder="confirm"
              className="bg-card border border-border rounded-md px-4 py-3 text-2xl tracking-[0.4em] text-center font-mono focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              data-testid="input-passcode-confirm"
            />
          )}
        </div>

        {error && (
          <p
            className="text-sm text-destructive"
            data-testid="text-auth-error"
          >
            {error}
          </p>
        )}

        {locked && lockedFor && (
          <p
            className="text-sm text-destructive"
            data-testid="text-auth-locked"
          >
            too many wrong tries. try again in {lockedFor}.
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || locked || code.length < 4}
          className="bg-primary text-primary-foreground rounded-md px-4 py-3 text-base font-medium hover-elevate active-elevate-2 disabled:opacity-50"
          data-testid="button-auth-submit"
        >
          {submitting
            ? "..."
            : mode === "setup"
              ? "set passcode"
              : "unlock"}
        </button>

        {mode === "login" && (
          <p className="text-xs text-muted-foreground text-center">
            forgot it? on the server, run:
            <br />
            <code className="text-[11px]">
              python -m meat_suit.reset_passcode
            </code>
          </p>
        )}
      </form>
    </div>
  );
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery<AuthStatus>({
    queryKey: AUTH_QUERY_KEY,
    queryFn: fetchAuthStatus,
    staleTime: 0,
    retry: false,
  });

  if (isLoading) {
    return (
      <div className="min-h-dvh w-full flex items-center justify-center bg-background text-muted-foreground">
        <span className="text-sm">...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="min-h-dvh w-full flex items-center justify-center bg-background text-foreground p-6">
        <div className="max-w-sm flex flex-col gap-3 text-center">
          <h1 className="text-xl font-medium">can't reach the server.</h1>
          <p className="text-sm text-muted-foreground">
            check the server is running, then refresh.
          </p>
        </div>
      </div>
    );
  }

  if (data.authenticated) {
    return <>{children}</>;
  }

  const onSuccess = () => {
    queryClient.invalidateQueries({ queryKey: AUTH_QUERY_KEY });
  };

  return (
    <PasscodeForm
      mode={data.has_passcode ? "login" : "setup"}
      locked={data.locked}
      lockedUntil={data.locked_until}
      onSuccess={onSuccess}
    />
  );
}
