import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { Segment } from '../api/types';
import { ToastProvider } from '../components/Toast';
import ChapterPage from './ChapterPage';

afterEach(() => vi.restoreAllMocks());

const cfg = { engine: 'ct2', model_id: 'm', beam: 2, batch: { auto: true, size: 16 }, chunk_mode: 'sentence', han_normalize: 'auto',
  honorific: { kinship: true, pronoun: true, modern_stable: true }, deepseek: {}, review: {} };
const seg = (idx: number, flags: string[] = []): Segment =>
  ({ idx, is_meta: false, src: `句${idx}`, dst: `Câu ${idx}.`, dst_machine: `Câu ${idx}.`, edited: false, flags });

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function setup({ no, prev, next, segments }: { no: number; prev: number | null; next: number | null; segments: Segment[] }) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/chapters/by-no/')) {
      return new Response(JSON.stringify({
        chapter: { id: `c${no}`, book_id: 'b1', no, title_zh: `第${no}章`, title_vi: `Chương ${no}`, status: 'translated',
          char_count: 10, model_id: 'HachimiMT-60', last_run: null, has_manual_edits: false, error: null,
          translated_at: null, reviewed_at: null, updated_at: 'x', run_config_override: null, run_config: cfg },
        segments, prev_no: prev, next_no: next, job: null, source_missing: false,
      }));
    }
    if (url.includes('/revisions')) return new Response('[]');
    return new Response(JSON.stringify({ id: 'b1', title_zh: 'T', title_vi: 'T' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={[`/books/b1/chapters/${no}`]}><ToastProvider>
      <Routes><Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} /></Routes>
      <Where />
    </ToastProvider></MemoryRouter></QueryClientProvider>,
  );
}

it('AC-6.8: ở chương cuối, nút "Chương sau" bị khoá và Alt+→ không làm gì', async () => {
  setup({ no: 3, prev: 2, next: null, segments: [seg(1)] });
  await screen.findByLabelText('Bản dịch câu 1');
  expect(screen.getByRole('button', { name: 'Chương sau →' })).toBeDisabled();
  expect(fireEvent.keyDown(window, { key: 'ArrowRight', altKey: true })).toBe(true); // không preventDefault
  expect(screen.getByTestId('where')).toHaveTextContent('/books/b1/chapters/3');
  expect(screen.getByText(/Alt\+←\/→ chương trước\/sau/)).toBeInTheDocument();
});

it('Alt+← sang chương trước (BR-6.9)', async () => {
  setup({ no: 3, prev: 2, next: null, segments: [seg(1)] });
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.keyDown(window, { key: 'ArrowLeft', altKey: true });
  await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent('/books/b1/chapters/2'));
});

it('Alt+↓ / Alt+↑ chuyển giữa các ô dịch; chưa chọn ô nào thì Alt+↑ vào câu cuối', async () => {
  setup({ no: 1, prev: null, next: 2, segments: [seg(1), seg(2), seg(3)] });
  const c1 = await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true });
  expect(document.activeElement).toBe(c1);
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true });
  expect(document.activeElement).toBe(screen.getByLabelText('Bản dịch câu 2'));
  fireEvent.keyDown(window, { key: 'ArrowUp', altKey: true });
  expect(document.activeElement).toBe(c1);
  c1.blur();
  fireEvent.keyDown(window, { key: 'ArrowUp', altKey: true });
  expect(document.activeElement).toBe(screen.getByLabelText('Bản dịch câu 3'));
});

it('AC-6.7: bật "Chỉ hiện câu có cờ" thì chỉ còn câu có flags', async () => {
  setup({ no: 1, prev: null, next: null, segments: [seg(1, ['placeholder_lost']), seg(2), seg(3, ['retried'])] });
  await screen.findByLabelText('Bản dịch câu 2');
  fireEvent.click(screen.getByLabelText('Chỉ hiện câu có cờ'));
  expect(screen.queryByLabelText('Bản dịch câu 2')).toBeNull();
  expect(screen.getByLabelText('Bản dịch câu 1')).toBeInTheDocument();
  expect(screen.getByLabelText('Bản dịch câu 3')).toBeInTheDocument();
});

it('Alt+↑/↓ không nhảy vào ô dịch khi đang gõ ở ô tiêu đề', async () => {
  setup({ no: 1, prev: null, next: null, segments: [seg(1), seg(2)] });
  await screen.findByLabelText('Bản dịch câu 1');
  const title = screen.getByLabelText('Tiêu đề tiếng Việt');
  title.focus();
  fireEvent.keyDown(title, { key: 'ArrowUp', altKey: true, bubbles: true });
  fireEvent.keyDown(title, { key: 'ArrowDown', altKey: true, bubbles: true });
  expect(document.activeElement).toBe(title);
});
