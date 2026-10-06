import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { ChapterDetail, ReviewFix, Segment } from '../api/types';
import { ToastProvider } from '../components/Toast';
import ChapterPage from './ChapterPage';

afterEach(() => vi.restoreAllMocks());

const cfg = { engine: 'ct2', model_id: 'm', beam: 2, batch: { auto: true, size: 16 }, chunk_mode: 'sentence', han_normalize: 'auto',
  honorific: { kinship: true, pronoun: true, modern_stable: true }, deepseek: {}, review: {} };
const seg = (idx: number, dst: string, edited = false): Segment => ({ idx, is_meta: false, src: '他', dst, dst_machine: 'm', edited, flags: [] });
const detail = (segments: Segment[], status = 'translated'): ChapterDetail => ({
  chapter: { id: 'c1', book_id: 'b1', no: 1, title_zh: '第1章', title_vi: 'Chương 1', status, char_count: 10, model_id: 'M',
    last_run: null, has_manual_edits: false, error: null, translated_at: null, reviewed_at: null, updated_at: 'x', run_config_override: null, run_config: cfg },
  segments, prev_no: null, next_no: null, job: null, source_missing: false,
} as unknown as ChapterDetail);

function setup(segments: Segment[], status = 'translated') {
  const calls: { method: string; url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body as string | undefined });
    if (method === 'PATCH' && url.includes('/segments/')) {
      const body = JSON.parse(init!.body as string);
      const idx = Number(url.split('/').pop());
      const s = body.revert ? seg(idx, 'máy') : seg(idx, body.dst, true);
      return new Response(JSON.stringify({ segment: s, chapter: { id: 'c1', status: 'translated' } }));
    }
    if (method !== 'GET') return new Response(JSON.stringify({}));
    if (url.includes('/chapters/by-no/')) return new Response(JSON.stringify(detail(segments, status)));
    if (url.includes('/revisions')) return new Response('[]');
    return new Response(JSON.stringify({ id: 'b1', title_zh: 'T', title_vi: 'T' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={['/books/b1/chapters/1']}><ToastProvider>
      <Routes><Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} /></Routes>
    </ToastProvider></MemoryRouter></QueryClientProvider>,
  );
  return calls;
}

it('Ctrl+Enter gửi bản sửa đang chờ trước khi đánh dấu đã soát', async () => {
  const calls = setup([seg(1, 'Hắn nói.')]);
  const cell = await screen.findByLabelText('Bản dịch câu 1');
  cell.textContent = 'Hắn bảo.';
  fireEvent.input(cell);
  fireEvent.keyDown(window, { key: 'Enter', ctrlKey: true });
  await waitFor(() => expect(calls.some((c) => c.url.includes('mark-reviewed'))).toBe(true));
  const patch = calls.findIndex((c) => c.method === 'PATCH' && c.url.endsWith('/segments/c1/1'));
  const mark = calls.findIndex((c) => c.url.includes('mark-reviewed'));
  expect(patch).toBeGreaterThanOrEqual(0);
  expect(patch).toBeLessThan(mark);
});

it('nút Đánh dấu đã soát cũng gửi bản sửa đang chờ trước', async () => {
  const calls = setup([seg(1, 'Hắn nói.')]);
  const cell = await screen.findByLabelText('Bản dịch câu 1');
  cell.textContent = 'Hắn bảo.';
  fireEvent.input(cell);
  fireEvent.click(screen.getByRole('button', { name: '✓ Đánh dấu đã soát' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('mark-reviewed'))).toBe(true));
  const patch = calls.findIndex((c) => c.method === 'PATCH');
  expect(patch).toBeGreaterThanOrEqual(0);
  expect(patch).toBeLessThan(calls.findIndex((c) => c.url.includes('mark-reviewed')));
});

async function addTermFromSelection() {
  const src = await screen.findByText('他');
  const node = src.closest('.src') ?? src;
  vi.spyOn(window, 'getSelection').mockReturnValue({
    toString: () => '他', anchorNode: node.firstChild ?? node,
    getRangeAt: () => ({ getBoundingClientRect: () => ({ left: 0, bottom: 0 }) }),
  } as unknown as Selection);
  fireEvent.mouseUp(node);
  fireEvent.click(await screen.findByRole('button', { name: '＋ Thêm vào glossary' }));
  fireEvent.change(await screen.findByLabelText('Đích Việt'), { target: { value: 'Hắn' } });
  fireEvent.click(screen.getByRole('button', { name: 'Lưu thuật ngữ' }));
}

it('thêm term khi chương đã dịch thì hỏi dịch lại', async () => {
  setup([seg(1, 'Hắn nói.')]);
  await addTermFromSelection();
  expect(await screen.findByText('Dịch lại chương này để áp thuật ngữ mới?')).toBeInTheDocument();
});

