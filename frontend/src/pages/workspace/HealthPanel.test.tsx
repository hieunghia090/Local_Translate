import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import HealthPanel from './HealthPanel';

afterEach(() => vi.restoreAllMocks());

it('liệt kê đích Việt còn chữ Hán và âm nghi ngờ, sửa âm thì ghi manual (BR-4.14, BR-4.20)', async () => {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const health = {
    han_in_dst: [{ id: 't2', src_zh: '高俅', dst_vi: '高俅 Cầu', category: 'character', name_lang: null, notes: null, aliases: [],
      enabled: true, predictable: false, always_send: false, prompt_note: false, miss_count: 0, occurrence_count: 3,
      origin: 'ai', created_at: '', updated_at: '' }],
    suspicious_readings: [{ char: '楷', readings: ['giai'], proposed: 'khải', terms: [{ id: 't1', src_zh: '赵楷', dst_vi: 'Triệu Khải' }] }],
  };
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    calls.push({ method: init?.method ?? 'GET', url: String(input), body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (init?.method === 'PUT') return new Response(JSON.stringify({ char: '楷', readings: [] }));
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ term: health.han_in_dst[0], affected_chapter_ids: [] }));
    return new Response(JSON.stringify(health));
  });
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ToastProvider><HealthPanel bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  expect(calls).toEqual([]);  // chỉ tải khi bấm
  fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra glossary' }));
  expect(await screen.findByText('Đích Việt còn chữ Hán (1)')).toBeInTheDocument();
  expect(screen.getByText(/赵楷 = Triệu Khải/)).toBeInTheDocument();
  const dst = screen.getByLabelText('Sửa đích Việt của 高俅');
  fireEvent.change(dst, { target: { value: 'Cao Cầu' } });
  fireEvent.blur(dst);
  await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')).toMatchObject({ url: '/api/v1/glossary/t2', body: { dst_vi: 'Cao Cầu' } }));
  fireEvent.change(screen.getByLabelText('Âm Hán Việt của 楷'), { target: { value: 'khải, giai' } });
  fireEvent.click(screen.getByRole('button', { name: 'Lưu âm 楷' }));
  await waitFor(() => expect(calls.find((c) => c.method === 'PUT')).toMatchObject({
    url: `/api/v1/hanviet/${encodeURIComponent('楷')}`, body: { readings: ['khải', 'giai'] },
  }));
});
