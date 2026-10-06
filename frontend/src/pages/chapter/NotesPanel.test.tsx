import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import NotesPanel from './NotesPanel';

afterEach(() => vi.restoreAllMocks());

it('thêm ghi chú correction và đánh dấu đã xử lý (US-8.7, spec 06 mục 4)', async () => {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const note = { id: 'n1', chapter_id: 'c1', type: 'correction', content: 'Tên 高俅 là Cao Cầu', resolved: false, created_at: 'x', updated_at: 'x' };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const method = init?.method ?? 'GET';
    calls.push({ method, url: String(input), body: init?.body ? JSON.parse(init.body as string) : undefined });
    return new Response(JSON.stringify(method === 'GET' ? { items: [note] } : note), { status: method === 'POST' ? 201 : 200 });
  });
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider><NotesPanel chapterId="c1" /></ToastProvider></QueryClientProvider>);
  expect(await screen.findByText('Tên 高俅 là Cao Cầu')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '＋ Thêm ghi chú' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Ghi chú sửa lỗi mới'), { target: { value: '  Tên 林凡 là Lâm Phàm ' } });
  fireEvent.click(screen.getByRole('button', { name: '＋ Thêm ghi chú' }));
  await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toMatchObject({
    url: '/api/v1/chapters/c1/notes', body: { type: 'correction', content: 'Tên 林凡 là Lâm Phàm' },
  }));
  fireEvent.click(screen.getByRole('button', { name: 'Đã xử lý' }));
  await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ url: '/api/v1/notes/n1', body: { resolved: true } }));
});
