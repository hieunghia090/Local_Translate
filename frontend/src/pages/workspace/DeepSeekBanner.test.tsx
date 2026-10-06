import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import DeepSeekBanner from './DeepSeekBanner';

afterEach(() => vi.restoreAllMocks());

function setup(status: object) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    calls.push({ method: init?.method ?? 'GET', url: String(input), body: init?.body ? JSON.parse(init.body as string) : undefined });
    return new Response(JSON.stringify(init?.method === 'POST' ? { engine: 'deepseek', paused: false } : status));
  });
  const { container } = render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ToastProvider><DeepSeekBanner bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return { calls, container };
}

it('banner đỏ khi DeepSeek từ chối key, bấm tiếp tục pool (AC-8.7)', async () => {
  const { calls } = setup({ key_present: true, key_masked: '••••abcd', paused: true, paused_reason: 'auth',
    message: 'DeepSeek từ chối key / hết số dư. Kiểm tra trong Cài đặt.' });
  expect(await screen.findByRole('alert')).toHaveTextContent('DeepSeek từ chối key / hết số dư. Kiểm tra trong Cài đặt.');
  fireEvent.click(screen.getByRole('button', { name: '▶ Tiếp tục pool DeepSeek' }));
  await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toMatchObject({ url: '/api/v1/queue/resume', body: { engine: 'deepseek' } }));
});

it('tạm dừng tay (không có lý do) thì không hiện banner', async () => {
  const { calls } = setup({ key_present: true, key_masked: '••••abcd', paused: true, paused_reason: null, message: 'Pool DeepSeek đang tạm dừng.' });
  await waitFor(() => expect(calls.length).toBeGreaterThan(0));
  expect(screen.queryByRole('alert')).toBeNull();
});
