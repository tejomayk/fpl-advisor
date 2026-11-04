import { AnimatePresence, LazyMotion, m, MotionConfig } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Deadline } from "./components/Deadline";
import { Finder } from "./components/Finder";
import { SheetProvider, useCloseSheet, useSheet } from "./components/Sheet";
import { TabBar, type Item } from "./components/Tabs";
import { parseIds } from "./lib/ids";
import { prefetchAdvice } from "./lib/requests";
import { saved, useAdvice, writeUrl, type Status } from "./lib/store";
import type { Advice } from "./lib/types";
import { aboutSheet } from "./sheets/sheets";
import { League } from "./views/League";
import { hasScouting, Scouting } from "./views/Scouting";
import { Team } from "./views/Team";
import { ThisWeek } from "./views/ThisWeek";
import { useWhatIf } from "./views/WhatIf";

const loadMotion = () => import("./lib/motionFeatures").then((r) => r.default);

type View = "week" | "team" | "league" | "scout";
const VIEW_IDS: View[] = ["week", "team", "league", "scout"];

function viewsFor(d: Advice): Item<View>[] {
  const v: Item<View>[] = [{ id: "week", label: "This week" }];
  if (d.lineup?.xi?.length) v.push({ id: "team", label: "Team" });
  if (d.league) v.push({ id: "league", label: "League" });
  if (hasScouting(d)) v.push({ id: "scout", label: "Scouting" });
  return v;
}

function Mark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" width="22" height="22" aria-hidden="true" className={className}>
      <rect x="4" y="7" width="24" height="18" rx="2.5" fill="none" stroke="currentColor" strokeWidth="2" />
      <path d="M16 7v18" stroke="currentColor" strokeWidth="2" />
      <circle cx="16" cy="16" r="3.4" fill="var(--c-grass)" />
    </svg>
  );
}

/* ---------------- Change team ---------------- */

function ChangeTeam({ onLoad, onDemo, demo }: { onLoad: (t: number, l: number | null) => void; onDemo: () => void; demo: boolean }) {
  const close = useCloseSheet();
  return (
    <>
      <Finder
        initialTeam={saved.get("team")}
        initialLeague={saved.get("league")}
        autoFocus
        onSubmit={(t, l) => {
          close();
          onLoad(t, l);
        }}
        onPause={prefetchAdvice}
      />
      {!demo && (
        <button
          type="button"
          onClick={() => {
            close();
            onDemo();
          }}
          className="mt-8 text-[15px] text-fg-2 underline decoration-line-2 underline-offset-4 hover:text-fg"
        >
          See an example team
        </button>
      )}
    </>
  );
}

/* ---------------- Landing ---------------- */

function Landing({
  status,
  onLoad,
  onDemo,
  initial,
}: {
  status: Status;
  onLoad: (t: number, l: number | null) => void;
  onDemo: () => void;
  initial: { team: string; league: string; errors: { team?: string; league?: string } };
}) {
  const err = status.kind === "error" ? status.message : null;
  return (
    <div className="relative flex min-h-dvh flex-col overflow-hidden">
      <header className="relative mx-auto flex w-full max-w-[1120px] items-center gap-2.5 px-5 pt-6 text-[15px] font-semibold md:px-8">
        <Mark />
        FPL Advisor
      </header>
      <main id="main" className="relative mx-auto flex w-full max-w-[440px] flex-1 flex-col justify-center px-5 py-16">
        <m.h1
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 180, damping: 26 }}
          className="text-[44px] leading-[1.02] font-semibold tracking-[-0.035em] md:text-[56px]"
        >
          Win the week
          <br />
          <span className="text-fg-2">before kick-off.</span>
        </m.h1>
        <m.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 180, damping: 26, delay: 0.04 }}
          className="mt-4 text-[17px] leading-snug text-fg-2"
        >
          Transfer, captain and chip advice for your Fantasy Premier League team.
        </m.p>
        <m.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ type: "spring", stiffness: 180, damping: 26, delay: 0.08 }}
          className="mt-12"
        >
          <AnimatePresence>
            {err && (
              <m.p
                role="alert"
                initial={{ opacity: 0, y: -6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="mb-6 rounded-2xl bg-red-soft px-4 py-3 text-[15px] text-fg"
              >
                {err}
              </m.p>
            )}
          </AnimatePresence>
          <Finder
            key={`${initial.team}|${initial.league}`}
            initialTeam={initial.team}
            initialLeague={initial.league}
            initialErrors={initial.errors}
            onSubmit={onLoad}
            onPause={prefetchAdvice}
          />
          <m.button
            type="button"
            onClick={onDemo}
            whileHover={{ x: 2 }}
            className="mt-10 inline-flex items-center gap-2 text-[15px] font-medium text-grass-ink"
          >
            See an example
            <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
              <path d="M5 12h13m-5-5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </m.button>
        </m.div>
      </main>
      <footer className="relative px-5 pb-8 text-center text-[13px] text-fg-3">
        Free · No login · Unofficial, not affiliated with the Premier League
      </footer>
    </div>
  );
}

