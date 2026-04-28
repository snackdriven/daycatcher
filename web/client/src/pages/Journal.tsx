import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJournal, JournalEntry, markJournalSeen } from "@/lib/api";
import { cn } from "@/lib/utils";
import { format, parseISO } from "date-fns";

type Filter = "all" | "daily" | "weekly";

export default function Journal() {
  const [filter, setFilter] = useState<Filter>("all");
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["/api/journal", filter],
    queryFn: () => fetchJournal({ kind: filter, limit: 60 }),
  });

  const see = useMutation({
    mutationFn: ({ date, kind }: { date: string; kind: "daily" | "weekly" }) =>
      markJournalSeen(date, kind),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["/api/journal"] });
      qc.invalidateQueries({ queryKey: ["/api/state"] });
    },
  });

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6">
        <h1 className="font-mono text-sm uppercase tracking-widest text-muted-foreground">
          journal
        </h1>
        <FilterPills filter={filter} onChange={setFilter} />
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-6 sm:px-8">
        <div className="mx-auto max-w-2xl">
          {isLoading && (
            <p className="font-mono text-sm text-muted-foreground">loading…</p>
          )}

          {!isLoading && (data?.entries.length ?? 0) === 0 && (
            <EmptyState filter={filter} />
          )}

          <div className="flex flex-col gap-10">
            {data?.entries.map((e) => (
              <Entry
                key={`${e.kind}:${e.local_date}`}
                entry={e}
                onSeen={() =>
                  !e.seen && see.mutate({ date: e.local_date, kind: e.kind })
                }
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function FilterPills({
  filter,
  onChange,
}: {
  filter: Filter;
  onChange: (f: Filter) => void;
}) {
  const opts: { value: Filter; label: string }[] = [
    { value: "all", label: "all" },
    { value: "daily", label: "daily" },
    { value: "weekly", label: "weekly" },
  ];
  return (
    <div role="tablist" className="inline-flex gap-1 rounded-md border border-border p-0.5 bg-secondary/40">
      {opts.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={filter === o.value}
          onClick={() => onChange(o.value)}
          data-testid={`tab-${o.value}`}
          className={cn(
            "rounded-sm px-2.5 py-1 font-mono text-xs uppercase tracking-wide",
            "transition-colors hover-elevate active-elevate-2",
            filter === o.value
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

function Entry({ entry, onSeen }: { entry: JournalEntry; onSeen: () => void }) {
  const date = (() => {
    try {
      return format(parseISO(entry.local_date), "EEE, MMM d");
    } catch {
      return entry.local_date;
    }
  })();

  return (
    <article
      onMouseEnter={onSeen}
      className="relative"
      data-testid={`journal-entry-${entry.kind}-${entry.local_date}`}
    >
      <header className="mb-2 flex items-baseline justify-between gap-3">
        <h2 className="font-mono text-xs uppercase tracking-widest text-muted-foreground">
          {entry.kind === "weekly" ? `week ending ${date}` : date}
        </h2>
        {!entry.seen && (
          <span
            className="size-1.5 rounded-full bg-accent"
            aria-label="unread"
            data-testid="dot-unread"
          />
        )}
      </header>
      <p
        className={cn(
          "font-journal text-[17px] leading-[1.7]",
          entry.kind === "weekly" && "italic text-foreground/90",
        )}
      >
        {entry.narrative}
      </p>
    </article>
  );
}

function EmptyState({ filter }: { filter: Filter }) {
  const message =
    filter === "weekly"
      ? "no weekly reflections yet. one shows up every sunday night."
      : filter === "daily"
      ? "no daily entries yet. the scribe writes one each evening."
      : "nothing in the journal yet. it fills as the days pass.";
  return (
    <p className="font-mono text-sm leading-relaxed text-muted-foreground">
      {message}
    </p>
  );
}
