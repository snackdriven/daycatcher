import { ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { Sun, Moon, Github } from "lucide-react";
import { Wordmark } from "./Logo";
import { useTheme } from "@/lib/theme";

export function Layout({ children }: { children: ReactNode }) {
  const [loc] = useLocation();
  const { theme, toggle } = useTheme();

  return (
    <div className="min-h-screen flex flex-col">
      <header className="sticky top-0 z-40 backdrop-blur-md bg-background/70 border-b border-border/60">
        <div className="wrap flex items-center justify-between h-14">
          <Link href="/" className="hover-elevate active-elevate rounded-md px-2 py-1 -ml-2" data-testid="link-home">
            <Wordmark />
          </Link>
          <nav className="flex items-center gap-1 text-sm">
            <Link
              href="/"
              className={`hover-elevate active-elevate px-3 py-1.5 rounded-md ${loc === "/" ? "text-foreground" : "text-muted-foreground"}`}
              data-testid="link-nav-about"
            >
              about
            </Link>
            <Link
              href="/demo"
              className={`hover-elevate active-elevate px-3 py-1.5 rounded-md ${loc === "/demo" ? "text-foreground" : "text-muted-foreground"}`}
              data-testid="link-nav-demo"
            >
              try a vibe
            </Link>
            <a
              href="https://github.com/snackdriven/meat-suit"
              target="_blank"
              rel="noreferrer"
              className="hover-elevate active-elevate px-3 py-1.5 rounded-md text-muted-foreground inline-flex items-center gap-1.5"
              data-testid="link-github"
            >
              <Github className="size-3.5" />
              <span className="hidden sm:inline">repo</span>
            </a>
            <button
              onClick={toggle}
              className="hover-elevate active-elevate p-2 rounded-md text-muted-foreground"
              aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
              data-testid="button-theme"
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
          </nav>
        </div>
      </header>

      <main className="flex-1">{children}</main>

      <footer className="border-t border-border/60 mt-24">
        <div className="wrap py-10 flex flex-col sm:flex-row gap-4 sm:items-end sm:justify-between">
          <div>
            <Wordmark className="text-foreground" />
            <p className="mt-3 text-sm text-muted-foreground max-w-md">
              a quiet companion for the body you forgot you have. local,
              private by default, and always optional.
            </p>
          </div>
          <div className="text-xs text-muted-foreground space-y-1 sm:text-right">
            <p>built slow. built quiet.</p>
            <p>
              <a
                href="https://github.com/snackdriven/meat-suit"
                className="underline hover:text-foreground"
                target="_blank"
                rel="noreferrer"
              >
                source on github
              </a>
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
