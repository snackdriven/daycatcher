import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChatMessage,
  PERSONA_PRESENTATION,
  PersonaKey,
  fetchMessages,
  fetchState,
  markPersonasSeen,
  postMessage,
  setVibe,
} from "@/lib/api";
import { PersonaSwitcher } from "@/components/PersonaSwitcher";
import { FirstRunSheet } from "@/components/FirstRunSheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Send } from "lucide-react";
import { format, parseISO } from "date-fns";

export default function Chat() {
  const qc = useQueryClient();

  // App state — vibe, personas list, first-run flag, last-message-id
  const { data: state } = useQuery({
    queryKey: ["/api/state"],
    queryFn: fetchState,
  });

  // Initial chat load
  const { data: initial = [] } = useQuery({
    queryKey: ["/api/messages"],
    queryFn: () => fetchMessages({ limit: 200 }),
  });

  // Live message buffer (initial + anything posted since)
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  useEffect(() => {
    if (initial.length) setMessages(initial);
  }, [initial]);

  // Poll for new system_nudge rows every 30s
  useEffect(() => {
    if (!messages.length) return;
    const id = setInterval(async () => {
      const lastId = messages[messages.length - 1]?.id ?? 0;
      const fresh = await fetchMessages({ since_id: lastId });
      if (fresh.length) setMessages((m) => [...m, ...fresh]);
    }, 30_000);
    return () => clearInterval(id);
  }, [messages]);

  // Persona switch — optimistic
  const switchVibe = useMutation({
    mutationFn: setVibe,
    onMutate: async (vibe) => {
      await qc.cancelQueries({ queryKey: ["/api/state"] });
      const prev = qc.getQueryData<typeof state>(["/api/state"]);
      if (prev) qc.setQueryData(["/api/state"], { ...prev, vibe });
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(["/api/state"], ctx.prev);
    },
  });

  // Send
  const send = useMutation({
    mutationFn: postMessage,
    onSuccess: ({ user, assistant }) => {
      setMessages((m) => [...m, user, assistant]);
    },
  });

  const seen = useMutation({ mutationFn: markPersonasSeen });

  // Persona sheet — opens automatically on first run, on demand via the
  // `?` button after that. `null` means closed.
  const [sheetMode, setSheetMode] = useState<"first-run" | "info" | null>(null);
  useEffect(() => {
    if (state?.first_run) setSheetMode("first-run");
  }, [state?.first_run]);

  const onCloseSheet = () => {
    const wasFirstRun = sheetMode === "first-run";
    setSheetMode(null);
    if (wasFirstRun) {
      seen.mutate();
      qc.invalidateQueries({ queryKey: ["/api/state"] });
    }
  };

  const activeVibe: PersonaKey = state?.vibe ?? "smug_construct";

  return (
    <div className="flex h-full flex-col">
      <ChatHeader
        active={activeVibe}
        personas={state?.personas ?? []}
        onSwitch={(v) => switchVibe.mutate(v)}
        onOpenInfo={() => setSheetMode("info")}
      />
      <MessageList messages={messages} activeVibe={activeVibe} />
      <Composer
        onSubmit={(text) => send.mutate(text)}
        disabled={send.isPending}
        activeVibe={activeVibe}
      />
      <FirstRunSheet
        open={sheetMode !== null}
        mode={sheetMode ?? "first-run"}
        personas={state?.personas ?? []}
        active={activeVibe}
        onPick={(v) => switchVibe.mutate(v)}
        onClose={onCloseSheet}
      />
    </div>
  );
}

function ChatHeader({
  active,
  personas,
  onSwitch,
  onOpenInfo,
}: {
  active: PersonaKey;
  personas: { key: PersonaKey; display: string }[];
  onSwitch: (v: PersonaKey) => void;
  onOpenInfo: () => void;
}) {
  return (
    <header
      className="flex items-center justify-between border-b border-border px-4 py-3 sm:px-6"
      data-testid="chat-header"
    >
      <div className="flex items-baseline gap-3">
        <h1 className="font-mono text-sm uppercase tracking-widest text-muted-foreground">
          chat
        </h1>
        <span className="font-mono text-xs text-muted-foreground/70">
          {personas.find((p) => p.key === active)?.display ?? ""}
        </span>
      </div>
      <PersonaSwitcher
        active={active}
        personas={personas}
        onChange={onSwitch}
        onOpenInfo={onOpenInfo}
      />
    </header>
  );
}

