import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ChapterDetail, ReviewFix } from '../../api/types';
import { ToastProvider } from '../../components/Toast';
import ChapterSide from './ChapterSide';

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

const cfg = { engine: 'ct2', model_id: 'm', beam: 2, batch: { auto: true, size: 16 }, chunk_mode: 'sentence', han_normalize: 'auto',
  honorific: { kinship: true, pronoun: true, modern_stable: true }, deepseek: {}, review: {} };
const detail = { chapter: { id: 'c1', run_config_override: { beam: 2, chunk_mode: 'sentence' }, run_config: cfg } } as unknown as ChapterDetail;

function setup(gate?: () => Promise<void>) {
  const puts: unknown[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_u, init) => {
    if (init?.method === 'PUT') { puts.push(JSON.parse(init.body as string)); await gate?.(); }
    return new Response(init?.method === 'PUT' ? '{}' : '[]');
  });
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider><ChapterSide detail={detail} busy={false} /></ToastProvider></QueryClientProvider>);
  return puts;
}

it('đổi chunk rồi beam nhanh: chỉ một PUT với cả hai giá trị', async () => {
  const puts = setup();
  fireEvent.click(screen.getByRole('button', { name: 'Theo đoạn' }));
  fireEvent.change(screen.getByLabelText('Beam'), { target: { value: '3' } });
  expect(screen.getByLabelText('Beam')).toHaveValue('3');
  await act(async () => { await vi.advanceTimersByTimeAsync(400); });
  expect(puts).toEqual([{ beam: 3, chunk_mode: 'paragraph' }]);
});

it('đang có PUT bay thì xếp hàng giá trị mới nhất, gửi sau khi xong', async () => {
  let release!: () => void;
  const puts = setup(() => new Promise<void>((r) => { release = r; }));
  fireEvent.change(screen.getByLabelText('Beam'), { target: { value: '3' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(400); });
  fireEvent.change(screen.getByLabelText('Beam'), { target: { value: '4' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(400); });
  expect(puts).toEqual([{ beam: 3, chunk_mode: 'sentence' }]);
  await act(async () => { release(); await vi.advanceTimersByTimeAsync(0); });
  expect(puts).toEqual([{ beam: 3, chunk_mode: 'sentence' }, { beam: 4, chunk_mode: 'sentence' }]);
});

it('hiện route tự nhận và ép "hiện đại" thì PUT /register (AC-7.10)', async () => {
  const puts: { url: string; body: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (u, init) => {
    if (init?.method === 'PUT') puts.push({ url: String(u), body: JSON.parse(init.body as string) });
    return new Response(init?.method === 'PUT' ? '{}' : '[]');
  });
  const d = { chapter: { ...detail.chapter, register_route: 'ancient', register_score: 0.62, register_override: null } } as unknown as ChapterDetail;
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider><ChapterSide detail={d} busy={false} /></ToastProvider></QueryClientProvider>);
  expect(screen.getByText(/Cổ trang/)).toBeInTheDocument();
  expect(screen.getByText(/0,62/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Ép văn phong'), { target: { value: 'modern' } });
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  expect(puts.filter((p) => p.url.endsWith('/chapters/c1/register')).map((p) => p.body)).toEqual([{ route: 'modern' }]);
});

it('liệt kê đề xuất, áp / bỏ tất cả, bấm để cuộn tới câu (spec 06 mục 4)', () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('[]'));
  const mk = (id: string, idx: number) => ({ id, chapter_id: 'c1', segment_idx: idx, type: 'mistranslation', before: 'a', after: 'b',
    reason: 'sai nghĩa', confidence: 80, status: 'pending', model_id: 'deepseek-flash', created_at: 'x', decided_at: null }) as ReviewFix;
  const onDecide = vi.fn();
  const onJump = vi.fn();
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider>
    <ChapterSide detail={detail} busy={false} fixes={[mk('f1', 4), mk('f2', 9)]} onDecide={onDecide} onJump={onJump} />
  </ToastProvider></QueryClientProvider>);
  expect(screen.getByText('Đề xuất sửa của DeepSeek (2)')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Áp tất cả' }));
  expect(onDecide).toHaveBeenCalledWith(['f1', 'f2'], 'apply');
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ tất cả' }));
  expect(onDecide).toHaveBeenCalledWith(['f1', 'f2'], 'reject');
  fireEvent.click(screen.getByRole('button', { name: /Câu 9/ }));
  expect(onJump).toHaveBeenCalledWith(9);
});
