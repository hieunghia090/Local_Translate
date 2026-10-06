import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { BookDetail, ChapterRow } from '../../api/types';
import { ToastProvider } from '../../components/Toast';
import ChaptersTab from './ChaptersTab';

afterEach(() => vi.restoreAllMocks());

const row = (no: number, status: ChapterRow['status']): ChapterRow => ({
  id: `c${no}`, book_id: 'b1', no, title_zh: `第${no}章`, title_vi: `Chương ${no}`, status, char_count: 3000,
  model_id: status === 'translated' ? 'HachimiMT-60' : null, last_run: null, has_manual_edits: false, error: null,
  translated_at: null, reviewed_at: null, updated_at: '2026-10-04T00:00:00Z',
});

const book = { id: 'b1', run_config: { model_id: 'HachimiMT-60' }, stats: { total: 5, todo: 3, queued: 0, translating: 0, translated: 2, needs_review: 0, reviewed: 0, error: 0 } } as unknown as BookDetail;

it('dịch 5 chương đã chọn thì báo bỏ qua chương đã dịch', async () => {
  // AC-3.3
  const calls: { url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, body: init?.body as string | undefined });
    if (url.includes('/chapters/bulk')) return new Response(JSON.stringify({ affected: 3, skipped: 2, job_ids: [] }));
    return new Response(JSON.stringify({ items: [row(1, 'todo'), row(2, 'translated'), row(3, 'todo'), row(4, 'translated'), row(5, 'todo')], next_cursor: null, total: 5 }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ChaptersTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  await screen.findByLabelText('Chọn chương 1');
  for (const n of [1, 2, 3, 4, 5]) fireEvent.click(screen.getByLabelText(`Chọn chương ${n}`));
  fireEvent.click(screen.getByRole('button', { name: '▶ Dịch' }));
  expect(await screen.findByText(/Bỏ qua 2 chương đã dịch/)).toBeInTheDocument();
  await waitFor(() => {
    const bulk = calls.find((c) => c.url.includes('/chapters/bulk'));
    expect(JSON.parse(bulk!.body!)).toMatchObject({ action: 'translate', chapter_ids: ['c1', 'c2', 'c3', 'c4', 'c5'] });
  });
});

const estimate = {
  action: 'translate', model_id: 'deepseek-v4-pro', chapters: 12, skipped: 0, tokens_in: 48000, tokens_in_cached: 9000,
  tokens_out: 40000, cost_usd: 0.0612, prices_are_samples: true,
  coefficients: { han_per_token: 1.8, out_ratio: 3.3, samples: 0, calibrated: false },
};

function setupMany(n: number) {
  const calls: { url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, body: init?.body as string | undefined });
    if (url.includes('/estimate')) return new Response(JSON.stringify(estimate));
    if (url.includes('/chapters/bulk')) return new Response(JSON.stringify({ affected: n, skipped: 0, job_ids: [] }));
    const rows = Array.from({ length: n }, (_, i) => row(i + 1, 'translated'));
    return new Response(JSON.stringify({ items: rows, next_cursor: null, total: n }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ChaptersTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return calls;
}

it('dịch bằng DeepSeek từ 10 chương hiện ước tính chi phí trước khi tạo job (AC-8.8, BR-3.2b)', async () => {
  const calls = setupMany(12);
  await screen.findByLabelText('Chọn chương 1');
  fireEvent.click(screen.getByLabelText('Chọn tất cả chương đang hiện'));
  fireEvent.click(screen.getByRole('button', { name: '▶ Dịch bằng DeepSeek' }));
  expect(await screen.findByText(/12 chương · ~48\.000 token vào · ~40\.000 token ra · ~\$0\.06 \(giả định trúng cache prompt nền\)/)).toBeInTheDocument();
  expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(false);
  expect(JSON.parse(calls.find((c) => c.url.includes('/estimate'))!.body!)).toMatchObject({ action: 'translate' });
  fireEvent.click(screen.getByRole('button', { name: 'Thêm vào hàng đợi' }));
  await waitFor(() => {
    const bulk = calls.find((c) => c.url.includes('/chapters/bulk'));
    expect(JSON.parse(bulk!.body!)).toMatchObject({ action: 'translate', engine: 'deepseek' });
    expect(JSON.parse(bulk!.body!).chapter_ids).toHaveLength(12);
  });
});

it('soát bằng DeepSeek dưới 10 chương thì gửi ngay, không ước tính (BR-3.2a)', async () => {
  const calls = setupMany(2);
  await screen.findByLabelText('Chọn chương 1');
  fireEvent.click(screen.getByLabelText('Chọn chương 1'));
  fireEvent.click(screen.getByLabelText('Chọn chương 2'));
  fireEvent.click(screen.getByRole('button', { name: '✦ Soát bằng DeepSeek' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(true));
  expect(JSON.parse(calls.find((c) => c.url.includes('/chapters/bulk'))!.body!)).toEqual({ action: 'review', chapter_ids: ['c1', 'c2'] });
  expect(calls.some((c) => c.url.includes('/estimate'))).toBe(false);
  expect(await screen.findByText('Đã thêm 2 chương vào hàng đợi soát DeepSeek')).toBeInTheDocument();
});

function setupBook(n: number, engine: 'ct2' | 'deepseek', status: ChapterRow['status'] = 'translated') {
  const calls: { url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, body: init?.body as string | undefined });
    if (url.includes('/estimate')) return new Response(JSON.stringify(estimate));
    if (url.includes('/chapters/bulk')) return new Response(JSON.stringify({ affected: n, skipped: 0, job_ids: [] }));
    return new Response(JSON.stringify({ items: Array.from({ length: n }, (_, i) => row(i + 1, status)), next_cursor: null, total: n }));
  });
  const b = { ...book, run_config: { model_id: 'deepseek-v4-pro', engine } } as unknown as BookDetail;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ChaptersTab book={b} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return calls;
}

it.each([['▶ Dịch', 'todo'], ['↻ Dịch lại', 'translated']] as const)(
  'truyện engine DeepSeek: %s từ 10 chương cũng hỏi ước tính chi phí (BR-8.26)', async (label, status) => {
    const calls = setupBook(12, 'deepseek', status);
    await screen.findByLabelText('Chọn chương 1');
    fireEvent.click(screen.getByLabelText('Chọn tất cả chương đang hiện'));
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(await screen.findByText(/12 chương · ~48\.000 token vào/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Thêm vào hàng đợi' }));
    await waitFor(() => expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(true));
    expect(JSON.parse(calls.find((c) => c.url.includes('/chapters/bulk'))!.body!).action).toBe(label === '▶ Dịch' ? 'translate' : 'retranslate');
  });

it('truyện engine DeepSeek nhưng dưới 10 chương thì gửi ngay', async () => {
  const calls = setupBook(12, 'deepseek');
  await screen.findByLabelText('Chọn chương 1');
  for (let n = 1; n <= 9; n++) fireEvent.click(screen.getByLabelText(`Chọn chương ${n}`));
  fireEvent.click(screen.getByRole('button', { name: '↻ Dịch lại' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(true));
  expect(calls.some((c) => c.url.includes('/estimate'))).toBe(false);
});

it('truyện engine HachimiMT: dịch lại 12 chương không cần ước tính (chỉ DeepSeek tốn tiền)', async () => {
  const calls = setupBook(12, 'ct2');
  await screen.findByLabelText('Chọn chương 1');
  fireEvent.click(screen.getByLabelText('Chọn tất cả chương đang hiện'));
  fireEvent.click(screen.getByRole('button', { name: '↻ Dịch lại' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('/chapters/bulk'))).toBe(true));
  expect(calls.some((c) => c.url.includes('/estimate'))).toBe(false);
});

it('AC-6.13: chương có cả hai bản hiện badge số câu khác nhau', async () => {
  const both = { ...row(1, 'translated'), compare: { mt: 85, ai: 85, both: 85, differ: 12 } };
  const onlyMt = { ...row(2, 'translated'), compare: { mt: 85, ai: 0, both: 0, differ: 0 } };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response(JSON.stringify({ items: [both, onlyMt], next_cursor: null, total: 2 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ChaptersTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByTitle('Có bản Hachimi và AI · 12/85 câu khác nhau')).toHaveTextContent('≠ 12');
  expect(screen.getAllByTitle(/Có bản Hachimi và AI/)).toHaveLength(1);
});
