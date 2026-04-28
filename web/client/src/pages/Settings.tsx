import { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  authLogout,
  fetchPersonas,
  fetchSettings,
  patchSettings,
  snoozeSleep,
  PERSONA_PRESENTATION,
  PERSONA_ORDER,
  PersonaKey,
  Settings as SettingsT,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type RestartFlag = "sleep_weeknight_hour" | "sleep_weekend_hour";

export default function Settings() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["/api/settings"],
    queryFn: fetchSettings,
  });

  const { data: personasResp } = useQuery({
    queryKey: ["/api/personas"],
    queryFn: fetchPersonas,
    staleTime: 60_000,
  });

  const displays = useMemo<Partial<Record<PersonaKey, string>>>(() => {
    if (!personasResp?.personas) return {};
    return Object.fromEntries(
      personasResp.personas.map((p) => [p.key, p.display]),
    ) as Partial<Record<PersonaKey, string>>;
  }, [personasResp]);

  const [weeknight, setWeeknight] = useState<number | "">("");
  const [weekend, setWeekend] = useState<number | "">("");
  const [restartFlags, setRestartFlags] = useState<RestartFlag[]>([]);
  const [snoozedAt, setSnoozedAt] = useState<string | null>(null);

  // Hydrate local form state from server payload once it lands.
  useEffect(() => {
    if (!data) return;
    setWeeknight(data.rhythm.sleep_weeknight_hour);
    setWeekend(data.rhythm.sleep_weekend_hour);
  }, [data?.rhythm.sleep_weeknight_hour, data?.rhythm.sleep_weekend_hour]);

  const patch = useMutation({
    mutationFn: patchSettings,
    onSuccess: (resp) => {
      qc.setQueryData(["/api/settings"], stripRestart(resp));
      qc.invalidateQueries({ queryKey: ["/api/state"] });
      if (resp.restart_required && resp.restart_required.length > 0) {
        setRestartFlags((prev) => mergeFlags(prev, resp.restart_required as RestartFlag[]));
      }
    },
  });

  const snooze = useMutation({
    mutationFn: snoozeSleep,
    onSuccess: () => {
      const now = new Date();
      setSnoozedAt(now.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }));
    },
  });

  const weeknightDirty = data ? weeknight !== "" && weeknight !== data.rhythm.sleep_weeknight_hour : false;
  const weekendDirty = data ? weekend !== "" && weekend !== data.rhythm.sleep_weekend_hour : false;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6">
        <h1 className="font-mono text-sm uppercase tracking-widest text-muted-foreground">
          settings
        </h1>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto flex max-w-2xl flex-col gap-10">
          {isLoading && (
            <p className="font-mono text-sm text-muted-foreground">loading…</p>
          )}

          {data && (
            <>
              <VibeSection
                current={data.vibe}
                displays={displays}
                onPick={(vibe) => patch.mutate({ vibe })}
                pending={patch.isPending && patch.variables?.vibe !== undefined}
              />

              <SleepSection
                weeknight={weeknight}
                weekend={weekend}
                onWeeknight={setWeeknight}
                onWeekend={setWeekend}
                onSave={() => {
                  const body: { sleep_weeknight_hour?: number; sleep_weekend_hour?: number } = {};
                  if (weeknightDirty && weeknight !== "") body.sleep_weeknight_hour = weeknight;
                  if (weekendDirty && weekend !== "") body.sleep_weekend_hour = weekend;
                  if (Object.keys(body).length > 0) patch.mutate(body);
                }}
                disabled={!weeknightDirty && !weekendDirty}
                pending={patch.isPending && (patch.variables?.sleep_weeknight_hour !== undefined || patch.variables?.sleep_weekend_hour !== undefined)}
                weeknightFlagged={restartFlags.includes("sleep_weeknight_hour")}
                weekendFlagged={restartFlags.includes("sleep_weekend_hour")}
              />

              <NudgesSection
                settings={data}
                onToggleNudges={(v) => patch.mutate({ nudges_enabled: v })}
                onToggleSleep={(v) => patch.mutate({ sleep_nudges_enabled: v })}
                onSnoozeTonight={() => snooze.mutate()}
                snoozedAt={snoozedAt}
                snoozePending={snooze.isPending}
              />

              <DataSection timezone={data.rhythm.timezone} />
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------- Vibe ----------

function VibeSection({
  current,
  displays,
  onPick,
  pending,
}: {
  current: PersonaKey;
  displays: Partial<Record<PersonaKey, string>>;
  onPick: (k: PersonaKey) => void;
  pending: boolean;
}) {
  return (
    <Section title="default vibe" hint="who shows up first when you open the app.">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {PERSONA_ORDER.map((key) => {
          const p = PERSONA_PRESENTATION[key];
          const active = key === current;
          return (
            <button
              key={key}
              type="button"
              data-testid={`button-vibe-${key}`}
              onClick={() => !active && onPick(key)}
              disabled={active || pending}
              className={cn(
                "group flex items-start gap-3 rounded-md border px-4 py-3 text-left transition",
                "elevate",
                active
                  ? "border-foreground/40 bg-foreground/5"
                  : "border-border hover:border-foreground/30",
              )}
            >
              <span
                className="mt-0.5 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm font-mono text-sm font-semibold"
                style={{
                  background: `hsl(var(${p.tint_var}) / 0.18)`,
                  color: `hsl(var(${p.tint_var}))`,
                }}
              >
                {p.letter}
              </span>
              <span className="flex flex-col gap-0.5">
                <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
                  {displays?.[key] ?? key.replace(/_/g, " ")}
                </span>
                <span className="text-sm text-foreground/85">{p.blurb}</span>
              </span>
            </button>
          );
        })}
      </div>
    </Section>
  );
}

// ---------- Sleep windows ----------

function SleepSection({
  weeknight,
  weekend,
  onWeeknight,
  onWeekend,
  onSave,
  disabled,
  pending,
  weeknightFlagged,
  weekendFlagged,
}: {
  weeknight: number | "";
  weekend: number | "";
  onWeeknight: (v: number | "") => void;
  onWeekend: (v: number | "") => void;
  onSave: () => void;
  disabled: boolean;
  pending: boolean;
  weeknightFlagged: boolean;
  weekendFlagged: boolean;
}) {
  return (
    <Section
      title="sleep windows"
      hint="hour when the sleep nudge starts firing. 0–23, your local time."
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <HourField
          label="weeknights"
          value={weeknight}
          onChange={onWeeknight}
          flagged={weeknightFlagged}
          testId="input-weeknight-hour"
        />
        <HourField
          label="weekends"
          value={weekend}
          onChange={onWeekend}
          flagged={weekendFlagged}
          testId="input-weekend-hour"
        />
      </div>
      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          data-testid="button-save-sleep"
          onClick={onSave}
          disabled={disabled || pending}
          className={cn(
            "rounded-md border px-3 py-1.5 font-mono text-xs uppercase tracking-wider transition elevate",
            disabled || pending
              ? "border-border text-muted-foreground/60"
              : "border-foreground/40 bg-foreground/5 text-foreground hover:border-foreground/60",
          )}
        >
          {pending ? "saving…" : "save sleep windows"}
        </button>
        {(weeknightFlagged || weekendFlagged) && (
          <span className="font-mono text-xs text-[hsl(var(--persona-ceo))]">
            restart required to take effect
          </span>
        )}
      </div>
    </Section>
  );
}

function HourField({
  label,
  value,
  onChange,
  flagged,
  testId,
}: {
  label: string;
  value: number | "";
  onChange: (v: number | "") => void;
  flagged: boolean;
  testId: string;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">
        {label}
        {flagged && (
          <span className="ml-2 normal-case tracking-normal text-[hsl(var(--persona-ceo))]">
            (pending restart)
          </span>
        )}
      </span>
      <input
        type="number"
        min={0}
        max={23}
        value={value}
        data-testid={testId}
        onChange={(e) => {
          const v = e.target.value;
          if (v === "") return onChange("");
          const n = Number(v);
          if (!Number.isFinite(n)) return;
          onChange(Math.max(0, Math.min(23, Math.round(n))));
        }}
        className="w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-sm text-foreground focus:border-foreground/40 focus:outline-none"
      />
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/60">
        24h · {formatHour(value)}
      </span>
    </label>
  );
}

function formatHour(v: number | ""): string {
  if (v === "") return "—";
  const h = v % 12 === 0 ? 12 : v % 12;
  const ap = v < 12 ? "am" : "pm";
  return `${h}${ap}`;
}

// ---------- Nudges & sleep toggles ----------

function NudgesSection({
  settings,
  onToggleNudges,
  onToggleSleep,
  onSnoozeTonight,
  snoozedAt,
  snoozePending,
}: {
  settings: SettingsT;
  onToggleNudges: (v: boolean) => void;
  onToggleSleep: (v: boolean) => void;
  onSnoozeTonight: () => void;
  snoozedAt: string | null;
  snoozePending: boolean;
}) {
  return (
    <Section
      title="nudges"
      hint="background pings from the brain. silence anytime, no penalty."
    >
      <div className="flex flex-col divide-y divide-border rounded-md border border-border">
        <ToggleRow
          label="all nudges"
          sub="hourly check-ins, anti-PDA jabs, weekly recap. master switch."
          checked={settings.rhythm.nudges_enabled}
          onChange={onToggleNudges}
          testId="toggle-nudges"
        />
        <ToggleRow
          label="sleep nudges"
          sub="the one that yells at you past your sleep hour."
          checked={settings.rhythm.sleep_nudges_enabled}
          onChange={onToggleSleep}
          testId="toggle-sleep"
        />
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm text-foreground/90">snooze tonight</span>
            <span className="text-xs text-muted-foreground">
              kills only the sleep nudge until tomorrow.{" "}
              {snoozedAt && (
                <span className="text-foreground/70">snoozed at {snoozedAt}.</span>
              )}
            </span>
          </div>
          <button
            type="button"
            data-testid="button-snooze-tonight"
            onClick={onSnoozeTonight}
            disabled={snoozePending}
            className={cn(
              "rounded-md border px-3 py-1.5 font-mono text-xs uppercase tracking-wider transition elevate",
              snoozePending
                ? "border-border text-muted-foreground/60"
                : "border-border text-foreground hover:border-foreground/40",
            )}
          >
            {snoozePending ? "…" : "snooze"}
          </button>
        </div>
      </div>
    </Section>
  );
}

function ToggleRow({
  label,
  sub,
  checked,
  onChange,
  testId,
}: {
  label: string;
  sub: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  testId: string;
}) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-4 px-4 py-3">
      <span className="flex flex-col gap-0.5">
        <span className="text-sm text-foreground/90">{label}</span>
        <span className="text-xs text-muted-foreground">{sub}</span>
      </span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        data-testid={testId}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-6 w-11 shrink-0 rounded-full border transition-colors",
          checked ? "border-foreground/40 bg-foreground/30" : "border-border bg-background",
        )}
      >
        <span
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-foreground transition-transform",
            checked ? "translate-x-[22px]" : "translate-x-0.5",
          )}
        />
      </button>
    </label>
  );
}

