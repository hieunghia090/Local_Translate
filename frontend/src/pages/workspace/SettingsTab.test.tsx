import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { StrictMode } from 'react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { BookDetail } from '../../api/types';
import { ToastProvider } from '../../components/Toast';
import SettingsTab from './SettingsTab';

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const cfg = { engine: 'ct2', model_id: 'm', beam: 2, batch: { auto: true, size: 16 }, chunk_mode: 'sentence', han_normalize: 'auto',
  honorific: { kinship: true, pronoun: true, modern_stable: true }, deepseek: {}, review: {} };
const book = { id: 'b1', title_zh: '书', title_vi: 'Sách', author: 'A', genre: 'xianxia', note: null, run_config: cfg, source_dir: '/x', updated_at: 't1' } as unknown as BookDetail;

function setup(b = book, strict = false) {
  const calls: { method: string; body: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
    calls.push({ method: init?.method ?? 'GET', body: init?.body ? JSON.parse(init.body as string) : undefined });
    return new Response('{}');
  });
  const qc = new QueryClient();
  const ui = (bk: BookDetail) => {
    const tree = <QueryClientProvider client={qc}><MemoryRouter><ToastProvider><SettingsTab book={bk} /></ToastProvider></MemoryRouter></QueryClientProvider>;
    return strict ? <StrictMode>{tree}</StrictMode> : tree;
  };
  const r = render(ui(b));
  return { calls, ...r, ui };
}

it('đổi beam rồi rời tab trong 500 ms vẫn lưu, chỉ gửi trường đã đổi', () => {
  const { calls, unmount } = setup();
  fireEvent.change(screen.getByLabelText('Beam'), { target: { value: '4' } });
  unmount();
  const patches = calls.filter((c) => c.method === 'PATCH');
  expect(patches).toHaveLength(1);
  expect(patches[0].body).toEqual({ run_config: { beam: 4 } });
});

it('StrictMode: không lưu gì khi chưa đổi', () => {
  const { calls, unmount } = setup(book, true);
  act(() => { vi.advanceTimersByTime(2000); });
  unmount();
  expect(calls.filter((c) => c.method === 'PATCH')).toHaveLength(0);
});

it('chỉ gửi khóa lồng nhau đã đổi', () => {
  const { calls } = setup();
  fireEvent.click(screen.getByLabelText(/Đại từ/));
  act(() => { vi.advanceTimersByTime(500); });
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ run_config: { honorific: { pronoun: false } } });
});

it('nạp lại nháp khi book.updated_at đổi và không có sửa chưa lưu', () => {
  const { rerender, ui } = setup();
  rerender(ui({ ...book, updated_at: 't2', title_vi: 'Sách mới' }));
  expect(screen.getByLabelText('Tên tiếng Việt')).toHaveValue('Sách mới');
});

it('đổi xưng hô xong hiện banner áp lại, bấm thì POST reapply', async () => {
  const bk = { ...book, stats: { total: 10, todo: 3, queued: 0, translating: 0, translated: 4, needs_review: 2, reviewed: 1, error: 0 } } as unknown as BookDetail;
  const { calls } = setup(bk);
  fireEvent.click(screen.getByLabelText(/Đại từ/));
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(screen.getByText('Áp lại cho 7 chương đã dịch?')).toBeInTheDocument();
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Áp lại' })); await vi.advanceTimersByTimeAsync(0); });
  const post = calls.find((c) => c.method === 'POST');
  expect(post).toBeDefined();
});

const stats = (n: number) => ({ total: 10, todo: 10 - n, queued: 0, translating: 0, translated: n, needs_review: 0, reviewed: 0, error: 0 });

it('không có chương nào đã dịch thì không hiện banner áp lại', async () => {
  setup({ ...book, stats: stats(0) } as unknown as BookDetail);
  fireEvent.click(screen.getByLabelText(/Đại từ/));
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(screen.queryByText(/Áp lại cho/)).toBeNull();
});

it('đổi thể loại (ảnh hưởng tự chọn route) cũng hiện banner áp lại', async () => {
  const { calls } = setup({ ...book, stats: stats(5) } as unknown as BookDetail);
  fireEvent.change(screen.getByLabelText('Thể loại'), { target: { value: 'urban' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ genre: 'urban' });
  expect(screen.getByText('Áp lại cho 5 chương đã dịch?')).toBeInTheDocument();
});

it('chọn DeepSeek làm model mặc định thì gửi engine và model_id', () => {
  const { calls } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'DeepSeek API' }));
  fireEvent.change(screen.getByLabelText('Số chương song song'), { target: { value: '5' } });
  act(() => { vi.advanceTimersByTime(500); });
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({
    run_config: { engine: 'deepseek', model_id: 'deepseek-v4-pro', deepseek: { concurrency: 5 } },
  });
});

it('bật tự soát sau HachimiMT chỉ gửi khối review', () => {
  const { calls } = setup();
  fireEvent.click(screen.getByLabelText(/Tự soát sau khi HachimiMT dịch xong/));
  act(() => { vi.advanceTimersByTime(500); });
  expect(calls.find((c) => c.method === 'PATCH')!.body).toEqual({ run_config: { review: { auto_after_ct2: true } } });
});
