import {
  PersonaKey,
  PersonaRow,
  PERSONA_PRESENTATION,
  PERSONA_ORDER,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { HelpCircle } from "lucide-react";

interface Props {
  active: PersonaKey;
  personas: PersonaRow[];
  onChange: (key: PersonaKey) => void;
  onOpenInfo?: () => void;
  disabled?: boolean;
}

/**
 * Letter-mark segmented control (C / F / A / I). Active button fills with
 * the persona's tint color; inactive buttons hold the letter on a muted ring.
 * Hover reveals the full persona display name.
 */
export function PersonaSwitcher({ active, personas, onChange, onOpenInfo, disabled }: Props) {
  // Maintain a stable order even if /api/personas comes back shuffled.
  const map = new Map(personas.map((p) => [p.key, p] as const));
  const ordered = PERSONA_ORDER.filter((k) => map.has(k)).map((k) => map.get(k)!);

  return (
    <div
      role="radiogroup"
      aria-label="persona"
      data-testid="persona-switcher"
      className="inline-flex items-center gap-1 rounded-md border border-border bg-secondary/40 p-0.5"
    >
      {ordered.map((p) => {
        const meta = PERSONA_PRESENTATION[p.key];
        const isActive = active === p.key;
        const tint = `hsl(var(${meta.tint_var}))`;
        return (
          <Tooltip key={p.key}>
            <TooltipTrigger asChild>
              <button
                role="radio"
                aria-checked={isActive}
                disabled={disabled}
                onClick={() => onChange(p.key)}
                data-testid={`button-persona-${p.key}`}
                className={cn(
                  "relative size-7 rounded-sm font-mono text-xs font-semibold tracking-tight",
                  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive
                    ? "text-background"
                    : "text-muted-foreground hover:text-foreground",
                )}
                style={isActive ? { backgroundColor: tint } : undefined}
              >
                {meta.letter}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="font-mono text-xs">
              {p.display}
            </TooltipContent>
          </Tooltip>
        );
      })}
      {onOpenInfo && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={onOpenInfo}
              data-testid="button-persona-info"
              className="ml-0.5 flex size-7 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="about the personas"
            >
              <HelpCircle className="size-4" strokeWidth={1.75} />
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="font-mono text-xs">
            who's who
          </TooltipContent>
        </Tooltip>
      )}
    </div>
  );
}
