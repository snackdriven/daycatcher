import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { Send, RefreshCw } from "lucide-react";
import type { Message, PersonaKey } from "@shared/schema";

interface PersonaInfo {
  key: PersonaKey;
  display: string;
  blurb: string;
  sample: string;
}

const VIBE_VARS: Record<PersonaKey, string> = {
  smug_construct: "--vibe-construct",
  feral_bff: "--vibe-feral",
  stoic_mentor: "--vibe-stoic",
  quiet_guardian: "--vibe-quiet",
};

const SHORT_NAMES: Record<PersonaKey, string> = {
  smug_construct: "the construct",
  feral_bff: "feral bff",
  stoic_mentor: "the mentor",
  quiet_guardian: "quiet one",
};

// Generate a session id without needing crypto.randomUUID (broader support).
function newSessionId() {
  return (
    "s_" +
    Date.now().toString(36) +
    "_" +
    Math.random().toString(36).slice(2, 10)
  );
}

export default function Demo() {
  const qc = useQueryClient();
  const [persona, setPersona] = useState<PersonaKey>("smug_construct");
  // Keep one session per persona — switching keeps each conversation alive.
  const [sessions, setSessions] = useState<Record<PersonaKey, string>>(() => ({
    smug_construct: newSessionId(),
    feral_bff: newSessionId(),
    stoic_mentor: newSessionId(),
    quiet_guardian: newSessionId(),
  }));
  const sessionId = sessions[persona];
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const personasQuery = useQuery<PersonaInfo[]>({
    queryKey: ["/api/personas"],
  });

  const messagesQuery = useQuery<Message[]>({
    queryKey: ["/api/sessions", sessionId, "messages"],
    queryFn: async () => {
      const res = await apiRequest("GET", `/api/sessions/${sessionId}/messages`);
      return res.json();
    },
  });

  const sendMutation = useMutation({
    mutationFn: async (message: string) => {
      const res = await apiRequest("POST", "/api/chat", {
        sessionId,
        persona,
        message,
      });
      return res.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["/api/sessions", sessionId, "messages"] });
    },
  });

  const resetMutation = useMutation({
    mutationFn: async () => {
      await apiRequest("DELETE", `/api/sessions/${sessionId}`);
    },
    onSuccess: () => {
      // assign a new session id so cache key changes
      const next = newSessionId();
      setSessions((s) => ({ ...s, [persona]: next }));
      qc.invalidateQueries({ queryKey: ["/api/sessions"] });
    },
  });

  const messages = messagesQuery.data ?? [];
  const isThinking = sendMutation.isPending;

  // Optimistic user message while waiting
  const displayed = useMemo(() => {
    if (!sendMutation.isPending) return messages;
    const optimistic: Message = {
      id: -1,
      sessionId,
      role: "user",
      content: sendMutation.variables ?? "",
      createdAt: Date.now(),
    };
    return [...messages, optimistic];
  }, [messages, sendMutation.isPending, sendMutation.variables, sessionId]);

  // auto-scroll
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [displayed.length, isThinking]);

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text || isThinking) return;
    sendMutation.mutate(text);
    setDraft("");
  }

  const personas = personasQuery.data ?? [];
  const currentInfo = personas.find((p) => p.key === persona);

  return (
    <div className="wrap py-10 sm:py-14">
      <div className="max-w-2xl">
        <span className="hand text-xl text-muted-foreground">demo</span>
        <h1 className="font-serif text-4xl sm:text-5xl mt-1">
          pick a vibe. say something.
        </h1>
        <p className="mt-4 text-foreground/80 leading-relaxed">
          this is just mind 1, the persona. the scribe and the weaver live in
          the python version. switching vibes keeps each conversation alive.
          your messages are stored on the server side per persona, so a
          refresh won't lose them.
        </p>
      </div>

      {/* Persona switcher — chip row, hand-feel */}
      <div className="mt-8 flex flex-wrap gap-2.5" role="tablist" aria-label="Pick a vibe">
        {(personas.length ? personas : []).map((p) => {
          const active = p.key === persona;
          return (
            <button
              key={p.key}
              role="tab"
              aria-selected={active}
              onClick={() => setPersona(p.key as PersonaKey)}
              className={`group inline-flex items-center gap-2 px-4 py-2 rounded-full text-sm border transition-all hover-elevate active-elevate ${
                active
                  ? "bg-foreground text-background border-foreground"
                  : "bg-card text-foreground border-border"
              }`}
              data-testid={`button-vibe-${p.key}`}
            >
              <span
                className="inline-block size-2 rounded-full"
                style={{
                  background: `hsl(var(${VIBE_VARS[p.key as PersonaKey]}))`,
                  boxShadow: active
                    ? "none"
                    : `0 0 0 3px hsl(var(${VIBE_VARS[p.key as PersonaKey]}) / 0.18)`,
                }}
                aria-hidden="true"
              />
              <span>{SHORT_NAMES[p.key as PersonaKey]}</span>
            </button>
          );
        })}
      </div>

      {/* Chat surface */}
      <div className="mt-6 grid lg:grid-cols-[1fr_320px] gap-6 items-start">
        <div
          className="note-warm p-0 overflow-hidden flex flex-col h-[min(70vh,640px)]"
          data-testid="surface-chat"
        >
          {/* Chat header */}
          <div
            className="px-5 py-3 border-b border-border/60 flex items-center gap-3"
            style={{
              background: `linear-gradient(180deg, hsl(var(${VIBE_VARS[persona]}) / 0.10), transparent)`,
            }}
          >
            <span
              className="size-2.5 rounded-full"
              style={{
                background: `hsl(var(${VIBE_VARS[persona]}))`,
                boxShadow: `0 0 0 4px hsl(var(${VIBE_VARS[persona]}) / 0.18)`,
              }}
              aria-hidden="true"
            />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate" data-testid="text-persona-name">
                {currentInfo?.display ?? "..."}
              </p>
              <p className="text-xs text-muted-foreground truncate hand text-base">
                {currentInfo?.blurb ?? ""}
              </p>
            </div>
            <button
              onClick={() => resetMutation.mutate()}
              disabled={resetMutation.isPending}
              className="text-xs text-muted-foreground hover-elevate active-elevate px-2.5 py-1.5 rounded-md inline-flex items-center gap-1.5"
              data-testid="button-reset"
              title="Start fresh with this vibe"
            >
              <RefreshCw className="size-3.5" />
              fresh
            </button>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 sm:px-6 py-5 space-y-3">
            {messagesQuery.isLoading ? (
              <SkeletonMessages />
            ) : displayed.length === 0 ? (
              <EmptyState personaKey={persona} sample={currentInfo?.sample} />
            ) : (
              displayed.map((m) => (
                <div
                  key={m.id}
                  className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
                >
                  <div
                    className={`bubble ${m.role === "user" ? "bubble-user" : "bubble-bot"}`}
                    data-testid={`message-${m.role}-${m.id}`}
                  >
                    {m.content}
                  </div>
                </div>
              ))
            )}
            {isThinking && (
              <div className="flex justify-start">
                <div className="bubble bubble-bot">
                  <span className="typing">
                    <span /><span /><span />
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Composer */}
          <form
            onSubmit={submit}
            className="border-t border-border/60 p-3 bg-background/60 backdrop-blur-sm"
          >
            <div className="flex items-end gap-2">
              <textarea
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    submit(e as any);
                  }
                }}
                placeholder="say something. fragments are fine."
                rows={1}
                className="flex-1 resize-none bg-card border border-border rounded-2xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring/40 max-h-32"
                data-testid="input-message"
              />
              <button
                type="submit"
                disabled={!draft.trim() || isThinking}
                className="bg-primary text-primary-foreground rounded-full size-10 flex items-center justify-center disabled:opacity-40 hover-elevate active-elevate"
                aria-label="Send"
                data-testid="button-send"
              >
                <Send className="size-4" />
              </button>
            </div>
          </form>
        </div>

        {/* Sidebar — context */}
        <aside className="space-y-4">
          <div className="note p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">about this vibe</p>
            <p className="mt-3 text-sm leading-relaxed">{currentInfo?.blurb}</p>
            <p
              className="mt-4 text-sm italic leading-relaxed border-l-2 pl-3"
              style={{ borderColor: `hsl(var(${VIBE_VARS[persona]}) / 0.5)`, fontFamily: "var(--font-serif)" }}
            >
              {currentInfo?.sample}
            </p>
          </div>
          <div className="note p-5">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">house rules</p>
            <ul className="mt-3 text-sm space-y-2 text-foreground/80">
              <li>no commands. no shoulds.</li>
              <li>match your energy, don't soothe it.</li>
              <li>short replies by default.</li>
              <li>fragments and lowercase are on purpose.</li>
            </ul>
          </div>
          <p className="text-xs text-muted-foreground px-1 leading-relaxed">
            messages are saved server-side per session so a refresh keeps your
            chat. tap "fresh" to wipe just this vibe.
          </p>
        </aside>
      </div>
    </div>
  );
}

