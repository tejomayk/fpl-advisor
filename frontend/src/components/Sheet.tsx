import * as Dialog from "@radix-ui/react-dialog";
import { AnimatePresence, m, useDragControls, useReducedMotion } from "motion/react";
import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useMedia } from "../lib/useMedia";

export interface SheetSpec {
  title: string;
  /** Visible heading content; defaults to the title text. */
  head?: ReactNode;
  body: ReactNode;
}

const Ctx = createContext<{ show: (s: SheetSpec) => void; close: () => void }>({
  show: () => {},
  close: () => {},
});
export const useSheet = () => useContext(Ctx).show;
export const useCloseSheet = () => useContext(Ctx).close;

const spring = { type: "spring", damping: 34, stiffness: 340, mass: 0.9 } as const;

export function SheetProvider({ children }: { children: ReactNode }) {
  const [spec, setSpec] = useState<SheetSpec | null>(null);
  const [open, setOpen] = useState(false);
  const show = useCallback((s: SheetSpec) => {
    setSpec(s);
    setOpen(true);
  }, []);
  const wide = useMedia("(min-width: 768px)");
  const reduce = useReducedMotion();
  const drag = useDragControls();

  const from = reduce ? { opacity: 0 } : wide ? { x: "104%" } : { y: "100%" };
  const to = reduce ? { opacity: 1 } : wide ? { x: 0 } : { y: 0 };

  const value = useMemo(() => ({ show, close: () => setOpen(false) }), [show]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <AnimatePresence>
          {open && spec && (
            <Dialog.Portal forceMount>
              <Dialog.Overlay asChild forceMount>
                <m.div
                  className="fixed inset-0 z-40 bg-scrim"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.25 }}
                />
              </Dialog.Overlay>
              <Dialog.Content asChild forceMount aria-describedby={undefined}>
                <m.div
                  className={
                    wide
                      ? "fixed top-3 right-3 bottom-3 z-50 flex w-[min(460px,calc(100vw-24px))] flex-col overflow-hidden rounded-[28px] bg-panel shadow-[var(--shadow-sheet)] outline-none"
                      : "fixed inset-x-0 bottom-0 z-50 flex max-h-[90dvh] flex-col overflow-hidden rounded-t-[28px] bg-panel shadow-[var(--shadow-sheet)] outline-none"
                  }
                  initial={from}
                  animate={to}
                  exit={from}
                  transition={spring}
                  drag={wide || reduce ? false : "y"}
                  dragControls={drag}
                  dragListener={false}
                  dragConstraints={{ top: 0, bottom: 0 }}
                  dragElastic={{ top: 0, bottom: 0.7 }}
                  onDragEnd={(_, info) => {
                    if (info.offset.y > 110 || info.velocity.y > 600) setOpen(false);
                  }}
                >
                  <div
                    className="relative shrink-0 touch-none px-6 pt-3 pb-2 md:px-8 md:pt-7"
                    onPointerDown={(e) => !wide && drag.start(e)}
                  >
                    {!wide && <div aria-hidden="true" className="mx-auto mb-4 h-1 w-10 rounded-full bg-line-2" />}
                    <Dialog.Title className="pr-12 text-[22px] leading-tight font-semibold tracking-[-0.01em]">
                      {spec.head ?? spec.title}
                    </Dialog.Title>
                    <Dialog.Close
                      aria-label="Close"
                      className="absolute top-5 right-4 grid size-10 place-items-center rounded-full text-fg-2 transition-colors hover:bg-raised hover:text-fg md:top-6 md:right-6"
                    >
                      <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                        <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                      </svg>
                    </Dialog.Close>
                  </div>
                  <div className="scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain px-6 pt-4 pb-[max(2rem,env(safe-area-inset-bottom))] md:px-8">
                    {spec.body}
                  </div>
                </m.div>
              </Dialog.Content>
            </Dialog.Portal>
          )}
        </AnimatePresence>
      </Dialog.Root>
    </Ctx.Provider>
  );
}
