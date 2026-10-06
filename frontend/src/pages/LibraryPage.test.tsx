import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import LibraryPage from './LibraryPage';

afterEach(() => vi.restoreAllMocks());

function renderPage(responses: Record<string, unknown>) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    const key = Object.keys(responses).find((k) => url.includes(k));
    return new Response(JSON.stringify(key ? responses[key] : {}), { status: 200 });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter><LibraryPage /></MemoryRouter></QueryClientProvider>);
}

it('thư viện trống hiện hướng dẫn', async () => {
  // AC-1.7
  renderPage({ '/library/stats': { books: 0, chapters_total: 0, chapters_done: 0, chapters_left: 0 }, '/books': { items: [] } });
  expect(await screen.findByText(/Chưa có truyện nào/)).toBeInTheDocument();
});

it('hiện thẻ truyện và ô tạo mới', async () => {
  // AC-1.1
  const stats = { total: 707, todo: 600, queued: 0, translating: 1, translated: 90, needs_review: 12, reviewed: 4, error: 0 };
  renderPage({
    '/library/stats': { books: 1, chapters_total: 707, chapters_done: 106, chapters_left: 601 },
    '/books': { items: [{ id: 'b1', slug: 's', title_zh: '大宋有种', title_vi: 'Đại Tống Hữu Chủng', author: null, genre: 'modern_war',
      cover_url: null, stats, progress_pct: 15, state: 'in_progress', last_opened_at: null, created_at: '2026-10-03T00:00:00Z' }] },
  });
  expect(await screen.findByText('Đại Tống Hữu Chủng')).toBeInTheDocument();
  expect(screen.getByText('106/707 chương · 15%')).toBeInTheDocument();
  expect(screen.getByText('Tạo truyện mới')).toBeInTheDocument();
  expect(screen.getByText('707')).toBeInTheDocument();
});