function SkeletonMessages() {
  return (
    <div className="space-y-3 animate-pulse">
      <div className="flex justify-start"><div className="bubble bubble-bot w-2/3 h-10" /></div>
      <div className="flex justify-end"><div className="bubble bubble-user w-1/2 h-8" /></div>
      <div className="flex justify-start"><div className="bubble bubble-bot w-3/5 h-12" /></div>
    </div>
  );
}

function EmptyState({ personaKey, sample }: { personaKey: PersonaKey; sample?: string }) {
  return (
    <div className="h-full min-h-[280px] flex flex-col items-center justify-center text-center px-6">
      <span
        className="size-12 rounded-full mb-4"
        style={{
          background: `hsl(var(${VIBE_VARS[personaKey]}) / 0.18)`,
          boxShadow: `0 0 0 6px hsl(var(${VIBE_VARS[personaKey]}) / 0.08)`,
        }}
        aria-hidden="true"
      />
      <p className="hand text-2xl text-foreground">they're here.</p>
      <p className="text-sm text-muted-foreground mt-2 max-w-xs">
        say anything. a fragment is fine. a vent is fine. there's no wrong
        opener.
      </p>
      {sample && (
        <p
          className="mt-5 text-sm italic text-muted-foreground max-w-sm"
          style={{ fontFamily: "var(--font-serif)" }}
        >
          they sound like: <span className="text-foreground/85">"{sample}"</span>
        </p>
      )}
    </div>
  );
}
