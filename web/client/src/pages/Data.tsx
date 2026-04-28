import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchDataSummary,
  DataRange,
  DataSummary,
  PERSONA_PRESENTATION,
  PersonaKey,
  SleepStatus,
} from "@/lib/api";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Cell,
  Tooltip as RTooltip,
} from "recharts";
import { cn } from "@/lib/utils";

const RANGES: { value: DataRange; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "all", label: "all" },
];

export default function Data() {
  const [range, setRange] = useState<DataRange>("30d");
  const { data, isLoading } = useQuery({
    queryKey: ["/api/data/summary", range],
    queryFn: () => fetchDataSummary(range),
  });

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6">
        <h1 className="font-mono text-sm uppercase tracking-widest text-muted-foreground">
          data
        </h1>
        <RangePills range={range} onChange={setRange} />
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto flex max-w-4xl flex-col gap-8 font-mono">
          {isLoading && (
            <p className="text-sm text-muted-foreground">loading…</p>
          )}
          {data && <Body summary={data} />}
        </div>
      </div>
    </div>
  );
}

function RangePills({
  range,
  onChange,
}: {
  range: DataRange;
  onChange: (r: DataRange) => void;
}) {
  return (
    <div role="tablist" className="inline-flex gap-1 rounded-md border border-border p-0.5 bg-secondary/40">
      {RANGES.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={range === o.value}
          onClick={() => onChange(o.value)}
          data-testid={`tab-range-${o.value}`}
          className={cn(
            "rounded-sm px-2.5 py-1 font-mono text-xs uppercase tracking-wide",
            "transition-colors hover-elevate active-elevate-2",
            range === o.value
              ? "bg-background text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Body({ summary }: { summary: DataSummary }) {
  return (
    <>
      <KpiRow summary={summary} />
      <Section title="hourly density">
        <HourlyDensity rows={summary.hourly_density} />
      </Section>
      <Section title="vibe distribution">
        <VibeDistribution summary={summary} />
      </Section>
      <Section title="sleep adherence">
        <SleepAdherence rows={summary.sleep_adherence} />
      </Section>
      <Section title="this week vs last">
        <WeeklyCompare summary={summary} />
      </Section>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h2 className="mb-3 text-[10px] uppercase tracking-widest text-muted-foreground">
        {title}
      </h2>
      <div className="rounded-md border border-border bg-card p-4 sm:p-6">
        {children}
      </div>
    </section>
  );
}

// ---------- KPI row ----------

function KpiRow({ summary }: { summary: DataSummary }) {
  const { kpis } = summary;
  const lateNightPct = Math.round((kpis.after_11pm_pct ?? 0) * 100);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Kpi label="messages" value={kpis.messages_total} sparkline={kpis.messages_sparkline} />
      <Kpi label="streak" value={`${kpis.streak_days}d`} hint="days in a row" />
      <Kpi label="late nights" value={`${lateNightPct}%`} hint="last msg ≥ 11pm" />
      <Kpi label="range" value={summary.range} hint="window" />
    </div>
  );
}

function Kpi({
  label,
  value,
  hint,
  sparkline,
}: {
  label: string;
  value: string | number;
  hint?: string;
  sparkline?: number[];
}) {
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="text-[10px] uppercase tracking-widest text-muted-foreground">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {hint && (
        <div className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground/80">
          {hint}
        </div>
      )}
      {sparkline && sparkline.length > 0 && (
        <Sparkline values={sparkline} />
      )}
    </div>
  );
}

function Sparkline({ values }: { values: number[] }) {
  const max = Math.max(1, ...values);
  return (
    <div className="mt-2 flex h-6 items-end gap-[2px]">
      {values.map((v, i) => (
        <div
          key={i}
          className="flex-1 rounded-sm bg-accent/60"
          style={{ height: `${(v / max) * 100 || 4}%`, minHeight: 2 }}
        />
      ))}
    </div>
  );
}

// ---------- Hourly density ----------

function HourlyDensity({ rows }: { rows: { hour: number; count: number }[] }) {
  // Fill missing hours so the x-axis stays 0..23
  const data = useMemo(() => {
    const m = new Map(rows.map((r) => [r.hour, r.count]));
    return Array.from({ length: 24 }, (_, h) => ({
      hour: h,
      count: m.get(h) ?? 0,
    }));
  }, [rows]);

  if (data.every((d) => d.count === 0)) {
    return <p className="text-sm text-muted-foreground">no data yet.</p>;
  }

  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          <XAxis
            dataKey="hour"
            tickLine={false}
            axisLine={false}
            tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
            interval={3}
            tickFormatter={(h) => `${h}`}
          />
          <YAxis hide />
          <RTooltip
            cursor={{ fill: "hsl(var(--muted))" }}
            contentStyle={{
              background: "hsl(var(--popover))",
              border: "1px solid hsl(var(--border))",
              borderRadius: 6,
              fontSize: 12,
              fontFamily: "var(--font-mono)",
            }}
            formatter={(v: number) => [`${v} msg`, "count"]}
            labelFormatter={(h) => `${h}:00`}
          />
          <Bar dataKey="count" fill="hsl(var(--accent))" radius={[2, 2, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

// ---------- Vibe distribution ----------

function VibeDistribution({ summary }: { summary: DataSummary }) {
  const { vibe_distribution } = summary;
  if (!vibe_distribution.length) {
    return <p className="text-sm text-muted-foreground">no data yet.</p>;
  }
  const total = vibe_distribution.reduce((s, v) => s + v.count, 0) || 1;

  return (
    <div className="flex flex-col gap-3">
      {/* Stacked horizontal bar */}
      <div className="flex h-3 w-full overflow-hidden rounded-sm">
        {vibe_distribution.map((v) => {
          const tint = `hsl(var(${PERSONA_PRESENTATION[v.key as PersonaKey]?.tint_var ?? "--accent"}))`;
          const width = (v.count / total) * 100;
          return (
            <div
              key={v.key}
              style={{ width: `${width}%`, backgroundColor: tint }}
              title={`${v.display}: ${v.count}`}
            />
          );
        })}
      </div>

      {/* Legend table */}
      <div className="grid grid-cols-1 gap-1 text-xs sm:grid-cols-2">
        {vibe_distribution.map((v) => {
          const meta = PERSONA_PRESENTATION[v.key as PersonaKey];
          const tint = `hsl(var(${meta?.tint_var ?? "--accent"}))`;
          const pct = ((v.count / total) * 100).toFixed(0);
          return (
            <div
              key={v.key}
              className="flex items-center gap-2 tabular-nums"
              data-testid={`vibe-row-${v.key}`}
            >
              <span
                className="inline-block size-2.5 rounded-full"
                style={{ backgroundColor: tint }}
              />
              <span className="font-mono text-foreground">{meta?.letter ?? "·"}</span>
              <span className="text-muted-foreground">{v.display}</span>
              <span className="ml-auto text-muted-foreground/80">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------- Sleep adherence ----------

const SLEEP_COLOR: Record<SleepStatus, string> = {
  within: "hsl(var(--persona-innkeeper))",     // slow green
  late: "hsl(40 60% 70% / 0.6)",                // gold dim
  very_late: "hsl(var(--persona-ceo))",         // burnt orange
  unknown: "hsl(var(--muted))",
};

function SleepAdherence({
  rows,
}: {
  rows: { date: string; last_msg_local: string; status: SleepStatus }[];
}) {
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">no data yet.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1">
        {rows.map((r) => (
          <div
            key={r.date}
            className="size-3 rounded-sm"
            style={{ backgroundColor: SLEEP_COLOR[r.status] }}
            title={`${r.date} · last msg ${r.last_msg_local} · ${r.status.replace("_", " ")}`}
            data-testid={`sleep-cell-${r.date}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        <Legend color={SLEEP_COLOR.within} label="within" />
        <Legend color={SLEEP_COLOR.late} label="late" />
        <Legend color={SLEEP_COLOR.very_late} label="very late" />
      </div>
    </div>
  );
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="size-2 rounded-sm" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}

// ---------- Weekly compare ----------

function WeeklyCompare({ summary }: { summary: DataSummary }) {
  const { this_week, last_week } = summary.weekly_compare;

  const rows: { label: string; this_v: string | number; last_v: string | number }[] = [
    { label: "messages", this_v: this_week.messages, last_v: last_week.messages },
    { label: "late nights", this_v: this_week.late_nights, last_v: last_week.late_nights },
    {
      label: "top vibe",
      this_v: this_week.top_vibe ? PERSONA_PRESENTATION[this_week.top_vibe].letter : "—",
      last_v: last_week.top_vibe ? PERSONA_PRESENTATION[last_week.top_vibe].letter : "—",
    },
  ];

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-widest text-muted-foreground">
          <th className="pb-2 font-normal">metric</th>
          <th className="pb-2 font-normal text-right">this</th>
          <th className="pb-2 font-normal text-right">prev</th>
          <th className="pb-2 font-normal text-right">Δ</th>
        </tr>
      </thead>
      <tbody className="font-mono tabular-nums">
        {rows.map((r) => {
          const tThis = typeof r.this_v === "number" ? r.this_v : null;
          const tLast = typeof r.last_v === "number" ? r.last_v : null;
          const delta = tThis !== null && tLast !== null ? tThis - tLast : null;
          return (
            <tr key={r.label} className="border-t border-border/50">
              <td className="py-2 text-muted-foreground">{r.label}</td>
              <td className="py-2 text-right">{r.this_v}</td>
              <td className="py-2 text-right text-muted-foreground">{r.last_v}</td>
              <td
                className={cn(
                  "py-2 text-right",
                  delta === null
                    ? "text-muted-foreground"
                    : delta > 0
                    ? "text-foreground"
                    : delta < 0
                    ? "text-muted-foreground/80"
                    : "text-muted-foreground",
                )}
              >
                {delta === null ? "—" : delta > 0 ? `+${delta}` : `${delta}`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
