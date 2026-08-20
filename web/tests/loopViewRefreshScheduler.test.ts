import { LoopViewRefreshScheduler } from "../src/components/delivery-trace/loopViewRefreshScheduler.js";

function assert(condition: unknown, message: string): void {
  if (!condition) throw new Error(message);
}

class FakeClock {
  now = 0;
  private nextId = 0;
  private readonly timers = new Map<number, { at: number; callback: () => void }>();

  readonly clear = (timer: ReturnType<typeof setTimeout>): void => {
    this.timers.delete(timer as unknown as number);
  };

  readonly set = (callback: () => void, delayMs: number): ReturnType<typeof setTimeout> => {
    this.nextId += 1;
    this.timers.set(this.nextId, { at: this.now + delayMs, callback });
    return this.nextId as unknown as ReturnType<typeof setTimeout>;
  };

  advance(delayMs: number): void {
    const target = this.now + delayMs;
    while (true) {
      const due = [...this.timers.entries()]
        .filter(([, timer]) => timer.at <= target)
        .sort((left, right) => left[1].at - right[1].at || left[0] - right[0])[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].callback();
    }
    this.now = target;
  }

  pending(): number {
    return this.timers.size;
  }
}

function makeScheduler(clock: FakeClock, state: { refreshes: number; visible: boolean }) {
  return new LoopViewRefreshScheduler({
    clearTimer: clock.clear,
    debounceMs: 1500,
    isVisible: () => state.visible,
    maxWaitMs: 10_000,
    refresh: () => { state.refreshes += 1; },
    setTimer: clock.set,
  });
}

{
  const clock = new FakeClock();
  const state = { refreshes: 0, visible: true };
  const scheduler = makeScheduler(clock, state);
  scheduler.invalidate();
  clock.advance(500);
  scheduler.invalidate();
  clock.advance(500);
  scheduler.invalidate();
  clock.advance(1499);
  assert(state.refreshes === 0, "short bursts should use a trailing debounce");
  clock.advance(1);
  assert(state.refreshes === 1, "a short semantic burst should coalesce into one refresh");
  assert(clock.pending() === 0, "a debounce flush should clear its max-wait timer");
}

{
  const clock = new FakeClock();
  const state = { refreshes: 0, visible: true };
  const scheduler = makeScheduler(clock, state);
  for (let second = 0; second < 60; second += 1) {
    scheduler.invalidate();
    clock.advance(1000);
  }
  assert(state.refreshes === 6, "continuous semantic traffic should refresh at most six times per minute");
}

{
  const clock = new FakeClock();
  const state = { refreshes: 0, visible: false };
  const scheduler = makeScheduler(clock, state);
  scheduler.invalidate();
  scheduler.invalidate();
  clock.advance(20_000);
  assert(state.refreshes === 0 && clock.pending() === 0, "hidden pages must schedule zero requests");
  state.visible = true;
  scheduler.visibilityChanged();
  clock.advance(1500);
  assert(state.refreshes === 1, "hidden invalidations should reconcile once after visibility returns");

  scheduler.invalidate();
  state.visible = false;
  scheduler.visibilityChanged();
  clock.advance(20_000);
  assert(state.refreshes === 1, "hiding during debounce must cancel the pending refresh");
  state.visible = true;
  scheduler.visibilityChanged();
  clock.advance(1500);
  assert(state.refreshes === 2, "cancelled visible work should reconcile after returning");

  state.visible = false;
  scheduler.invalidate();
  scheduler.cancel();
  state.visible = true;
  scheduler.visibilityChanged();
  assert(clock.pending() === 0, "cancel should clear timers and hidden dirty state");
}

{
  const clock = new FakeClock();
  const state = { refreshes: 0, visible: true };
  const retry = new LoopViewRefreshScheduler({
    clearTimer: clock.clear,
    debounceMs: 30_000,
    isVisible: () => state.visible,
    maxWaitMs: 30_000,
    refresh: () => { state.refreshes += 1; },
    setTimer: clock.set,
  });
  retry.invalidate();
  retry.invalidate();
  clock.advance(29_999);
  assert(state.refreshes === 0, "background failures must not cause an immediate retry storm");
  clock.advance(1);
  assert(state.refreshes === 1, "a background failure should retry once after 30 seconds");
  retry.invalidate();
  clock.advance(30_000);
  assert(state.refreshes === 2, "a failed retry may schedule the next bounded 30 second retry");
}