/* ---------------- Loading ---------------- */

function Loading({ slow }: { slow: 0 | 1 | 2 }) {
  const msg = ["Reading your squad", "Fetching the latest FPL data. The first load can take a minute.", "Still working. FPL is slow near deadlines."][slow];
  return (
    <div className="mx-auto w-full max-w-[640px] px-5 pt-10 md:px-0 md:pt-20" role="status" aria-live="polite">
      <div className="h-4 w-28 animate-pulse rounded-full bg-raised" />
      <div className="mt-5 h-24 w-56 animate-pulse rounded-3xl bg-raised" />
      <div className="mt-20 space-y-5">
        {[0, 1, 2].map((i) => (
          <div key={i} className="flex items-center gap-4" style={{ opacity: 1 - i * 0.25 }}>
            <div className="size-8 animate-pulse rounded-full bg-raised" />
            <div className="h-4 flex-1 animate-pulse rounded-full bg-raised" />
            <div className="h-7 w-16 animate-pulse rounded-full bg-raised" />
          </div>
        ))}
      </div>
      <AnimatePresence mode="wait">
        <m.p
          key={slow}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          className="mt-14 text-[15px] text-fg-2"
        >
          {msg}…
        </m.p>
      </AnimatePresence>
    </div>
  );
}

/* ---------------- Shell ---------------- */

function TopBar({
  d,
  demo,
  views,
  view,
  setView,
  onChange,
}: {
  d: Advice | null;
  demo: boolean;
  views: Item<View>[];
  view: View;
  setView: (v: View) => void;
  onChange: () => void;
}) {
  const name = d?.team?.name || (d ? "Your team" : "");
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-bg">
      <div className="mx-auto grid h-16 max-w-[1120px] grid-cols-[1fr_auto] items-center gap-4 px-5 md:grid-cols-[1fr_auto_1fr] md:px-8">
        <button
          type="button"
          onClick={onChange}
          aria-label={d ? `${name}${demo ? " (example)" : ""}. Change team` : "Change team"}
          className="group flex min-w-0 items-center gap-2.5 justify-self-start rounded-full py-1.5 pr-2 text-left"
        >
          <Mark className="shrink-0 text-fg" />
          <span className="truncate text-[16px] font-semibold">{name}</span>
          {demo && (
            <span className="hidden shrink-0 rounded-full bg-raised px-2 py-0.5 text-[12px] font-medium text-fg-2 sm:inline">Example</span>
          )}
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className="shrink-0 text-fg-3 transition-transform group-hover:translate-y-px">
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </button>
        <TabBar items={views} value={view} onChange={setView} className="hidden md:flex" />
        <div className="flex items-baseline gap-2 justify-self-end text-[15px]">
          {d?.gameweek?.deadline && (
            <span className="text-[13px] text-fg-3 sm:text-[15px]">
              {d?.gameweek?.id != null ? <span className="hidden sm:inline">GW{d.gameweek.id} deadline</span> : null}
              <span className={d?.gameweek?.id != null ? "sm:hidden" : ""}>Deadline</span>
            </span>
          )}
          <Deadline iso={d?.gameweek?.deadline} className="font-semibold" />
        </div>
      </div>
      {views.length > 1 && (
        <div className="no-scrollbar overflow-x-auto px-3 pb-2.5 md:hidden">
          <TabBar items={views} value={view} onChange={setView} className="justify-between" pill="view-pill-mob" />
        </div>
      )}
    </header>
  );
}

