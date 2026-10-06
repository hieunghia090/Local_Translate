import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { LogEntry } from '../../api/types';
import { ToastProvider } from '../../components/Toast';
import LogsTab from './LogsTab';

afterEach(() => vi.restoreAllMocks());

const log = (id: string): LogEntry => ({
  id, ts: '2026-10-04T00:00:00Z', book_id: 'b1', chapter_id: null, chapter_no: null, job_id: null, level: 'info', source: 'system',
  provider: null, model: null, message: `msg ${id}`, tokens_in: null, tokens_out: null, tokens_in_cached: null, latency_ms: null, params: {},
});

it('tải thêm log cũ hơn không tự cuộn xuống đáy', async () => {
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(500);
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const older = String(input).includes('before=');
    return new Response(JSON.stringify(older ? { items: [log('1')], next_cursor: null } : { items: [log('3'), log('2')], next_cursor: 'c' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><LogsTab bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  await screen.findByText('msg 3');
  const box = container.querySelector('.log') as HTMLDivElement;
  expect(box.scrollTop).toBe(500);
  box.scrollTop = 40;
  fireEvent.click(screen.getByRole('button', { name: 'Tải thêm log cũ hơn' }));
  await screen.findByText('msg 1');
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Tải thêm log cũ hơn' })).toBeNull());
  expect(box.scrollTop).toBe(40);
});
