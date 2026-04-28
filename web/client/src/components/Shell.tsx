import { ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { MessageSquare, BookOpen, BarChart3, SlidersHorizontal } from "lucide-react";
import { fetchState } from "@/lib/api";
import { cn } from "@/lib/utils";

interface NavItem {
  href: string;
  label: string;
  icon: typeof MessageSquare;
  /** show an unread dot when this returns truthy */
  unread?: (state: { unread_journal: number }) => boolean;
}

const NAV: NavItem[] = [
  { href: "/", label: "chat", icon: MessageSquare },
  {
    href: "/journal",
    label: "journal",
    icon: BookOpen,
    unread: (s) => s.unread_journal > 0,
  },
  { href: "/data", label: "data", icon: BarChart3 },
  { href: "/settings", label: "settings", icon: SlidersHorizontal },
];

export function Shell({ children }: { children: ReactNode }) {
  const [location] = useLocation();

  // Poll state every 20s so the unread dot stays fresh without WebSockets.
  const { data: state } = useQuery({
    queryKey: ["/api/state"],
    queryFn: fetchState,
    refetchInterval: 20_000,
    refetchOnWindowFocus: true,
    staleTime: 5_000,
  });

  return (
    <div className="flex h-dvh flex-col bg-background text-foreground">
      <main className="flex-1 overflow-hidden pb-[68px] sm:pb-0 sm:pl-[68px]">
        {children}
      </main>
      <Nav location={location} unread={state?.unread_journal ?? 0} />
    </div>
  );
}

function Nav({ location, unread }: { location: string; unread: number }) {
  return (
    <nav
      aria-label="primary"
      data-testid="nav-primary"
      className={cn(
        // Mobile: fixed bottom bar. Desktop (sm+): fixed left rail.
        "fixed inset-x-0 bottom-0 z-30 flex h-[68px] items-stretch border-t border-sidebar-border bg-sidebar text-sidebar-foreground",
        "sm:inset-y-0 sm:right-auto sm:h-auto sm:w-[68px] sm:flex-col sm:border-r sm:border-t-0 sm:items-center sm:py-4",
      )}
    >
      <Link
        href="/"
        className="hidden sm:flex items-center justify-center mb-4 mt-1 size-10 rounded-md hover-elevate active-elevate-2"
        aria-label="meat-suit home"
        data-testid="link-logo"
      >
        <Logo />
      </Link>
      {NAV.map((item) => {
        const active = location === item.href;
        const showDot = item.href === "/journal" && unread > 0;
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            data-testid={`link-${item.label}`}
            className={cn(
              "relative flex flex-1 sm:flex-none flex-col items-center justify-center gap-1 px-2 py-2",
              "sm:size-12 sm:rounded-md sm:my-1 hover-elevate active-elevate-2 transition-colors",
              active
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
              showDot && "unread-dot",
            )}
            aria-current={active ? "page" : undefined}
          >
            <Icon className="size-5" strokeWidth={active ? 2.25 : 1.75} />
            <span className="text-[10px] tracking-wide uppercase sm:hidden">
              {item.label}
            </span>
            {active && (
              <span
                aria-hidden
                className="hidden sm:block absolute right-0 top-1/2 -translate-y-1/2 h-6 w-[2px] bg-accent"
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}

function Logo() {
  // Compact mark: a stacked rectangle (the suit) with a faint inner notch (the meat).
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-5 text-foreground"
      aria-label="meat-suit"
    >
      <rect x="4" y="6" width="16" height="14" rx="2" />
      <path d="M9 6V4a3 3 0 0 1 6 0v2" />
      <path d="M9 12h6" stroke="currentColor" opacity="0.5" />
    </svg>
  );
}
