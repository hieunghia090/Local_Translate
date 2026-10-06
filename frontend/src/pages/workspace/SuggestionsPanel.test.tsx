import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../components/Toast';
import SuggestionsPanel from './SuggestionsPanel';

afterEach(() => vi.restoreAllMocks());

const sug = (id: string, src: string, dst: string, confidence: number) => ({
  id, src_zh: src, dst_vi: dst, category: 'character', name_lang: 'zh', notes: null, context: null, confidence,
  occurrence_count: 10, provider: 'deepseek', model: 'deepseek-v4-pro', status: 'pending', selected: confidence >= 80, created_at: '',
});
const job = (status: string) => ({ id: 'j1', status, progress: 50, error: status === 'failed' ? 'HTTP 503' : null, auto: false,
  chapters: 20, created_at: '', finished_at: null });

function setup(lists: object[]) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  let n = 0;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ method, url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    if (url.includes('/extract/estimate')) {
      return new Response(JSON.stringify({ model_id: 'deepseek-v4-pro', chapters: 20, batches: 2, tokens_in: 9000,
        tokens_in_cached: 500, tokens_out: 2000, cost_usd: 0.0123, prices_are_samples: true }));
    }
    if (url.endsWith('/glossary/extract')) return new Response(JSON.stringify({ job_id: 'j2', chapters: 20 }), { status: 202 });
    if (url.endsWith('/accept')) return new Response(JSON.stringify({ added: 1, existing: 0, skipped: 0, term_ids: ['t1'] }));
    if (url.endsWith('/reject')) return new Response(JSON.stringify({ rejected: 2 }));
    return new Response(JSON.stringify(lists[Math.min(n++, lists.length - 1)]));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><SuggestionsPanel bookId="b1" /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return calls;
}

it('chấp nhận chỉ các mục đã chọn, kèm bản sửa (AC-4.5, AC-4.7 phần giao diện)', async () => {
  const calls = setup([{ items: [sug('s1', '赵楷', 'Triệu Khải', 95), sug('s2', '苏清雪', 'Tô Thanh Tuyết', 70)], last_job: job('done') }]);
  expect(await screen.findByText(/Đề xuất từ AI · deepseek-v4-pro \(2\)/)).toBeInTheDocument();
  const input = screen.getByLabelText('Đề xuất Việt cho 赵楷');
  fireEvent.change(input, { target: { value: 'Triệu Khải Đế' } });
  fireEvent.blur(input);
  fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận các mục đã chọn' }));
  // chọn mặc định (tin cậy ≥ 80): bản sửa đi theo items, phần còn lại chọn theo bộ lọc phía server
  await waitFor(() => expect(calls.find((c) => c.url.endsWith('/accept'))?.body).toEqual({
    items: [{ id: 's1', dst_vi: 'Triệu Khải Đế' }], filter: { min_confidence: 80, exclude_ids: ['s1'] },
  }));
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ hết' }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith('/reject'))?.body).toEqual({ filter: {} }));
});

it('hộp trích xuất: cảnh báo, ước tính, gửi đúng phạm vi và loại', async () => {
  const calls = setup([{ items: [], last_job: null }]);
  fireEvent.click(await screen.findByRole('button', { name: '✦ Trích xuất bằng AI' }));
  expect(screen.getByText('Nội dung các chương được chọn sẽ gửi tới DeepSeek.')).toBeInTheDocument();
  expect(await screen.findByText(/20 chương · 2 lô · ~9\.000 token vào · ~2\.000 token ra · ~\$0\.01 \(giá mẫu\)/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Khoảng chương' }));
  fireEvent.change(screen.getByLabelText('Từ chương'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Đến chương'), { target: { value: '9' } });
  fireEvent.click(screen.getByLabelText('Vật phẩm / Công pháp'));
  fireEvent.click(screen.getByRole('button', { name: 'Bắt đầu trích xuất' }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith('/glossary/extract'))?.body).toEqual({
    provider: 'deepseek', model: 'deepseek-v4-pro', scope: { mode: 'range', from: 3, to: 9 },
    categories: ['character', 'organization', 'realm', 'location', 'item'],
  }));
});

it('job trích vừa lỗi thì báo kèm link Console logs (BR-4.10)', async () => {
  setup([{ items: [], last_job: job('running') }, { items: [], last_job: job('failed') }]);
  expect(await screen.findByText(/Đang trích xuất… 50%/)).toBeInTheDocument();
  expect(await screen.findByText(/Trích xuất bằng AI lỗi: HTTP 503/, undefined, { timeout: 3000 })).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Xem Console logs' })).toHaveAttribute('href', '/books/b1/logs');
});

const many = (n: number, from = 0) => Array.from({ length: n }, (_, i) => sug(`s${from + i}`, `词${from + i}`, `Từ ${from + i}`, 90));

it('phân trang phía server: 200 mục mỗi trang, trang sau gọi offset 200; chọn tay gửi đúng id đã chọn', async () => {
  const calls = setup([
    { items: many(200), total: 450, limit: 200, offset: 0, last_job: job('done') },
    { items: many(200, 200), total: 450, limit: 200, offset: 200, last_job: job('done') },
  ]);
  expect(await screen.findByText(/Đề xuất từ AI · deepseek-v4-pro \(450\)/)).toBeInTheDocument();
  expect(calls.find((c) => c.url.includes('/suggestions?'))!.url).toContain('limit=200&offset=0');
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  await waitFor(() => expect(calls.some((c) => c.url.includes('offset=200'))).toBe(true));
  expect(await screen.findByLabelText('Chọn đề xuất 词200')).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Chọn tất cả đề xuất')); // bỏ chọn trang 2 → chuyển sang chọn tay
  fireEvent.click(screen.getByLabelText('Chọn đề xuất 词201'));
  fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận các mục đã chọn' }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith('/accept'))).toBeTruthy());
  const body = calls.find((c) => c.url.endsWith('/accept'))!.body as { items: { id: string }[]; filter?: unknown };
  expect(body.filter).toBeUndefined();
  expect(body.items.map((i) => i.id)).toEqual(['s201']);
});

it('Bỏ hết dùng bộ lọc phía server, không gửi danh sách id (vượt 2000 vẫn chạy)', async () => {
  const calls = setup([{ items: many(200), total: 5000, limit: 200, offset: 0, last_job: job('done') }]);
  fireEvent.click(await screen.findByRole('button', { name: 'Bỏ hết' }));
  await waitFor(() => expect(calls.find((c) => c.url.endsWith('/reject'))?.body).toEqual({ filter: {} }));
});
it('job đang tạm dừng (paused) thì không polling nữa', async () => {
  const calls = setup([{ items: [], total: 0, limit: 200, offset: 0, last_job: job('paused') }]);
  expect(await screen.findByText(/tạm dừng/)).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 1600));
  expect(calls.filter((c) => c.url.includes('/suggestions?'))).toHaveLength(1);
});

it('job đang chạy thì vẫn polling mỗi giây', async () => {
  const calls = setup([{ items: [], total: 0, limit: 200, offset: 0, last_job: job('running') }]);
  expect(await screen.findByText(/Đang trích xuất/)).toBeInTheDocument();
  await waitFor(() => expect(calls.filter((c) => c.url.includes('/suggestions?')).length).toBeGreaterThan(1), { timeout: 2500 });
});
