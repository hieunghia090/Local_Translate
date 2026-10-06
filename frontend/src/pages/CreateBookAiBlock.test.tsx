import { expect, it } from 'vitest';
import { aiExtractPayload, DEFAULT_AI } from './CreateBookAiBlock';

it('tắt AI thì chỉ gửi {enabled:false}, dù không chọn loại nào (backend không đòi loại khi tắt)', () => {
  expect(aiExtractPayload({ ...DEFAULT_AI, enabled: false, categories: [] })).toEqual({ enabled: false });
  expect(aiExtractPayload({ ...DEFAULT_AI, enabled: false })).toEqual({ enabled: false });
});

it('bật AI thì gửi đủ cấu hình', () => {
  expect(aiExtractPayload(DEFAULT_AI)).toEqual({
    enabled: true, provider: 'deepseek', model: 'deepseek-v4-pro', chapters: 20,
    categories: ['character', 'organization', 'realm', 'location'], accepted_preview_ids: [], preview_edits: {},
  });
});

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, vi } from 'vitest';
import CreateBookAiBlock from './CreateBookAiBlock';

afterEach(() => vi.restoreAllMocks());

function renderBlock(props: { importId: string | null; ready: boolean }) {
  const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
    const url = String(input);
    if (url.includes('/glossary-preview/estimate')) {
      return new Response(JSON.stringify({ model_id: 'deepseek-v4-pro', chapters: 20, batches: 2, tokens_in: 12000, tokens_in_cached: 500,
        tokens_out: 900, cost_usd: 0.0123, prices_are_samples: true }));
    }
    return new Response(JSON.stringify({ key_present: true, key_masked: '••••abcd', paused: false, paused_reason: null, message: null }));
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><CreateBookAiBlock {...props} value={DEFAULT_AI} onChange={() => {}} /></QueryClientProvider>);
  return fetchMock;
}

it('hiện chi phí ước tính ngay trong khối AI, trước nút Chạy thử', async () => {
  const fetchMock = renderBlock({ importId: 'imp1', ready: true });
  expect(await screen.findByText(/Chi phí ước tính cho 20 chương.*\$0[.,]01.*\(giá mẫu\)/)).toBeInTheDocument();
  const call = fetchMock.mock.calls.find(([u]) => String(u).includes('/imports/imp1/glossary-preview/estimate'))!;
  expect(JSON.parse(String(call[1]!.body))).toEqual({
    provider: 'deepseek', model: 'deepseek-v4-pro', chapters: 20, categories: ['character', 'organization', 'realm', 'location'],
  });
  const estimate = screen.getByText(/Chi phí ước tính cho 20 chương/);
  const run = screen.getByRole('button', { name: 'Chạy thử' });
  expect(estimate.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it('chưa phân tích xong thì chưa gọi ước tính', async () => {
  const fetchMock = renderBlock({ importId: 'imp1', ready: false });
  expect(await screen.findByText(/sẽ hiện khi file phân tích xong/)).toBeInTheDocument();
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/estimate'))).toBe(false);
});
