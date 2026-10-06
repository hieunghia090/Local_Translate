import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useEvents } from './events';

class FakeES {
  static last: FakeES;
  onerror: (() => void) | null = null;
  listeners = new Map<string, (() => void)[]>();
  constructor(public url: string) { FakeES.last = this; }
  addEventListener(t: string, fn: () => void) { this.listeners.set(t, [...(this.listeners.get(t) ?? []), fn]); }
  emit(t: string) { (this.listeners.get(t) ?? []).forEach((f) => f()); }
  close() {}
}

beforeEach(() => { vi.useFakeTimers(); vi.stubGlobal('EventSource', FakeES); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function setup() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, 'invalidateQueries');
  renderHook(() => useEvents('b1'), { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider> });
  const keys = () => spy.mock.calls.map((c) => (c[0]!.queryKey as string[])[0]).sort();
  const tick = () => act(() => { vi.advanceTimersByTime(300); });
  return { keys, tick, reset: () => spy.mockClear() };
}

it('mở lại kết nối sau lỗi thì làm mới toàn bộ', () => {
  const { keys, tick } = setup();
  FakeES.last.emit('open'); // lần mở đầu: không resync
  tick();
  expect(keys()).toEqual([]);
  FakeES.last.onerror?.();
  FakeES.last.emit('open');
  tick();
  expect(keys()).toEqual(['book', 'books', 'chapter', 'chapters', 'deepseek-status', 'glossary', 'glossary-send-stats', 'glossary-suggestions', 'library-stats', 'log-summary', 'logs', 'notes', 'queue', 'review-fixes', 'revisions', 'usage']);
});

it('chapter.updated vẫn làm mới chapters', () => {
  const { keys, tick } = setup();
  FakeES.last.emit('chapter.updated');
  tick();
  expect(keys()).toContain('chapters');
});
it('job.updated chỉ làm mới queue, chapter, trạng thái DeepSeek và glossary', () => {
  const { keys, tick } = setup();
  FakeES.last.emit('job.updated');
  tick();
  expect(keys()).toEqual(['chapter', 'deepseek-status', 'glossary', 'glossary-suggestions', 'queue']);
});

