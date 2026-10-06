import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from '../components/Toast';
import CreateBookPage from './CreateBookPage';

afterEach(() => vi.restoreAllMocks());

function renderPage() {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
    new Response(JSON.stringify({ honorific: { kinship: false, pronoun: false, modern_stable: true } }), { status: 200 }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><CreateBookPage /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return fetchMock;
}

it('bỏ trống tên gốc thì không gửi request và focus ô tên gốc', async () => {
  // AC-2.7
  const fetchMock = renderPage();
  fireEvent.click(screen.getByRole('button', { name: 'Trống' }));
  fireEvent.click(screen.getByRole('button', { name: 'Tạo workspace' }));
  expect(await screen.findByText('Cần nhập tên gốc của truyện')).toBeInTheDocument();
  expect(screen.getByLabelText('Tên gốc (Trung) *')).toHaveFocus();
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/books'))).toBe(false);
});

it('slug cập nhật theo tên', () => {
  renderPage();
  fireEvent.change(screen.getByLabelText('Tên tiếng Việt'), { target: { value: 'Đại Tống Hữu Chủng' } });
  expect(screen.getByText('~/LocalTranslate/books/dai-tong-huu-chung/')).toBeInTheDocument();
});

it('chọn DeepSeek thì gửi engine và model DeepSeek khi tạo truyện', async () => {
  const fetchMock = renderPage();
  fireEvent.change(screen.getByLabelText('Tên gốc (Trung) *'), { target: { value: '书' } });
  fireEvent.click(screen.getByRole('button', { name: 'Trống' }));
  fireEvent.click(screen.getByRole('button', { name: 'DeepSeek API' }));
  expect(screen.getByText(/Nội dung chương sẽ được gửi tới DeepSeek API/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Model DeepSeek'), { target: { value: 'deepseek-flash' } });
  fireEvent.click(screen.getByRole('button', { name: 'Tạo workspace' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')).toBe(true));
  const [, init] = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')!;
  expect(JSON.parse(String(init!.body)).run_config).toMatchObject({
    engine: 'deepseek', model_id: 'deepseek-flash', deepseek: { model_id: 'deepseek-flash', concurrency: 3 },
  });
});

it('chưa có key: khối AI hướng dẫn thêm key, vẫn tạo được và gửi ai_extract (AC-2.10)', async () => {
  const fetchMock = renderPage();
  expect(await screen.findByText(/Chưa có key: thêm DEEPSEEK_API_KEY vào file \.env/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Tên gốc (Trung) *'), { target: { value: '书' } });
  fireEvent.click(screen.getByRole('button', { name: 'Trống' }));
  fireEvent.click(screen.getByRole('button', { name: 'Tạo workspace' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')).toBe(true));
  const [, init] = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')!;
  expect(JSON.parse(String(init!.body)).ai_extract).toEqual({
    enabled: true, provider: 'deepseek', model: 'deepseek-v4-pro', chapters: 20,
    categories: ['character', 'organization', 'realm', 'location'], accepted_preview_ids: [], preview_edits: {},
  });
});

function renderWithAi(routes: Record<string, unknown>) {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const key = Object.keys(routes).find((k) => url.includes(k) && (!k.startsWith('POST ') || init?.method === 'POST'));
    if (url.endsWith('/imports') && init?.method === 'POST') return new Response(JSON.stringify({ import_id: 'imp1', status: 'parsing' }), { status: 202 });
    if (url.endsWith('/books') && init?.method === 'POST') return new Response(JSON.stringify({ id: 'b1', slug: 'sach' }), { status: 201 });
    return new Response(JSON.stringify(key ? routes[key] : { honorific: { kinship: false, pronoun: false, modern_stable: true } }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><ToastProvider><CreateBookPage /></ToastProvider></MemoryRouter></QueryClientProvider>);
  return fetchMock;
}

const STATUS = { key_present: true, key_masked: '••••abcd', paused: false, paused_reason: null, message: null };

it('Thử kết nối với key sai báo lỗi 401 rõ ràng (AC-4.6)', async () => {
  renderWithAi({
    '/deepseek/status': STATUS,
    '/deepseek/test': { ok: false, model: 'deepseek-v4-pro', status: 401, message: 'DeepSeek từ chối key / hết số dư (HTTP 401): Authentication Fails' },
  });
  const test = await screen.findByRole('button', { name: 'Thử kết nối' });
  await waitFor(() => expect(test).toBeEnabled());
  fireEvent.click(test);
  expect(await screen.findByText(/Lỗi 401: DeepSeek từ chối key/)).toBeInTheDocument();
});

it('Chạy thử rồi tạo: gửi id đã chọn và bản sửa', async () => {
  const view = { import_id: 'imp1', status: 'ready', mode: 'multi', split_rule: 'auto', split_regex: null, encoding: 'auto',
    suggested_title_zh: '书', total_chars: 9000, file_errors: [], error: null,
    chapters: [{ key: 'c1', no: 1, title_zh: '第1章', title_vi: 'Chương 1', title_vi_edited: false, chars: 3000, selected: true, warnings: [] }] };
  const preview = { model: 'deepseek-v4-pro', chapters: 1, batches: 1, dropped: {}, tokens_in: 4000, tokens_out: 300, cost_usd: 0.0012,
    items: [
      { id: 'p1', src_zh: '赵楷', dst_vi: 'Triệu Khải', category: 'character', name_lang: 'zh', notes: null, context: null, confidence: 95, occurrence_count: 8, selected: true },
      { id: 'p2', src_zh: '苏清雪', dst_vi: 'Tô Thanh Tuyết', category: 'character', name_lang: 'zh', notes: null, context: null, confidence: 70, occurrence_count: 3, selected: false },
    ] };
  const fetchMock = renderWithAi({ '/deepseek/status': STATUS, '/glossary-preview': preview, '/imports/imp1': view });
  const file = Object.assign(new File(['第1章'], '0001.txt'), { webkitRelativePath: '' }); // jsdom không có thuộc tính này
  fireEvent.drop(screen.getByText(/Kéo thả file/), { dataTransfer: { files: [file] } });
  const run = await screen.findByRole('button', { name: 'Chạy thử' });
  await waitFor(() => expect(run).toBeEnabled());
  fireEvent.click(run);
  const input = await screen.findByLabelText('Đề xuất Việt cho 赵楷');
  expect(screen.getByLabelText('Chọn đề xuất 苏清雪')).not.toBeChecked();
  fireEvent.change(input, { target: { value: 'Triệu Khải Đế' } });
  fireEvent.blur(input);
  fireEvent.click(screen.getByRole('button', { name: 'Tạo workspace' }));
  await waitFor(() => expect(fetchMock.mock.calls.some(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')).toBe(true));
  const [, init] = fetchMock.mock.calls.find(([u, i]) => String(u).endsWith('/books') && i?.method === 'POST')!;
  expect(JSON.parse(String(init!.body)).ai_extract).toMatchObject({
    enabled: true, accepted_preview_ids: ['p1'], preview_edits: { p1: { dst_vi: 'Triệu Khải Đế' } },
  });
  const previewCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/glossary-preview'))!;
  expect(JSON.parse(String(previewCall[1]!.body))).toEqual({
    provider: 'deepseek', model: 'deepseek-v4-pro', chapters: 20, categories: ['character', 'organization', 'realm', 'location'],
  });
});
