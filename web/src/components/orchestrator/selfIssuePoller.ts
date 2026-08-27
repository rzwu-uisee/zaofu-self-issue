export interface SelfIssuePollScheduler {
  set(callback: () => void, delayMs: number): unknown;
  clear(handle: unknown): void;
}

const browserScheduler: SelfIssuePollScheduler = {
  set: (callback, delayMs) => window.setTimeout(callback, delayMs),
  clear: (handle) => window.clearTimeout(handle as number),
};

export interface SelfIssueReadPollerOptions<T> {
  intervalMs: number;
  request: () => Promise<T>;
  onResult: (result: T) => void;
  onError?: (error: unknown) => void;
  isEnabled?: () => boolean;
  scheduler?: SelfIssuePollScheduler;
}

/**
 * Polls only after the preceding request has settled. This prevents a slow
 * project read from turning a fixed interval into an unbounded request queue.
 */
export class SelfIssueReadPoller<T> {
  private readonly intervalMs: number;
  private readonly request: () => Promise<T>;
  private readonly onResult: (result: T) => void;
  private readonly onError: (error: unknown) => void;
  private readonly isEnabled: () => boolean;
  private readonly scheduler: SelfIssuePollScheduler;
  private timer: unknown = null;
  private running = false;
  private stopped = true;

  constructor(options: SelfIssueReadPollerOptions<T>) {
    this.intervalMs = options.intervalMs;
    this.request = options.request;
    this.onResult = options.onResult;
    this.onError = options.onError ?? (() => undefined);
    this.isEnabled = options.isEnabled ?? (() => true);
    this.scheduler = options.scheduler ?? browserScheduler;
  }

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.schedule(this.intervalMs);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer !== null) this.scheduler.clear(this.timer);
    this.timer = null;
  }

  wake(): void {
    if (this.stopped || this.running || !this.isEnabled()) return;
    if (this.timer !== null) this.scheduler.clear(this.timer);
    this.timer = null;
    this.schedule(0);
  }

  private schedule(delayMs: number): void {
    if (this.stopped || this.timer !== null) return;
    this.timer = this.scheduler.set(() => {
      this.timer = null;
      void this.tick();
    }, delayMs);
  }

  private async tick(): Promise<void> {
    if (this.stopped) return;
    if (!this.isEnabled()) {
      this.schedule(this.intervalMs);
      return;
    }
    if (this.running) return;
    this.running = true;
    try {
      const result = await this.request();
      if (!this.stopped) this.onResult(result);
    } catch (error: unknown) {
      if (!this.stopped) this.onError(error);
    } finally {
      this.running = false;
      if (!this.stopped) this.schedule(this.intervalMs);
    }
  }
}
