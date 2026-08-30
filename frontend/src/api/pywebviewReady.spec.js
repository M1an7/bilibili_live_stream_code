import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPywebviewWaiter } from './pywebviewReady';


describe('createPywebviewWaiter', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps waiting in production after the development fallback deadline', async () => {
    vi.useFakeTimers();
    const target = new EventTarget();
    target.pywebview = null;
    const waitForPywebview = createPywebviewWaiter({ target, isDev: false });
    let settled = false;
    const waiting = waitForPywebview().then((ready) => {
      settled = true;
      return ready;
    });

    await vi.advanceTimersByTimeAsync(3_001);
    expect(settled).toBe(false);

    target.pywebview = { api: {} };
    target.dispatchEvent(new Event('pywebviewready'));
    await expect(waiting).resolves.toBe(true);
  });

  it('falls back after three seconds only in development preview', async () => {
    vi.useFakeTimers();
    const target = new EventTarget();
    target.pywebview = null;
    const waitForPywebview = createPywebviewWaiter({ target, isDev: true });
    const waiting = waitForPywebview();

    await vi.advanceTimersByTimeAsync(3_000);

    await expect(waiting).resolves.toBe(false);
  });

  it('resolves immediately when the bridge already exists', async () => {
    const target = new EventTarget();
    target.pywebview = { api: {} };
    const waitForPywebview = createPywebviewWaiter({ target, isDev: false });

    await expect(waitForPywebview()).resolves.toBe(true);
  });
});
