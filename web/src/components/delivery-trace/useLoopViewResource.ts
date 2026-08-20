import { useCallback, useEffect, useRef, useState } from "react";

import { getLoopView } from "../../api/client";
import type { LoopViewProjection, RecentEvent } from "../../api/types";
import { eventInvalidatesLoopView } from "../../app/pageLoadPolicy";
import type { LiveState } from "../../app/sharedTypes";
import { LoopViewRefreshScheduler } from "./loopViewRefreshScheduler";

const LIVE_REFRESH_DEBOUNCE_MS = 1500;
const LIVE_REFRESH_MAX_WAIT_MS = 10_000;
const DEGRADED_RECONCILE_MS = 30_000;
const REFRESH_RETRY_MS = 30_000;

interface UseLoopViewResourceOptions {
  liveEvents: RecentEvent[];
  liveState: LiveState;
  projectId?: string;
}

interface LoopViewResource {
  error: string;
  view: LoopViewProjection | null;
}

function eventKey(event: RecentEvent | undefined): string {
  if (!event) return "";
  return `${event.id ?? ""}:${event.seq ?? ""}:${event.ts ?? ""}:${event.type}`;
}

function isAbortError(error: unknown): boolean {
  return Boolean(error && typeof error === "object" && "name" in error && error.name === "AbortError");
}

export function useLoopViewResource({
  liveEvents,
  liveState,
  projectId,
}: UseLoopViewResourceOptions): LoopViewResource {
  const [view, setView] = useState<LoopViewProjection | null>(null);
  const [error, setError] = useState("");
  const scope = projectId ?? "";
  const activeScopeRef = useRef(scope);
  const mountedRef = useRef(false);
  const inFlightRef = useRef<{
    controller: AbortController;
    promise: Promise<LoopViewProjection>;
    scope: string;
  } | null>(null);
  const pendingRefreshRef = useRef(false);
  const initialTimerRef = useRef<number | undefined>(undefined);
  const loadViewRef = useRef<(clearCurrent: boolean) => void>(() => undefined);
  const seenLiveEventKeysRef = useRef(new Set(liveEvents.map(eventKey)));
  const previousLiveStateRef = useRef<LiveState>(liveState);
  const schedulerRef = useRef<LoopViewRefreshScheduler | null>(null);
  const retrySchedulerRef = useRef<LoopViewRefreshScheduler | null>(null);

  activeScopeRef.current = scope;
  if (schedulerRef.current === null) {
    schedulerRef.current = new LoopViewRefreshScheduler({
      debounceMs: LIVE_REFRESH_DEBOUNCE_MS,
      isVisible: () => document.visibilityState === "visible",
      maxWaitMs: LIVE_REFRESH_MAX_WAIT_MS,
      refresh: () => loadViewRef.current(false),
    });
  }
  if (retrySchedulerRef.current === null) {
    retrySchedulerRef.current = new LoopViewRefreshScheduler({
      debounceMs: REFRESH_RETRY_MS,
      isVisible: () => document.visibilityState === "visible",
      maxWaitMs: REFRESH_RETRY_MS,
      refresh: () => loadViewRef.current(false),
    });
  }

  const loadView = useCallback((clearCurrent: boolean) => {
    const requestScope = projectId ?? "";
    if (clearCurrent) {
      setView(null);
      setError("");
    }
    const active = inFlightRef.current;
    if (active?.scope === requestScope) {
      if (!clearCurrent) pendingRefreshRef.current = true;
      return;
    }
    if (active) active.controller.abort();

    const controller = new AbortController();
    const promise = getLoopView(projectId, { signal: controller.signal });
    const flight = { controller, promise, scope: requestScope };
    inFlightRef.current = flight;
    void promise
      .then((nextView) => {
        if (!mountedRef.current || activeScopeRef.current !== requestScope) return;
        setView(nextView);
        setError("");
        retrySchedulerRef.current?.cancel();
      })
      .catch((loadError) => {
        if (isAbortError(loadError) || !mountedRef.current || activeScopeRef.current !== requestScope) return;
        if (clearCurrent) {
          setError(String(loadError));
          return;
        }
        retrySchedulerRef.current?.invalidate();
      })
      .finally(() => {
        if (inFlightRef.current !== flight) return;
        inFlightRef.current = null;
        if (!pendingRefreshRef.current || !mountedRef.current || activeScopeRef.current !== requestScope) return;
        pendingRefreshRef.current = false;
        schedulerRef.current?.invalidate();
      });
  }, [projectId]);

  loadViewRef.current = loadView;

  useEffect(() => {
    mountedRef.current = true;
    pendingRefreshRef.current = false;
    seenLiveEventKeysRef.current = new Set(liveEvents.map(eventKey));
    schedulerRef.current?.cancel();
    retrySchedulerRef.current?.cancel();
    // Defer the first request past React StrictMode's effect probe. The probe
    // cancels this timer before fetch starts, so development still performs
    // one initial network read.
    initialTimerRef.current = window.setTimeout(() => {
      initialTimerRef.current = undefined;
      loadViewRef.current(true);
    }, 0);
    return () => {
      mountedRef.current = false;
      if (initialTimerRef.current !== undefined) {
        window.clearTimeout(initialTimerRef.current);
        initialTimerRef.current = undefined;
      }
      schedulerRef.current?.cancel();
      retrySchedulerRef.current?.cancel();
      const active = inFlightRef.current;
      if (active?.scope === scope) {
        active.controller.abort();
        inFlightRef.current = null;
      }
    };
  }, [loadView, scope]);

  useEffect(() => {
    const previousKeys = seenLiveEventKeysRef.current;
    const nextKeys = new Set<string>();
    let semanticChange = false;
    for (const event of liveEvents) {
      const key = eventKey(event);
      nextKeys.add(key);
      if (!previousKeys.has(key) && eventInvalidatesLoopView(event.type)) semanticChange = true;
    }
    seenLiveEventKeysRef.current = nextKeys;
    if (semanticChange) schedulerRef.current?.invalidate();
  }, [liveEvents]);

  useEffect(() => {
    const reconcileWhenVisible = () => {
      schedulerRef.current?.visibilityChanged();
      retrySchedulerRef.current?.visibilityChanged();
    };
    document.addEventListener("visibilitychange", reconcileWhenVisible);
    return () => document.removeEventListener("visibilitychange", reconcileWhenVisible);
  }, []);

  useEffect(() => {
    if (liveState === "live") return undefined;
    const timer = window.setInterval(() => schedulerRef.current?.invalidate(), DEGRADED_RECONCILE_MS);
    return () => window.clearInterval(timer);
  }, [liveState]);

  useEffect(() => {
    const previous = previousLiveStateRef.current;
    previousLiveStateRef.current = liveState;
    if (
      (previous === "reconnecting" || previous === "degraded")
      && liveState === "live"
    ) schedulerRef.current?.invalidate();
  }, [liveState]);

  return { error, view };
}
