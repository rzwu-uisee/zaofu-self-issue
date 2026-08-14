import { useEffect, useState } from "react";

import { getProjectCost } from "../api/client";
import type { CostSummary } from "../api/types";

const COST_REFRESH_MS = 10_000;

interface ScopedCostState {
  cost: CostSummary;
  projectId: string;
}

export function useProjectCost(
  projectId: string,
  suppliedCost: CostSummary | null | undefined,
): CostSummary | null {
  const [scopedCost, setScopedCost] = useState<ScopedCostState | null>(null);
  const supplied = suppliedCost ?? null;
  const hasSupplied = supplied !== null;

  useEffect(() => {
    if (!projectId || hasSupplied) {
      setScopedCost(null);
      return;
    }
    let cancelled = false;
    const refresh = () => {
      void getProjectCost(projectId).then((cost) => {
        if (!cancelled) setScopedCost({ cost, projectId });
      }).catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, COST_REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [hasSupplied, projectId]);

  return supplied ?? (scopedCost?.projectId === projectId ? scopedCost.cost : null);
}
