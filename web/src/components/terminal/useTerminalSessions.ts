import { useCallback, useEffect, useState } from "react";
import {
  createTerminalSession,
  fetchTerminalSessions,
  renameTerminalSession,
  stopTerminalSession,
} from "./api";
import type { TerminalProvider, TerminalSessionsPage } from "./types";

export function useTerminalSessions(projectId: string) {
  const [page, setPage] = useState<TerminalSessionsPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    if (!projectId) {
      setPage(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setPage(await fetchTerminalSessions(projectId));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const create = useCallback(async (provider: TerminalProvider, slot: string, title: string) => {
    setBusy(true);
    try {
      const response = await createTerminalSession(projectId, provider, slot, title);
      if (!response.session) throw new Error(response.reason ?? "terminal create returned no session");
      await refresh();
      return response.session;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const rename = useCallback(async (sessionId: string, title: string) => {
    setBusy(true);
    try {
      const response = await renameTerminalSession(projectId, sessionId, title);
      if (!response.session) throw new Error(response.reason ?? "terminal rename returned no session");
      await refresh();
      return response.session;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  const stop = useCallback(async (sessionId: string) => {
    setBusy(true);
    try {
      await stopTerminalSession(projectId, sessionId);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      throw reason;
    } finally {
      setBusy(false);
    }
  }, [projectId, refresh]);

  return { page, loading, busy, error, refresh, create, rename, stop };
}
