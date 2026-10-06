import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import GlossaryTab from './GlossaryTab';

afterEach(() => vi.restoreAllMocks());

const term = {
  id: 't1', src_zh: '赵楷', dst_vi: 'Triệu Khải', category: 'character', name_lang: null, notes: null, aliases: [],
  enabled: true, predictable: false, always_send: false, prompt_note: false, miss_count: 0, occurrence_count: 41, origin: 'manual',
  created_at: '', updated_at: '',
};

it('sửa đích Việt có chương bị ảnh hưởng thì gợi ý dịch lại', async () => {
  // AC-4.8 (giao diện)
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ term: { ...term, dst_vi: 'Triệu Giai' }, affected_chapter_ids: Array.from({ length: 12 }, (_, i) => `c${i}`) }));
    if (url.includes('/glossary')) return new Response(JSON.stringify({ items: [term] }));
    return new Response(JSON.stringify({ items: [] }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><GlossaryTab bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  const input = await screen.findByLabelText('Đích Việt của 赵楷');
  fireEvent.change(input, { target: { value: 'Triệu Giai' } });
  fireEvent.blur(input);
  expect(await screen.findByText(/12 chương đã dịch dùng tên cũ/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Dịch lại 12 chương' })).toBeInTheDocument();
});

it('thêm term rỗng thì báo lỗi, không gọi API', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [] })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><GlossaryTab bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  fireEvent.click(await screen.findByRole('button', { name: '＋ Thêm' }));
  expect(screen.getByText('Cần nhập nguồn Trung và đích Việt')).toBeInTheDocument();
  expect(f.mock.calls.every(([, init]) => !init || (init as RequestInit).method !== 'POST')).toBe(true);
});

function renderTab() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><GlossaryTab bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
}

it('nhiều lần sửa gộp (hợp) các chương bị ảnh hưởng thay vì ghi đè', async () => {
  const two = { ...term, id: 't2', src_zh: '东京', dst_vi: 'Đông Kinh' };
  const sets = [['c1', 'c2'], ['c2', 'c3']];
  let n = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    if (init?.method === 'PATCH') return new Response(JSON.stringify({ term, affected_chapter_ids: sets[n++] }));
    if (String(input).includes('/glossary')) return new Response(JSON.stringify({ items: [term, two] }));
    return new Response(JSON.stringify({ items: [] }));
  });
  renderTab();
  for (const [label, value] of [['Đích Việt của 赵楷', 'A'], ['Đích Việt của 东京', 'B']]) {
    const input = await screen.findByLabelText(label);
    fireEvent.change(input, { target: { value } });
    fireEvent.blur(input);
  }
  expect(await screen.findByRole('button', { name: 'Dịch lại 3 chương' })).toBeInTheDocument();
});

it('xoá trống đích Việt rồi blur thì trả ô về giá trị đã lưu', async () => {
  const f = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) =>
    new Response(JSON.stringify(String(input).includes('/glossary') ? { items: [term] } : { items: [] })));
  renderTab();
  const input = (await screen.findByLabelText('Đích Việt của 赵楷')) as HTMLInputElement;
  fireEvent.change(input, { target: { value: '  ' } });
  fireEvent.blur(input);
  expect(input.value).toBe('Triệu Khải');
  expect(f.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')).toBe(false);
});

function mockList(items: object[], summary?: object) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (method === 'PATCH') return new Response(JSON.stringify({ term, affected_chapter_ids: [] }));
    if (url.includes('/glossary/suggestions')) return new Response(JSON.stringify({ items: [], last_job: null }));
    if (url.includes('/glossary')) return new Response(JSON.stringify({ items, summary }));
    return new Response(JSON.stringify({ items: [] }));
  });
  return calls;
}

it('HV, Luôn gửi, Gửi ghi chú, bộ lọc và dòng tổng (AC-4.14, BR-4.16)', async () => {
  const calls = mockList([{ ...term, predictable: true, miss_count: 0 }, { ...term, id: 't2', src_zh: '高俅', dst_vi: 'Cao Cầu', miss_count: 1 }],
    { total: 2, will_send: 1 });
  renderTab();
  expect(await screen.findByText('2 term · 1 term sẽ gửi khi gặp (không tự đoán được hoặc luôn gửi)')).toBeInTheDocument();
  expect(screen.getAllByText('HV')).toHaveLength(1);
  expect(screen.getByTitle('Tự đoán được: trùng âm Hán Việt, không gửi cho DeepSeek')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Luôn gửi 高俅'));
  fireEvent.click(screen.getByLabelText('Gửi ghi chú 赵楷'));
  await waitFor(() => {
    expect(calls.filter((c) => c.method === 'PATCH').map((c) => [c.url, c.body])).toEqual([
      ['/api/v1/glossary/t2', { always_send: true }], ['/api/v1/glossary/t1', { prompt_note: true }],
    ]);
  });
  fireEvent.change(screen.getByLabelText('Lọc gửi cho DeepSeek'), { target: { value: 'predictable' } });
  await waitFor(() => expect(calls.some((c) => c.url.includes('send=predictable'))).toBe(true));
});
