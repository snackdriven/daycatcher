import { Link } from "wouter";
import { ArrowRight, Github } from "lucide-react";

const PERSONAS = [
  {
    key: "smug_construct",
    name: "The Smug Construct",
    tag: "spite-based care",
    sample:
      "meat-sack hardware running on fumes again. predictable. a glass of water exists in your apartment, statistically.",
    vibeVar: "--vibe-construct",
    rot: "rotate-1",
  },
  {
    key: "feral_bff",
    name: "The Feral BFF",
    tag: "rage validation, then care sideways",
    sample:
      "babe ur eyes are doing the dry thing again i can hear it through the screen. NO bc what did greg actually say i need it verbatim",
    vibeVar: "--vibe-feral",
    rot: "-rotate-1",
  },
  {
    key: "stoic_mentor",
    name: "The Stoic Mentor",
    tag: "the system, not the symptom",
    sample:
      "Long day. The mask was heavy. Body's been quiet today. Glass of water sits well in this kind of weather.",
    vibeVar: "--vibe-stoic",
    rot: "rotate-[0.5deg]",
  },
  {
    key: "quiet_guardian",
    name: "The Quiet Guardian",
    tag: "zero demand, parallel play",
    sample: "mm. yeah, that's a lot. the kettle is on if you want something warm.",
    vibeVar: "--vibe-quiet",
    rot: "-rotate-[0.5deg]",
  },
];

