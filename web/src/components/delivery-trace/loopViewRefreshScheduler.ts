export interface LoopViewRefreshSchedulerOptions {
  clearTimer?: (timer: ReturnType<typeof setTimeout>) => void;
  debounceMs: number;
  isVisible: () => boolean;
  maxWaitMs: number;
  refresh: () => void;
  setTimer?: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
}

// Trailing debounce coalesces short bursts, while max-wait bounds staleness
// under a sustained stream. Hidden pages record only a dirty bit and issue no
// requests until visibility reconciliation.
export class LoopViewRefreshScheduler {
  private readonly clearTimer: (timer: ReturnType<typeof setTimeout>) => void;
  private readonly debounceMs: number;
  private readonly isVisible: () => boolean;
  private readonly maxWaitMs: number;
  private readonly refresh: () => void;
  private readonly setTimer: (callback: () => void, delayMs: number) => ReturnType<typeof setTimeout>;
  private hiddenDirty = false;
  private debounceTimer: ReturnType<typeof setTimeout> | undefined;
  private maxWaitTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(options: LoopViewRefreshSchedulerOptions) {
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer));
    this.debounceMs = options.debounceMs;
    this.isVisible = options.isVisible;
    this.maxWaitMs = Math.max(options.debounceMs, options.maxWaitMs);
    this.refresh = options.refresh;
    this.setTimer = options.setTimer ?? ((callback, delayMs) => setTimeout(callback, delayMs));
  }

  invalidate(): void {
    if (!this.isVisible()) {
      this.hiddenDirty = true;
      return;
    }
    this.schedule();
  }

  visibilityChanged(): void {
    if (!this.isVisible()) {
      if (this.debounceTimer !== undefined || this.maxWaitTimer !== undefined) {
        this.clearScheduledTimers();
        this.hiddenDirty = true;
      }
      return;
    }
    if (!this.hiddenDirty) return;
    this.hiddenDirty = false;
    this.schedule();
  }

  cancel(): void {
    this.clearScheduledTimers();
    this.hiddenDirty = false;
  }

  private schedule(): void {
    if (this.debounceTimer !== undefined) this.clearTimer(this.debounceTimer);
    this.debounceTimer = this.setTimer(() => this.flush(), this.debounceMs);
    if (this.maxWaitTimer === undefined) {
      this.maxWaitTimer = this.setTimer(() => this.flush(), this.maxWaitMs);
    }
  }

  private flush(): void {
    this.clearScheduledTimers();
    if (!this.isVisible()) {
      this.hiddenDirty = true;
      return;
    }
    this.refresh();
  }

  private clearScheduledTimers(): void {
    if (this.debounceTimer !== undefined) this.clearTimer(this.debounceTimer);
    if (this.maxWaitTimer !== undefined) this.clearTimer(this.maxWaitTimer);
    this.debounceTimer = undefined;
    this.maxWaitTimer = undefined;
  }
}