it('thêm term khi chương đang trong hàng đợi thì chỉ báo đã thêm, không hỏi dịch lại', async () => {
  setup([seg(1, 'Hắn nói.')], 'queued');
  await addTermFromSelection();
  expect(await screen.findByText('Đã thêm thuật ngữ')).toBeInTheDocument();
  expect(screen.queryByText('Dịch lại chương này để áp thuật ngữ mới?')).not.toBeInTheDocument();
});

it('chương HachimiMT có 3 đề xuất: 3 câu viền tím, áp 1 thì còn 2 (AC-6.10, BR-6.5a, BR-6.5b)', async () => {
  const mk = (i: number): ReviewFix => ({ id: `f${i}`, chapter_id: 'c1', segment_idx: i, type: 'mistranslation', before: `Câu ${i}.`,
    after: `Câu ${i} sửa.`, reason: null, confidence: 80, status: 'pending', model_id: 'deepseek-flash', created_at: 'x', decided_at: null });
  let fixes = [mk(1), mk(2), mk(3)];
  const calls: { method: string; url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body as string | undefined });
    if (url.includes('/review-fixes/apply')) {
      const ids: string[] = JSON.parse(init!.body as string).ids;
      fixes = fixes.filter((f) => !ids.includes(f.id));
      return new Response(JSON.stringify({ applied: ids.length, stale: 0, skipped: 0 }));
    }
    if (url.includes('/review-fixes')) return new Response(JSON.stringify({ items: fixes }));
    if (url.includes('/chapters/by-no/')) return new Response(JSON.stringify(detail([seg(1, 'Câu 1.'), seg(2, 'Câu 2.'), seg(3, 'Câu 3.')])));
    if (url.includes('/revisions')) return new Response('[]');
    if (url.includes('/notes')) return new Response(JSON.stringify({ items: [] }));
    return new Response(JSON.stringify({ id: 'b1', title_zh: 'T', title_vi: 'T' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={['/books/b1/chapters/1']}><ToastProvider>
      <Routes><Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} /></Routes>
    </ToastProvider></MemoryRouter></QueryClientProvider>,
  );
  expect(await screen.findByText('Đề xuất sửa của DeepSeek (3)')).toBeInTheDocument();
  expect(document.querySelectorAll('.cmp-row.fix')).toHaveLength(3);
  expect(screen.getByRole('button', { name: '✓ Đánh dấu đã soát' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '✦ Soát bằng DeepSeek' })).toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('button', { name: 'Áp' })[0]);
  await waitFor(() => expect(JSON.parse(calls.find((c) => c.url.includes('/review-fixes/apply'))!.body!)).toEqual({ ids: ['f1'] }));
  expect(await screen.findByText('Đề xuất sửa của DeepSeek (2)')).toBeInTheDocument();
});

it('menu Dịch lại cho chọn DeepSeek (BR-6.5a)', async () => {
  const calls = setup([seg(1, 'Hắn nói.')]);
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.click(screen.getByRole('button', { name: 'Chọn model dịch lại' }));
  fireEvent.click(screen.getByRole('button', { name: 'Bằng DeepSeek' }));
  await waitFor(() => {
    const t = calls.find((c) => c.url.endsWith('/chapters/c1/translate'));
    expect(JSON.parse(t!.body!)).toEqual({ priority: true, engine: 'deepseek' });
  });
});

it('chương đang dịch lại: các nút Áp / Bỏ trên đề xuất bị khoá', async () => {
  const fix: ReviewFix = { id: 'f1', chapter_id: 'c1', segment_idx: 1, type: 'mistranslation', before: 'Câu 1.', after: 'Câu 1 sửa.',
    reason: null, confidence: 80, status: 'pending', model_id: 'deepseek-flash', created_at: 'x', decided_at: null };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/review-fixes')) return new Response(JSON.stringify({ items: [fix] }));
    if (url.includes('/chapters/by-no/')) return new Response(JSON.stringify(detail([seg(1, 'Câu 1.')], 'translating')));
    if (url.includes('/revisions') || url.includes('/notes')) return new Response(url.includes('/notes') ? '{"items":[]}' : '[]');
    return new Response(JSON.stringify({ id: 'b1', title_zh: 'T', title_vi: 'T' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={['/books/b1/chapters/1']}><ToastProvider>
      <Routes><Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} /></Routes>
    </ToastProvider></MemoryRouter></QueryClientProvider>,
  );
  await screen.findByText('Đề xuất sửa của DeepSeek (1)');
  for (const b of screen.getAllByRole('button', { name: 'Áp' })) expect(b).toBeDisabled();
  for (const b of screen.getAllByRole('button', { name: 'Bỏ' })) expect(b).toBeDisabled();
});
