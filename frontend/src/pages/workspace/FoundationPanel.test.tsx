import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import FoundationPanel from './FoundationPanel';

afterEach(() => vi.restoreAllMocks());

function setup() {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const f = { foundation_prompt: 'Mẫu gốc', is_default: true, honorific_block: '# XƯNG HÔ\n- ta / ngươi' };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes('/preview')) {
      return new Response(JSON.stringify({ chapter_no: 3, model_id: 'deepseek-v4-pro', system: 'SYSTEM', user: '# VĂN BẢN CẦN DỊCH\n⟦0⟧ 第3章',
        glossary: { matched: 5, sent: 4, truncated: 1, skipped_predictable: 0, tokens_est: 20 }, tokens_in_est: 1234 }));
    }
    if (method === 'PUT') return new Response(JSON.stringify({ ...f, foundation_prompt: (JSON.parse(init!.body as string)).foundation_prompt, is_default: false }));
    return new Response(JSON.stringify(f));
  });
  render(<QueryClientProvider client={new QueryClient()}><ToastProvider><FoundationPanel bookId="b1" /></ToastProvider></QueryClientProvider>);
  return calls;
}

it('nạp prompt nền mà không lưu lại, sửa thì tự lưu (US-8.2, BR-3.13)', async () => {
  const calls = setup();
  const box = await screen.findByDisplayValue('Mẫu gốc');
  expect(screen.getByText(/# XƯNG HÔ/)).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 600));
  expect(calls.some((c) => c.method === 'PUT')).toBe(false);
  fireEvent.change(box, { target: { value: 'Giọng văn hài hước.' } });
  await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ foundation_prompt: 'Giọng văn hài hước.' }), { timeout: 2000 });
});

it('khôi phục mẫu và xem prompt đầy đủ cho một chương', async () => {
  const calls = setup();
  await screen.findByDisplayValue('Mẫu gốc');
  fireEvent.click(screen.getByRole('button', { name: 'Khôi phục mẫu' }));
  await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url.endsWith('/books/b1/foundation/reset'))).toBe(true));
  fireEvent.change(screen.getByLabelText('Xem prompt đầy đủ cho chương #'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Xem' }));
  expect(await screen.findByLabelText('User prompt')).toHaveTextContent('⟦0⟧ 第3章');
  expect(screen.getByText(/Glossary gửi: 4\/5 term \(cắt 1\)/)).toBeInTheDocument();
  expect(calls.some((c) => c.url.endsWith('/books/b1/foundation/preview?chapter_no=3'))).toBe(true);
});