function MessageList({
  messages,
  activeVibe,
}: {
  messages: ChatMessage[];
  activeVibe: PersonaKey;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [messages.length]);

  if (!messages.length) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center">
        <p className="max-w-md font-mono text-sm leading-relaxed text-muted-foreground">
          empty room. say something.
          <br />
          <span className="text-muted-foreground/60">
            currently listening as{" "}
            <span style={{ color: `hsl(var(${PERSONA_PRESENTATION[activeVibe].tint_var}))` }}>
              {PERSONA_PRESENTATION[activeVibe].letter}
            </span>
          </span>
        </p>
      </div>
    );
  }

  return (
    <div
      ref={ref}
      className="flex-1 overflow-y-auto px-4 py-6 sm:px-6"
      data-testid="message-list"
    >
      <div className="mx-auto flex max-w-2xl flex-col gap-4">
        {messages.map((m) => (
          <Bubble key={m.id} m={m} />
        ))}
      </div>
    </div>
  );
}

function Bubble({ m }: { m: ChatMessage }) {
  const tintVar = m.vibe ? PERSONA_PRESENTATION[m.vibe].tint_var : "--accent";
  const tint = `hsl(var(${tintVar}))`;

  if (m.role === "system_nudge") {
    return (
      <div
        className="self-start rounded-md border-l-2 px-3 py-2 text-sm text-muted-foreground"
        style={{ borderLeftColor: tint }}
        data-testid={`bubble-nudge-${m.id}`}
      >
        <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground/60">
          {m.vibe ? PERSONA_PRESENTATION[m.vibe].letter : "·"} · {timestamp(m.ts_utc)}
        </div>
        <div className="mt-1 leading-relaxed">{m.content}</div>
      </div>
    );
  }

  if (m.role === "user") {
    return (
      <div
        className="self-end max-w-[75%] rounded-md bg-secondary px-3 py-2 text-sm leading-relaxed"
        data-testid={`bubble-user-${m.id}`}
      >
        {m.content}
      </div>
    );
  }

  // assistant
  return (
    <div
      className="self-start max-w-[85%] border-l-2 pl-3 text-sm leading-relaxed"
      style={{ borderLeftColor: tint }}
      data-testid={`bubble-assistant-${m.id}`}
    >
      <div className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {m.vibe ? PERSONA_PRESENTATION[m.vibe].letter : "·"} · {timestamp(m.ts_utc)}
      </div>
      <div className="mt-1 whitespace-pre-wrap">{m.content}</div>
    </div>
  );
}

function timestamp(iso: string): string {
  try {
    return format(parseISO(iso), "HH:mm");
  } catch {
    return "";
  }
}

function Composer({
  onSubmit,
  disabled,
  activeVibe,
}: {
  onSubmit: (text: string) => void;
  disabled?: boolean;
  activeVibe: PersonaKey;
}) {
  const [text, setText] = useState("");
  const taRef = useRef<HTMLTextAreaElement>(null);
  const tintVar = PERSONA_PRESENTATION[activeVibe].tint_var;
  const tint = `hsl(var(${tintVar}))`;

  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setText("");
    taRef.current?.focus();
  };

  // Auto-grow up to ~6 lines
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 144) + "px";
  }, [text]);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="border-t border-border bg-card/50 px-4 py-3 sm:px-6"
      data-testid="composer"
    >
      <div
        className="mx-auto flex max-w-2xl items-end gap-2 rounded-md border border-border bg-background p-2 focus-within:ring-1"
        style={{ ["--tw-ring-color" as any]: tint }}
      >
        <textarea
          ref={taRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder="say something..."
          rows={1}
          className={cn(
            "flex-1 resize-none bg-transparent px-2 py-1 text-sm leading-relaxed",
            "outline-none placeholder:text-muted-foreground",
          )}
          data-testid="input-composer"
          disabled={disabled}
        />
        <Button
          type="submit"
          size="icon"
          variant="ghost"
          disabled={disabled || !text.trim()}
          data-testid="button-send"
          className="size-8"
          style={text.trim() ? { color: tint } : undefined}
        >
          <Send className="size-4" />
        </Button>
      </div>
    </form>
  );
}
