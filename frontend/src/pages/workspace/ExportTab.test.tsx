import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import type { BookDetail } from '../../api/types';
import { ToastProvider } from '../../components/Toast';
import ExportTab from './ExportTab';

afterEach(() => vi.restoreAllMocks());

const book = {
  id: 'b1', title_zh: '书', title_vi: 'Sách', source_dir: '/data/books/sach',
  stats: { total: 7, todo: 1, queued: 0, translating: 0, translated: 1, needs_review: 1, reviewed: 4, error: 0 },
} as unknown as BookDetail;

function setup(previewBody = { chapters: 4, skipped: 3 }) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes('/exports/preview')) return new Response(JSON.stringify(previewBody));
    if (method === 'POST') return new Response(JSON.stringify({ export_id: 'e1' }), { status: 202 });
    if (url.endsWith('/exports/e1')) {
      return new Response(JSON.stringify({
        id: 'e1', status: 'done', chapters: 4, skipped: 3, file_name: 'sach_da-soat_20261004-0905.epub',
        download_url: '/api/v1/exports/e1/download', error: null,
      }));
    }
    return new Response('{}');
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ExportTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return { calls, click };
}

it('báo "Sẽ xuất N chương, bỏ qua M", xuất theo tuỳ chọn rồi tự tải file (BR-3.15, BR-3.16)', async () => {
  const { calls, click } = setup();
  expect(await screen.findByText('Sẽ xuất 4 chương, bỏ qua 3')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Chỉ chương đã soát' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('scope=reviewed'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: '.epub' }));
  fireEvent.click(screen.getByLabelText('Chèn tiêu đề chương tiếng Việt'));
  fireEvent.click(screen.getByLabelText(/Giữ dòng meta/));
  const go = screen.getByRole('button', { name: '⤓ Xuất file' });
  await waitFor(() => expect(go).toBeEnabled());
  fireEvent.click(go);
  expect(await screen.findByText(/Đã xuất 4 chương/)).toBeInTheDocument();
  expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ scope: 'reviewed', format: 'epub', include_titles: false, keep_meta: true });
  expect(click).toHaveBeenCalledTimes(1);
});

it('khoảng chương: sai thì khoá nút, đúng thì gửi from_no / to_no', async () => {
  const { calls } = setup();
  await screen.findByText('Sẽ xuất 4 chương, bỏ qua 3');
  fireEvent.click(screen.getByRole('button', { name: 'Khoảng chương' }));
  fireEvent.change(screen.getByLabelText('Từ chương'), { target: { value: '10' } });
  fireEvent.change(screen.getByLabelText('Đến chương'), { target: { value: '5' } });
  expect(screen.getByText('Khoảng chương không hợp lệ.')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '⤓ Xuất file' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Đến chương'), { target: { value: '20' } });
  await waitFor(() => expect(calls.some((c) => c.url.includes('scope=range') && c.url.includes('from_no=10') && c.url.includes('to_no=20'))).toBe(true));
});

it('không có chương nào để xuất thì khoá nút', async () => {
  setup({ chapters: 0, skipped: 5 });
  expect(await screen.findByText('Sẽ xuất 0 chương, bỏ qua 5')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '⤓ Xuất file' })).toBeDisabled();
});

it('lỗi khi hỏi trạng thái lần xuất: dừng hỏi lại, hiện banner lỗi, nút không kẹt', async () => {
  let polls = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.includes('/exports/preview')) return new Response(JSON.stringify({ chapters: 4, skipped: 3 }));
    if ((init?.method ?? 'GET') === 'POST') return new Response(JSON.stringify({ export_id: 'e1' }), { status: 202 });
    if (url.endsWith('/exports/e1')) {
      polls += 1;
      return new Response(JSON.stringify({ error: { code: 'EXPORT_NOT_FOUND', message: 'Không tìm thấy lần xuất này' } }), { status: 404 });
    }
    return new Response('{}');
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ExportTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  await screen.findByText('Sẽ xuất 4 chương, bỏ qua 3');
  const go = screen.getByRole('button', { name: '⤓ Xuất file' });
  await waitFor(() => expect(go).toBeEnabled());
  fireEvent.click(go);
  expect(await screen.findByText(/Không theo dõi được lần xuất/)).toBeInTheDocument();
  await waitFor(() => expect(go).toBeEnabled());
  await new Promise((r) => setTimeout(r, 1300));
  expect(polls).toBe(1);
});

it('lỗi xem trước thì ẩn dòng "Sẽ xuất N chương"', async () => {
  vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response('{}', { status: 500 }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><ExportTab book={book} /></ToastProvider></MemoryRouter></QueryClientProvider>);
  await waitFor(() => expect(qc.isFetching()).toBe(0));
  expect(screen.queryByText(/Sẽ xuất/)).toBeNull();
});
