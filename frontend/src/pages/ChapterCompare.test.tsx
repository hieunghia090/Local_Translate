import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ChapterDetail, Segment } from '../api/types';
import { ToastProvider } from '../components/Toast';
import { READER_VIEW_KEY } from '../lib/readerView';
import ChapterPage from './ChapterPage';

beforeEach(() => localStorage.clear());
afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

const cfg = { engine: 'ct2', model_id: 'm', beam: 2, batch: { auto: true, size: 16 }, chunk_mode: 'sentence', han_normalize: 'auto',
  honorific: { kinship: true, pronoun: true, modern_stable: true }, deepseek: {}, review: {} };
const s = (idx: number, mt: string | null, ai: string | null, dst: string | null = ai ?? mt): Segment => ({
  idx, is_meta: false, src: `句${idx}`, dst, dst_machine: ai ?? mt, edited: false, flags: [], dst_mt: mt, dst_ai: ai,
  mt_ai_differ: mt === null || ai === null ? null : mt !== ai,
});
const SEGMENTS = [
  s(1, 'Khi Triệu Khải chuẩn bị rời', 'Lúc Triệu Khải sắp rời'),
  s(2, 'Hắn đi.', 'Hắn đi.'),
  s(3, 'Nàng cười.', 'Cô ấy cười.'),
  s(4, 'Chỉ có Hachimi.', null),
];
const detail = (status: string): ChapterDetail => ({
  chapter: { id: 'c1', book_id: 'b1', no: 1, title_zh: '第1章', title_vi: 'Chương 1', status, char_count: 10,
    model_id: 'deepseek-v4-pro', last_run: null, has_manual_edits: false, error: null, translated_at: null, reviewed_at: null,
    updated_at: 'x', run_config_override: null, run_config: cfg, compare: { mt: 4, ai: 3, both: 3, differ: 2 } },
  segments: SEGMENTS, prev_no: null, next_no: null, job: null, source_missing: false,
} as unknown as ChapterDetail);

function setup(status = 'translated') {
  const calls: { method: string; url: string; body?: string }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body as string | undefined });
    if (method === 'PATCH' && url.includes('/segments/')) {
      const idx = Number(url.split('/').pop());
      const body = JSON.parse(init!.body as string);
      const old = SEGMENTS.find((x) => x.idx === idx)!;
      return new Response(JSON.stringify({ segment: { ...old, dst: body.dst, edited: body.dst !== old.dst_machine },
        chapter: { id: 'c1', status: 'translated' } }));
    }
    if (method !== 'GET') return new Response(JSON.stringify({}));
    if (url.includes('/chapters/by-no/')) return new Response(JSON.stringify(detail(status)));
    if (url.includes('/revisions')) return new Response('[]');
    return new Response(JSON.stringify({ id: 'b1', title_zh: 'T', title_vi: 'T' }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}><MemoryRouter initialEntries={['/books/b1/chapters/1']}><ToastProvider>
      <Routes><Route path="/books/:bookId/chapters/:no" element={<ChapterPage />} /></Routes>
    </ToastProvider></MemoryRouter></QueryClientProvider>,
  );
  return { calls, container: view.container };
}
const rows = (container: HTMLElement) => [...container.querySelectorAll('.cmp-row[data-row]')].map((r) => r.getAttribute('data-row'));
const viewButton = (name: RegExp) => within(screen.getByRole('group', { name: 'Chế độ xem' })).getByRole('button', { name });

it('mặc định là Bản chính và vẫn sửa được', async () => {
  setup();
  const cell = await screen.findByLabelText('Bản dịch câu 1');
  expect(cell).toHaveAttribute('contenteditable', 'true');
  expect(viewButton(/^Bản chính$/)).toHaveAttribute('aria-pressed', 'true');
  expect(viewButton(/^Hachimi \(4\)$/)).toBeInTheDocument();
  expect(viewButton(/^AI \(3\)$/)).toBeInTheDocument();
});

it('AC-6.17: chế độ AI chỉ đọc, câu thiếu bản hiện mờ, lựa chọn được nhớ', async () => {
  const { container } = setup();
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.click(viewButton(/^AI/));
  expect(screen.getByLabelText('Bản AI câu 4')).toHaveTextContent('chưa có bản dịch AI');
  expect(container.querySelector('[contenteditable="true"]')).toBeNull();
  expect(localStorage.getItem(READER_VIEW_KEY)).toBe('ai');
});

it('mở trang với lựa chọn đã nhớ', async () => {
  localStorage.setItem(READER_VIEW_KEY, 'compare');
  setup();
  expect(await screen.findByText('2/3 câu khác nhau')).toBeInTheDocument();
});

it('localStorage lỗi thì về Bản chính', async () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  setup();
  expect(await screen.findByLabelText('Bản dịch câu 1')).toHaveAttribute('contenteditable', 'true');
});