function Results({
  d,
  demo,
  initialView,
  onView,
  openChange,
  team,
  league,
  linkDraft,
}: {
  d: Advice;
  demo: boolean;
  initialView: View;
  onView: (v: View) => void;
  openChange: () => void;
  team?: number;
  league?: number | null;
  linkDraft: string | null;
}) {
  const show = useSheet();
  const wi = useWhatIf(d, demo ? undefined : team, league, linkDraft);
  const views = useMemo(() => viewsFor(d), [d]);
  const [view, setViewRaw] = useState<View>(views.some((v) => v.id === initialView) ? initialView : "week");
  const dir = useRef(0);
  const setView = useCallback(
    (v: View) => {
      const a = views.findIndex((x) => x.id === view);
      const b = views.findIndex((x) => x.id === v);
      dir.current = b > a ? 1 : -1;
      setViewRaw(v);
      onView(v);
      window.scrollTo({ top: 0 });
    },
    [view, views, onView],
  );
  const step = (k: number) => {
    const i = views.findIndex((x) => x.id === view) + k;
    if (i >= 0 && i < views.length) setView(views[i].id);
  };
  return (
    <>
      <TopBar d={d} demo={demo} views={views} view={view} setView={setView} onChange={openChange} />
      <m.main
        id="main"
        className="min-h-[calc(100dvh-8rem)] overflow-x-clip"
        style={{ touchAction: "pan-y" }}
        onPanEnd={(e, info) => {
          if ((e as PointerEvent).pointerType !== "touch") return;
          if (Math.abs(info.offset.x) > 70 && Math.abs(info.offset.x) > Math.abs(info.offset.y) * 1.6) step(info.offset.x < 0 ? 1 : -1);
        }}
      >
        <AnimatePresence mode="wait" initial={false} custom={dir.current}>
          <m.div
            key={view}
            role="tabpanel"
            id={`panel-${view}`}
            aria-labelledby={`tab-${view}`}
            custom={dir.current}
            variants={{
              enter: (k: number) => ({ opacity: 0, x: k * 28 }),
              center: { opacity: 1, x: 0 },
              exit: (k: number) => ({ opacity: 0, x: k * -28 }),
            }}
            initial="enter"
            animate="center"
            exit="exit"
            transition={{ type: "spring", stiffness: 380, damping: 36, opacity: { duration: 0.18 } }}
          >
            {view === "week" && <ThisWeek d={d} demo={demo} wi={wi} />}
            {view === "team" && <Team d={d} />}
            {view === "league" && <League d={d} />}
            {view === "scout" && <Scouting d={d} />}
          </m.div>
        </AnimatePresence>
      </m.main>
      <footer className="mx-auto mt-16 flex max-w-[640px] flex-wrap items-center justify-center gap-x-3 gap-y-1 px-5 pb-12 text-[13px] text-fg-3 md:mt-24">
        <button type="button" onClick={() => show(aboutSheet(d))} className="underline decoration-line-2 underline-offset-4 hover:text-fg">
          How this works
        </button>
        <span aria-hidden="true">·</span>
        <span>Unofficial</span>
      </footer>
    </>
  );
}

