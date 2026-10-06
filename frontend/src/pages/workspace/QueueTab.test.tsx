import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import QueueTab from './QueueTab';

afterEach(() => vi.restoreAllMocks());

it('nút tạm dừng / tiếp tục riêng cho pool DeepSeek (BR-3.8)', async () => {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const view = { paused: { ct2: false, deepseek: true }, paused_reason: { ct2: null, deepseek: 'token_anomaly' }, active: [], recent: [],
    running_elsewhere: null, eta_seconds: null };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    calls.push({ method: init?.method ?? 'GET', url: String(input), body: init?.body ? JSON.parse(init.body as string) : undefined });
    return new Response(JSON.stringify(view));
  });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ToastProvider><QueueTab bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText(/Pool DeepSeek đang tạm dừng \(token ra bất thường\)/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '⏸ Tạm dừng worker CPU' })).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '▶ Tiếp tục pool DeepSeek' }));
  await waitFor(() => expect(calls.find((c) => c.method === 'POST')).toMatchObject({ url: '/api/v1/queue/resume', body: { engine: 'deepseek' } }));
});