it('AC-6.14, AC-6.15: tô, lọc câu khác nhau, nhảy câu bằng nút và Alt+Shift', async () => {
  const { container } = setup();
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.click(viewButton(/^So sánh$/));
  const mt1 = screen.getByRole('group', { name: 'Hachimi câu 1' });
  expect([...mt1.querySelectorAll('.d-mt')].map((e) => e.textContent)).toEqual(['Khi', 'chuẩn bị']);
  expect(rows(container)).toEqual(['1', '2', '3', '4']);
  fireEvent.click(screen.getByLabelText('Chỉ câu khác nhau'));
  expect(rows(container)).toEqual(['1', '3']);
  fireEvent.click(screen.getByRole('button', { name: '↓ Câu khác sau' }));
  expect(container.querySelector('[data-row="1"]')).toHaveClass('current');
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true, shiftKey: true });
  expect(container.querySelector('[data-row="3"]')).toHaveClass('current');
  fireEvent.click(screen.getByRole('button', { name: '↑ Câu khác trước' }));
  expect(container.querySelector('[data-row="1"]')).toHaveClass('current');
});

it('Review Focus 6: Alt+Shift+↓ ở Bản chính không chuyển focus vào câu', async () => {
  setup();
  const cell = await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true, shiftKey: true });
  expect(document.activeElement).not.toBe(cell);
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true });
  expect(document.activeElement).toBe(cell);
});

it('AC-6.16: Dùng bản này gửi PATCH dst; bên trùng bản chính hiện ✓ Đang dùng', async () => {
  const { calls } = setup();
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.click(viewButton(/^So sánh$/));
  expect(within(screen.getByRole('group', { name: 'AI câu 2' })).getByText('✓ Đang dùng')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dùng bản Hachimi cho câu 3' }));
  await waitFor(() => {
    const patch = calls.find((c) => c.method === 'PATCH' && c.url.endsWith('/segments/c1/3'));
    expect(JSON.parse(patch!.body!)).toEqual({ dst: 'Nàng cười.' });
  });
  expect(await within(screen.getByRole('group', { name: 'Hachimi câu 3' })).findByText('✓ Đang dùng')).toBeInTheDocument();
});

it('BR-6.14: chương bận thì khoá Dùng bản này', async () => {
  setup('queued');
  fireEvent.click(await screen.findByRole('button', { name: /^So sánh$/ }));
  expect(screen.getByRole('button', { name: 'Dùng bản Hachimi cho câu 1' })).toBeDisabled();
});

it('AC-6.18, Review Focus 4: chuyển chế độ khi đang sửa dở vẫn lưu bản sửa', async () => {
  const { calls } = setup();
  const cell = await screen.findByLabelText('Bản dịch câu 1');
  cell.textContent = 'Tôi sửa dở.';
  fireEvent.input(cell);
  fireEvent.click(viewButton(/^So sánh$/));
  await waitFor(() => {
    const patch = calls.find((c) => c.method === 'PATCH' && c.url.endsWith('/segments/c1/1'));
    expect(JSON.parse(patch!.body!)).toEqual({ dst: 'Tôi sửa dở.' });
  });
});

it('Dùng bản này chờ lần lưu đang bay của cùng câu, để bản sửa dở không đè bản đã chọn', async () => {
  const { calls } = setup();
  const cell = await screen.findByLabelText('Bản dịch câu 3');
  const real = vi.mocked(globalThis.fetch).getMockImplementation()!;
  let release: () => void = () => {};
  const gate = new Promise<void>((r) => { release = r; });
  let first = true;
  vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
    if ((init?.method ?? 'GET') === 'PATCH' && first) {
      first = false;
      calls.push({ method: 'PATCH', url: String(input), body: init?.body as string });
      await gate;
      return new Response(JSON.stringify({ segment: SEGMENTS[2], chapter: { id: 'c1', status: 'translated' } }));
    }
    return real(input, init);
  });
  cell.textContent = 'Tôi sửa dở.';
  fireEvent.input(cell);
  fireEvent.click(viewButton(/^So sánh$/)); // unmount gửi bản sửa dở, đang bay
  await waitFor(() => expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1));
  fireEvent.click(screen.getByRole('button', { name: 'Dùng bản Hachimi cho câu 3' }));
  await new Promise((r) => setTimeout(r, 30));
  expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(1); // chưa gửi bản đã chọn
  release();
  await waitFor(() => {
    const patches = calls.filter((c) => c.method === 'PATCH').map((c) => JSON.parse(c.body!).dst);
    expect(patches).toEqual(['Tôi sửa dở.', 'Nàng cười.']);
  });
});

it('Alt+Shift+↓ khi đang gõ ở ô nhập không nhảy câu khác nhau', async () => {
  const { container } = setup();
  await screen.findByLabelText('Bản dịch câu 1');
  fireEvent.click(viewButton(/^So sánh$/));
  const title = screen.getByLabelText('Tiêu đề tiếng Việt');
  title.focus();
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true, shiftKey: true });
  expect(container.querySelector('.cmp-row.current')).toBeNull();
  (title as HTMLElement).blur();
  fireEvent.keyDown(window, { key: 'ArrowDown', altKey: true, shiftKey: true });
  expect(container.querySelector('[data-row="1"]')).toHaveClass('current');
});