function AppInner() {
  const { status, load, loadDemo, reset } = useAdvice();
  const show = useSheet();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const qView = params.get("view") as View | null;
  // A shared what-if draft (lib/drafts.ts), only for the team in the same link.
  const linkDraft = useMemo(() => params.get("draft"), [params]);
  const [view, setView] = useState<View>(qView && VIEW_IDS.includes(qView) ? qView : "week");
  const [initial, setInitial] = useState({ team: saved.get("team"), league: saved.get("league"), errors: {} as { team?: string; league?: string } });

  const go = useCallback(
    (team: number, league: number | null) => {
      writeUrl({ team, league, view: "week", draft: null });
      setView("week");
      load(team, league);
    },
    [load],
  );
  const demo = useCallback(() => {
    writeUrl({ demo: true, view: "week" });
    setView("week");
    loadDemo();
  }, [loadDemo]);

  // Boot: ?demo=1, or ?team=&league= deep links; otherwise the finder with the last team filled in.
  useEffect(() => {
    if (params.get("demo") === "1") {
      loadDemo();
      return;
    }
    const qTeam = params.get("team");
    const qLeague = params.get("league");
    if (qTeam) {
      const r = parseIds(qTeam, qLeague || "");
      if (r.team && !r.errors.league) {
        load(r.team, r.league);
        return;
      }
      setInitial({ team: r.team ? String(r.team) : qTeam, league: r.league ? String(r.league) : qLeague || "", errors: r.errors });
      return;
    }
    // A returning visitor: start their advice now, so it's ready (or nearly) when they press the button.
    const back = parseIds(saved.get("team"), saved.get("league"));
    if (back.team && !back.errors.league) prefetchAdvice(back.team, back.league);
  }, [params, load, loadDemo]);

  const isDemo = status.kind !== "idle" && status.demo;
  const openChange = () =>
    show({
      title: isDemo ? "Use your team" : "Change team",
      body: <ChangeTeam onLoad={go} onDemo={demo} demo={isDemo} />,
    });

  if (status.kind === "idle" || (status.kind === "error" && !status.team && !status.demo)) {
    return <Landing status={status} onLoad={go} onDemo={demo} initial={initial} />;
  }
  if (status.kind === "error") {
    return (
      <>
        <TopBar d={null} demo={false} views={[]} view="week" setView={() => {}} onChange={openChange} />
        <main id="main" className="mx-auto w-full max-w-[520px] px-5 pt-16 md:pt-28" role="alert">
          <p className="text-[28px] leading-tight font-semibold tracking-[-0.02em]">Couldn't get your advice</p>
          <p className="mt-4 text-[17px] leading-relaxed text-fg-2">{status.message}</p>
          <div className="mt-10 flex flex-wrap gap-3">
            <m.button
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => (status.demo ? loadDemo() : status.team && load(status.team, status.league ?? null))}
              className="h-12 rounded-full bg-grass px-6 text-[16px] font-semibold text-on-grass"
            >
              Try again
            </m.button>
            <m.button
              type="button"
              whileTap={{ scale: 0.97 }}
              onClick={() => {
                writeUrl({});
                reset();
              }}
              className="h-12 rounded-full bg-raised px-6 text-[16px] font-medium"
            >
              Change team
            </m.button>
          </div>
        </main>
      </>
    );
  }
  if (status.kind === "loading") {
    return (
      <>
        <TopBar d={null} demo={status.demo} views={[]} view="week" setView={() => {}} onChange={openChange} />
        <Loading slow={status.slow} />
      </>
    );
  }
  return (
    <Results
      key={`${status.team ?? "demo"}-${status.league ?? ""}`}
      d={status.data}
      demo={status.demo}
      initialView={view}
      onView={(v) => {
        setView(v);
        writeUrl({ team: status.team, league: status.league, demo: status.demo, view: v });
      }}
      openChange={openChange}
      team={status.team}
      league={status.league}
      linkDraft={status.team && String(status.team) === params.get("team") ? linkDraft : null}
    />
  );
}

export function App() {
  return (
    <LazyMotion features={loadMotion} strict>
    <MotionConfig reducedMotion="user">
      <SheetProvider>
        <a
          href="#main"
          className="sr-only z-50 rounded-full bg-fg px-4 py-2 text-bg focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
        >
          Skip to content
        </a>
        <AppInner />
      </SheetProvider>
    </MotionConfig>
    </LazyMotion>
  );
}
