import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import CostPanel from './CostPanel';

afterEach(() => vi.restoreAllMocks());

const usage = {
  month: '2026-10',
  items: [{ model: 'deepseek-v4-pro', source: 'translate', requests: 50, tokens_in: 25000, tokens_in_cached: 9000, tokens_out: 20000, cost_usd: 0.05 }],
  total: { requests: 50, tokens_in: 25000, tokens_in_cached: 9000, tokens_out: 20000, cost_usd: 0.05 },
  accuracy: [{ model: 'deepseek-v4-pro', requests: 50, err_in_pct: 20, err_out_pct: 22.5,
    coefficients: { han_per_token: 0.9, out_ratio: 1, samples: 50, calibrated: true } }],
};
const models = { items: [{ id: 'deepseek-v4-pro', provider: 'deepseek', label: 'DeepSeek V4 Pro', context_window: 131072, max_output_tokens: 8192,
  price_in_per_mtok: 0.27, price_in_cached_per_mtok: 0.07, price_out_per_mtok: 1.1, prices_are_samples: true, enabled: true }] };

it('hiện chi phí tháng, bảng ước tính và thực tế, sửa giá (BR-8.24c, BR-8.25, BR-8.27)', async () => {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ method: init?.method ?? 'GET', url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes('/usage')) return new Response(JSON.stringify(usage));
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ ...models.items[0], price_out_per_mtok: 0.5, prices_are_samples: false }));
    return new Response(JSON.stringify(models));
  });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ToastProvider><CostPanel bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  expect((await screen.findAllByText('$0.05')).length).toBe(2); // tổng tháng + dòng theo model
  expect(screen.getByText('20%')).toBeInTheDocument();
  expect(screen.getByText('22,5%')).toBeInTheDocument();
  expect(screen.getByText('giá mẫu')).toBeInTheDocument();
  const out = screen.getByLabelText('Giá ra deepseek-v4-pro');
  fireEvent.change(out, { target: { value: '0.5' } });
  fireEvent.blur(out);
  await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({
    url: '/api/v1/ai-models/deepseek-v4-pro', body: { price_out_per_mtok: 0.5 },
  }));
});