// ---------- Data export + tz ----------

function DataSection({ timezone }: { timezone: string }) {
  return (
    <Section
      title="data"
      hint="export everything the brain has on you. a single json blob."
    >
      <div className="flex flex-col gap-3 rounded-md border border-border px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm text-foreground/90">full export</span>
            <span className="text-xs text-muted-foreground">
              messages, journal, vibe history, polls, sleep, prefs.
            </span>
          </div>
          <a
            href="/api/export"
            download
            data-testid="link-export"
            className="rounded-md border border-foreground/30 bg-foreground/5 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-foreground transition elevate hover:border-foreground/60"
          >
            download
          </a>
        </div>
        <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
          <span className="text-sm text-foreground/90">timezone</span>
          <span className="font-mono text-xs text-muted-foreground" data-testid="text-timezone">
            {timezone}
          </span>
        </div>
        <div className="flex items-center justify-between gap-4 border-t border-border pt-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm text-foreground/90">lock screen</span>
            <span className="text-xs text-muted-foreground">
              clears the session cookie. you'll need the passcode to come back.
            </span>
          </div>
          <button
            type="button"
            data-testid="button-lock"
            onClick={async () => {
              await authLogout();
              window.location.reload();
            }}
            className="rounded-md border border-foreground/30 bg-foreground/5 px-3 py-1.5 font-mono text-xs uppercase tracking-wider text-foreground transition elevate hover:border-foreground/60"
          >
            lock
          </button>
        </div>
      </div>
    </Section>
  );
}

// ---------- shared bits ----------

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-col gap-0.5">
        <h2 className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          {title}
        </h2>
        <p className="text-sm text-foreground/70">{hint}</p>
      </header>
      <div>{children}</div>
    </section>
  );
}

function stripRestart(s: SettingsT & { restart_required?: string[] }): SettingsT {
  const { restart_required: _r, ...rest } = s;
  void _r;
  return rest;
}

function mergeFlags(prev: RestartFlag[], next: RestartFlag[]): RestartFlag[] {
  const set = new Set<RestartFlag>(prev);
  for (const f of next) set.add(f);
  return Array.from(set);
}
