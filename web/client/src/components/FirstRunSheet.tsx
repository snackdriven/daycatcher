import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { PersonaKey, PersonaRow, PERSONA_PRESENTATION, PERSONA_ORDER } from "@/lib/api";
import { cn } from "@/lib/utils";

export type PersonaSheetMode = "first-run" | "info";

interface Props {
  open: boolean;
  mode?: PersonaSheetMode;
  active: PersonaKey;
  personas: PersonaRow[];
  onPick: (key: PersonaKey) => void;
  onClose: () => void;
}

/**
 * Persona introduction sheet. Two modes:
 *   first-run — shown once on a fresh install; closing it marks
 *               `web_prefs.personas_seen` so it never reappears.
 *   info      — opened on demand from the `?` button in PersonaSwitcher.
 *               Picking a card switches the vibe; closing just dismisses.
 *
 * The card grid and styling are identical; only header copy and the
 * footer button differ.
 */
export function FirstRunSheet({ open, mode = "first-run", active, personas, onPick, onClose }: Props) {
  const map = new Map(personas.map((p) => [p.key, p] as const));
  const ordered = PERSONA_ORDER.filter((k) => map.has(k)).map((k) => map.get(k)!);

  const isFirstRun = mode === "first-run";
  const title = isFirstRun ? "four voices, one room." : "who's who.";
  const description = isFirstRun
    ? "pick one to start. switch any time from the top right of the chat. mid-thread switches are fine; the new voice picks up from where the last one left off."
    : "tap a card to switch. the new voice picks up where the last one left off. close when you're done.";

  return (
    <Sheet open={open} onOpenChange={(v) => !v && onClose()}>
      <SheetContent
        side="bottom"
        className="max-h-[90dvh] overflow-y-auto bg-card text-foreground border-t border-border"
      >
        <SheetHeader className="text-left">
          <SheetTitle className="font-mono text-lg lowercase tracking-tight">
            {title}
          </SheetTitle>
          <SheetDescription className="text-sm leading-relaxed text-muted-foreground">
            {description}
          </SheetDescription>
        </SheetHeader>

        <div className="mt-6 grid gap-3 sm:grid-cols-2">
          {ordered.map((p) => {
            const meta = PERSONA_PRESENTATION[p.key];
            const isActive = active === p.key;
            const tint = `hsl(var(${meta.tint_var}))`;
            return (
              <button
                key={p.key}
                onClick={() => onPick(p.key)}
                data-testid={`persona-sheet-${p.key}`}
                className={cn(
                  "group flex flex-col items-start gap-2 rounded-md border bg-background p-4 text-left",
                  "transition-colors hover-elevate active-elevate-2",
                  isActive ? "border-foreground/40" : "border-border",
                )}
                style={isActive ? { borderColor: tint } : undefined}
              >
                <div className="flex items-baseline gap-3">
                  <span
                    className="font-mono text-2xl font-semibold leading-none"
                    style={{ color: tint }}
                  >
                    {meta.letter}
                  </span>
                  <span className="text-sm font-medium">{p.display}</span>
                </div>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {meta.blurb}
                </p>
              </button>
            );
          })}
        </div>

        <div className="mt-6 flex justify-end">
          <Button
            onClick={onClose}
            data-testid={isFirstRun ? "button-firstrun-done" : "button-info-close"}
            variant={isFirstRun ? "default" : "secondary"}
          >
            {isFirstRun ? (
              <>
                start with{" "}
                <span className="ml-1 font-mono">
                  {PERSONA_PRESENTATION[active].letter}
                </span>
              </>
            ) : (
              "close"
            )}
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