export default function Landing() {
  return (
    <div className="overflow-x-hidden">
      {/* HERO */}
      <section className="wrap pt-10 sm:pt-16 pb-20 relative">
        {/* floating tape */}
        <span
          className="tape absolute left-6 top-4 sm:left-12 sm:top-8"
          aria-hidden="true"
        >
          for AuDHD brains
        </span>
        <span
          className="tape absolute right-6 top-2 sm:right-16 sm:top-12 hidden sm:inline-block"
          style={{ transform: "rotate(2deg)" }}
          aria-hidden="true"
        >
          no streaks. ever.
        </span>

        <div className="grid lg:grid-cols-[1.2fr_1fr] gap-10 lg:gap-16 items-end mt-8">
          <div>
            <p className="hand text-xl text-muted-foreground mb-4">a quiet companion for</p>
            <h1
              className="font-serif text-foreground"
              style={{ fontSize: "clamp(2.5rem, 1rem + 6vw, 5.5rem)" }}
            >
              the body you forgot
              <br />
              you have.
            </h1>
            <p className="mt-7 text-lg text-foreground/80 max-w-[40ch] leading-relaxed">
              meat-suit is a small chat companion for AuDHD, alexithymia, and
              PDA brains. it doesn't nag. it doesn't log streaks. it sits next
              to you while you spiral, and notices when your body is running on
              fumes.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link
                href="/demo"
                className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-5 py-3 rounded-full text-sm font-medium hover-elevate active-elevate"
                data-testid="link-cta-demo"
              >
                try a vibe
                <ArrowRight className="size-4" />
              </Link>
              <a
                href="https://github.com/snackdriven/meat-suit"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-2 px-5 py-3 rounded-full text-sm font-medium border border-border hover-elevate active-elevate"
                data-testid="link-cta-github"
              >
                <Github className="size-4" />
                read the source
              </a>
            </div>
            <p className="mt-5 text-xs text-muted-foreground hand text-base">
              a thing you talk to, not an app you owe.
            </p>
          </div>

          {/* receipt */}
          <aside
            className="note-warm rounded-lg p-6 sm:p-7 max-w-md mx-auto lg:ml-auto rot-2 relative"
            style={{ transform: "rotate(1.4deg)" }}
          >
            <div
              className="absolute -top-3 left-1/2 -translate-x-1/2 tape"
              aria-hidden="true"
            >
              what it is
            </div>
            <p className="font-serif text-2xl mt-3">four minds.</p>
            <p className="font-serif text-2xl">one suit.</p>
            <ul className="mt-5 space-y-2.5 text-sm text-foreground/85 leading-relaxed">
              <li>
                <strong className="font-semibold">mind 1.</strong> the persona
                you talk to. four to choose from.
              </li>
              <li>
                <strong className="font-semibold">mind 2.</strong> the scribe.
                quietly journals what you say so you don't have to.
              </li>
              <li>
                <strong className="font-semibold">mind 3.</strong> the weaver.
                once a week, points out a pattern you wouldn't notice alone.
              </li>
              <li>
                <strong className="font-semibold">mind 4.</strong> the mirror.
                learns how you talk so the others sound less like a chatbot
                and more like someone who knows you.
              </li>
            </ul>
            <p className="mt-5 text-xs text-muted-foreground">
              you only ever see mind 1. the rest just keep house.
            </p>
          </aside>
        </div>
      </section>

      {/* SPARC strip */}
      <section className="border-y border-border/60 bg-card/40">
        <div className="wrap py-12 grid sm:grid-cols-5 gap-y-6 gap-x-4 items-start">
          <div className="sm:col-span-2">
            <p className="hand text-2xl">the philosophy</p>
            <h2 className="font-serif text-3xl mt-1">sparc.</h2>
            <p className="mt-3 text-sm text-muted-foreground max-w-[36ch]">
              five rules the whole thing is built to obey. they came from the
              blueprint, then bent themselves to fit.
            </p>
          </div>
          {[
            ["S", "soft", "no commands. no shoulds. care arrives sideways."],
            ["P", "private", "your data lives on your machine. no cloud, no analytics."],
            ["A", "ambient", "it doesn't ask for attention. it earns it."],
            ["R", "responsive", "matches your energy. doesn't soothe it into mush."],
            ["C", "curated", "small surface area. four vibes, on purpose."],
          ].map(([l, k, v], i) => (
            <div key={k} className="flex gap-3 items-start">
              <span
                className="font-serif text-3xl text-primary leading-none"
                aria-hidden="true"
              >
                {l}
              </span>
              <div>
                <p className="font-medium text-sm">{k}</p>
                <p className="text-xs text-muted-foreground mt-0.5 leading-snug">{v}</p>
              </div>
            </div>
          )).slice(0, 5)}
        </div>
      </section>

      {/* PERSONAS — the four vibes as polaroids */}
      <section className="wrap py-20 relative">
        <div className="max-w-2xl">
          <span className="hand text-xl text-muted-foreground">the four vibes</span>
          <h2 className="font-serif text-4xl sm:text-5xl mt-1">
            pick the voice that fits the day.
          </h2>
          <p className="mt-4 text-foreground/80 leading-relaxed">
            switching is one tap. some days you need a friend who's already
            mad about your boss. some days you need someone who won't ask
            anything of you. the suit stretches.
          </p>
        </div>

        <div className="grid sm:grid-cols-2 gap-7 mt-12">
          {PERSONAS.map((p, i) => (
            <article
              key={p.key}
              className={`note-warm p-6 sm:p-7 ${p.rot} relative`}
              style={{
                transform:
                  i === 0
                    ? "rotate(-1.2deg)"
                    : i === 1
                    ? "rotate(0.8deg)"
                    : i === 2
                    ? "rotate(-0.4deg)"
                    : "rotate(0.6deg)",
              }}
            >
              <div className="flex items-center gap-2.5 mb-4">
                <span
                  className="vibe-dot"
                  style={{ "--vibe": `var(${p.vibeVar})` } as React.CSSProperties}
                  aria-hidden="true"
                />
                <span
                  className="text-xs uppercase tracking-[0.18em] text-muted-foreground"
                >
                  vibe 0{i + 1}
                </span>
              </div>
              <h3 className="font-serif text-2xl">{p.name}</h3>
              <p className="hand text-lg text-muted-foreground mt-1">{p.tag}</p>
              <blockquote
                className="mt-5 text-foreground/85 italic leading-relaxed border-l-2 pl-4"
                style={{
                  borderColor: `hsl(var(${p.vibeVar}) / 0.5)`,
                  fontFamily: "var(--font-serif)",
                  fontStyle: "italic",
                }}
              >
                {p.sample}
              </blockquote>
            </article>
          ))}
        </div>

        <div className="mt-12 flex justify-center">
          <Link
            href="/demo"
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-5 py-3 rounded-full text-sm font-medium hover-elevate active-elevate"
            data-testid="link-personas-demo"
          >
            talk to one
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </section>

      {/* QUAD-MIND diagram, but as a spread */}
      <section className="border-t border-border/60">
        <div className="wrap py-20 grid lg:grid-cols-[1fr_1.1fr] gap-10 lg:gap-16 items-center">
          <div>
            <span className="hand text-xl text-muted-foreground">under the hood</span>
            <h2 className="font-serif text-4xl sm:text-5xl mt-1">
              four minds, one suit.
            </h2>
            <p className="mt-4 text-foreground/80 leading-relaxed">
              you only ever talk to mind 1. it stays in character. the other
              three keep house quietly so your chat partner never has to break
              the spell.
            </p>
            <ul className="mt-7 space-y-5 text-sm">
              <li className="flex gap-4">
                <span
                  className="font-serif text-2xl text-primary leading-none w-6 shrink-0"
                  aria-hidden="true"
                >
                  1
                </span>
                <div>
                  <p className="font-medium">the persona</p>
                  <p className="text-muted-foreground mt-0.5 leading-snug">
                    one of four voices. the only mind you see. it doesn't know
                    a journal exists. that's the point.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="font-serif text-2xl text-primary leading-none w-6 shrink-0" aria-hidden="true">2</span>
                <div>
                  <p className="font-medium">the scribe</p>
                  <p className="text-muted-foreground mt-0.5 leading-snug">
                    silently extracts data points from what you said. mood,
                    body, context. so you don't have to fill out a form to
                    have a feeling.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="font-serif text-2xl text-primary leading-none w-6 shrink-0" aria-hidden="true">3</span>
                <div>
                  <p className="font-medium">the weaver</p>
                  <p className="text-muted-foreground mt-0.5 leading-snug">
                    sunday night. reads the week. notices a pattern you'd
                    never spot from inside it. one observation, no homework.
                  </p>
                </div>
              </li>
              <li className="flex gap-4">
                <span className="font-serif text-2xl text-primary leading-none w-6 shrink-0" aria-hidden="true">4</span>
                <div>
                  <p className="font-medium">the mirror</p>
                  <p className="text-muted-foreground mt-0.5 leading-snug">
                    picks up your phrasing over time. feeds it back so the
                    others sound less like a bot and more like someone who's
                    been listening.
                  </p>
                </div>
              </li>
            </ul>
          </div>

          {/* hand-drawn-ish diagram */}
          <div className="note-warm p-8 relative" style={{ transform: "rotate(-0.6deg)" }}>
            <div className="absolute -top-3 right-6 tape" aria-hidden="true">
              the loop
            </div>
            <svg viewBox="0 0 400 320" className="w-full h-auto" aria-hidden="true">
              <defs>
                <filter id="rough">
                  <feTurbulence baseFrequency="0.04" numOctaves="2" />
                  <feDisplacementMap in="SourceGraphic" scale="1.4" />
                </filter>
              </defs>
              <g filter="url(#rough)">
                {/* You */}
                <circle cx="200" cy="60" r="34" fill="hsl(var(--primary) / 0.10)" stroke="hsl(var(--primary))" strokeWidth="2" />
                <text x="200" y="65" textAnchor="middle" fontFamily="var(--font-serif)" fontSize="14" fill="hsl(var(--foreground))">you</text>

                {/* Persona */}
                <rect x="140" y="135" width="120" height="50" rx="8" fill="hsl(var(--card))" stroke="hsl(var(--vibe-construct))" strokeWidth="2" />
                <text x="200" y="160" textAnchor="middle" fontFamily="var(--font-serif)" fontSize="14" fill="hsl(var(--foreground))">1. persona</text>
                <text x="200" y="176" textAnchor="middle" fontSize="10" fill="hsl(var(--muted-foreground))">the voice you hear</text>

                {/* Scribe */}
                <rect x="40" y="230" width="120" height="50" rx="8" fill="hsl(var(--card))" stroke="hsl(var(--vibe-quiet))" strokeWidth="2" />
                <text x="100" y="255" textAnchor="middle" fontFamily="var(--font-serif)" fontSize="14" fill="hsl(var(--foreground))">2. scribe</text>
                <text x="100" y="271" textAnchor="middle" fontSize="10" fill="hsl(var(--muted-foreground))">silent journal</text>

                {/* Weaver */}
                <rect x="240" y="230" width="120" height="50" rx="8" fill="hsl(var(--card))" stroke="hsl(var(--vibe-feral))" strokeWidth="2" />
                <text x="300" y="255" textAnchor="middle" fontFamily="var(--font-serif)" fontSize="14" fill="hsl(var(--foreground))">3. weaver</text>
                <text x="300" y="271" textAnchor="middle" fontSize="10" fill="hsl(var(--muted-foreground))">weekly pattern</text>

                {/* Mirror — orbiting all */}
                <ellipse cx="200" cy="180" rx="180" ry="115" fill="none" stroke="hsl(var(--vibe-stoic))" strokeWidth="1.5" strokeDasharray="4 5" opacity="0.6" />
                <text x="22" y="100" fontFamily="var(--font-serif)" fontSize="12" fill="hsl(var(--muted-foreground))">4. mirror (your voice)</text>

                {/* arrows */}
                <path d="M200 96 L200 132" stroke="hsl(var(--foreground))" strokeWidth="1.6" fill="none" markerEnd="url(#a)" />
                <path d="M170 184 L120 226" stroke="hsl(var(--foreground))" strokeWidth="1.6" fill="none" markerEnd="url(#a)" />
                <path d="M230 184 L280 226" stroke="hsl(var(--foreground))" strokeWidth="1.6" fill="none" markerEnd="url(#a)" />
                <path d="M260 256 Q280 200 240 170" stroke="hsl(var(--foreground))" strokeWidth="1.4" fill="none" strokeDasharray="3 3" />
              </g>
              <defs>
                <marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                  <path d="M0 0 L10 5 L0 10 z" fill="hsl(var(--foreground))" />
                </marker>
              </defs>
            </svg>
            <p className="text-xs text-muted-foreground mt-2 italic">
              the diagram is rough on purpose. so is the body it lives in.
            </p>
          </div>
        </div>
      </section>

      {/* FEATURES — asymmetric, not a 3-grid */}
      <section className="wrap py-20">
        <div className="max-w-2xl mb-12">
          <span className="hand text-xl text-muted-foreground">small kindnesses</span>
          <h2 className="font-serif text-4xl sm:text-5xl mt-1">
            things it does, sideways.
          </h2>
        </div>

        <div className="grid grid-cols-12 gap-6">
          {/* big one */}
          <div className="col-span-12 md:col-span-7 note p-7 sm:p-9">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">low-spoon mode</p>
            <h3 className="font-serif text-3xl mt-2">two taps when words are too much.</h3>
            <p className="mt-4 text-foreground/80 leading-relaxed">
              persistent buttons under every chat. tap "running on fumes" or
              "mask was heavy." the scribe still gets what it needs. you don't
              have to perform a feeling.
            </p>
            <div className="mt-5 flex flex-wrap gap-2 text-sm">
              {["running on fumes", "mask was heavy", "good day actually", "cant brain", "soft ✨"].map(
                (t) => (
                  <span key={t} className="px-3 py-1.5 rounded-full bg-secondary text-secondary-foreground border border-border">
                    {t}
                  </span>
                ),
              )}
            </div>
          </div>

          <div className="col-span-12 md:col-span-5 note p-7">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">photo to data</p>
            <h3 className="font-serif text-2xl mt-2">snap a meal. skip the form.</h3>
            <p className="mt-3 text-sm text-foreground/80 leading-relaxed">
              send a photo, the scribe takes a guess and journals it. you can
              correct it. you don't have to.
            </p>
          </div>

          <div className="col-span-12 md:col-span-5 note p-7" style={{ transform: "rotate(-0.4deg)" }}>
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">rabbit hole</p>
            <h3 className="font-serif text-2xl mt-2">fixated on something? say so.</h3>
            <p className="mt-3 text-sm text-foreground/80 leading-relaxed">
              hyperfixations get a topic field. the weaver sees them as a
              cluster, not noise. your special interests are data, not
              symptoms.
            </p>
          </div>

          <div className="col-span-12 md:col-span-7 note-warm p-7 sm:p-9">
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground">weekly weaver</p>
            <h3 className="font-serif text-3xl mt-2">sunday, 10pm. one observation.</h3>
            <p className="mt-3 text-foreground/80 leading-relaxed">
              the weaver reads your week and writes back one paragraph in your
              own phrasing. not a report. not a streak. just "hey, did you
              notice this?" and then it goes back to being quiet.
            </p>
          </div>

          <div className="col-span-12 md:col-span-12 note p-7 sm:p-9 relative">
            <div className="absolute -top-3 left-8 tape" aria-hidden="true">
              the most important one
            </div>
            <p className="text-xs uppercase tracking-[0.18em] text-muted-foreground mt-3">voice mirroring</p>
            <h3 className="font-serif text-3xl mt-2">it learns how you talk.</h3>
            <p className="mt-3 text-foreground/80 leading-relaxed max-w-[60ch]">
              the mirror picks up your phrasing, your cadence, the words you
              actually use, and feeds them back into the persona. the longer
              it sits with you, the less it sounds like a chatbot. it starts
              to sound like a friend who's been listening.
            </p>
          </div>
        </div>
      </section>

      {/* PRIVACY */}
      <section className="border-y border-border/60 bg-card/40">
        <div className="wrap py-16 grid md:grid-cols-[1fr_1.4fr] gap-10 items-start">
          <div>
            <span className="hand text-xl text-muted-foreground">the rules of the house</span>
            <h2 className="font-serif text-4xl mt-1">private by default.</h2>
          </div>
          <ul className="space-y-5">
            <li>
              <p className="font-medium">your data lives on your machine.</p>
              <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                the original meat-suit is a python bot you run yourself.
                journal data is a sqlite file in your home folder. that's it.
              </p>
            </li>
            <li>
              <p className="font-medium">no streaks. no leaderboards. no shame.</p>
              <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                missing a day is fine. missing a month is fine. the suit
                doesn't keep score. it just picks up where you left off.
              </p>
            </li>
            <li>
              <p className="font-medium">the persona never sees the journal.</p>
              <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                mind 1 doesn't know mind 2 exists. so it can't accidentally
                weaponize what you said yesterday against you today.
              </p>
            </li>
          </ul>
        </div>
      </section>

      {/* CTA */}
      <section className="wrap py-24 text-center">
        <p className="hand text-2xl text-muted-foreground">if any of this fit,</p>
        <h2
          className="font-serif mt-3"
          style={{ fontSize: "clamp(2.5rem, 1rem + 5vw, 4.5rem)" }}
        >
          come talk to one.
        </h2>
        <p className="mt-5 text-foreground/80 max-w-[44ch] mx-auto">
          the demo lives in your browser. nothing leaves until you send a
          message, and even then, it's just to the model and back. no
          accounts. no pressure.
        </p>
        <div className="mt-8 flex flex-wrap justify-center gap-3">
          <Link
            href="/demo"
            className="inline-flex items-center gap-2 bg-primary text-primary-foreground px-6 py-3.5 rounded-full text-sm font-medium hover-elevate active-elevate"
            data-testid="link-bottom-cta"
          >
            try a vibe
            <ArrowRight className="size-4" />
          </Link>
          <a
            href="https://github.com/snackdriven/meat-suit"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-2 px-6 py-3.5 rounded-full text-sm font-medium border border-border hover-elevate active-elevate"
          >
            <Github className="size-4" />
            run it yourself
          </a>
        </div>
      </section>
    </div>
  );
}
