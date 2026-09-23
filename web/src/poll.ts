// Polling for the dashboard's live views.

// Run `load` now, then again `ms` after each run settles, until the returned
// function is called. A chain of timeouts rather than an interval: a slow
// response (a history refresh behind a slow `sacct`) is never overlapped by the
// next request, so a struggling server is not piled onto.
export function startPolling(load: () => Promise<unknown>, ms: number): () => void {
  let stopped = false;
  let timer: number | undefined;
  const tick = (): void => {
    void load()
      .catch(() => undefined)
      .finally(() => {
        if (!stopped) timer = window.setTimeout(tick, ms);
      });
  };
  tick();
  return () => {
    stopped = true;
    window.clearTimeout(timer);
  };
}
