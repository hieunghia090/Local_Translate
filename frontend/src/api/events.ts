import { useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';

const INVALIDATE: Record<string, string[]> = {
  'book.stats_updated': ['books', 'library-stats', 'book'],
  'chapter.updated': ['chapters', 'chapter', 'book', 'revisions', 'review-fixes', 'notes'],
  'job.updated': ['queue', 'chapter', 'deepseek-status', 'glossary', 'glossary-suggestions'],
  'log.appended': ['logs', 'log-summary', 'usage', 'glossary-send-stats'],
  'glossary.suggestions': ['glossary-suggestions', 'glossary'],
  // Server phát `resync` sau khi kết nối LISTEN tới Postgres bị đứt rồi nối lại: làm mới mọi thứ.
  resync: ['books', 'library-stats', 'book', 'chapters', 'chapter', 'revisions', 'queue', 'logs', 'log-summary',
    'deepseek-status', 'review-fixes', 'notes', 'usage', 'glossary', 'glossary-suggestions', 'glossary-send-stats'],
};
const FLUSH_MS = 250; // tối đa 4 lần mỗi giây

/** Mở một kết nối SSE và làm mới các query liên quan khi có sự kiện. */
export function useEvents(bookId?: string): void {
  const qc = useQueryClient();
  useEffect(() => {
    if (typeof EventSource === 'undefined') return;
    const url = bookId ? `/api/v1/events?book_id=${encodeURIComponent(bookId)}` : '/api/v1/events';
    const source = new EventSource(url);
    const pending = new Set<string>();
    let timer: number | undefined;
    const flush = () => {
      timer = undefined;
      for (const key of pending) void qc.invalidateQueries({ queryKey: [key] });
      pending.clear();
    };
    const schedule = (keys: string[]) => {
      keys.forEach((k) => pending.add(k));
      if (timer === undefined) timer = window.setTimeout(flush, FLUSH_MS);
    };
    // Kết nối SSE đứt rồi mở lại: có thể đã lỡ sự kiện, làm mới mọi thứ.
    let errored = false;
    source.onerror = () => { errored = true; };
    source.addEventListener('open', () => {
      if (!errored) return;
      errored = false;
      schedule(INVALIDATE.resync);
    });
    for (const [type, keys] of Object.entries(INVALIDATE)) {
      source.addEventListener(type, () => schedule(keys));
    }
    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [bookId, qc]);
}
