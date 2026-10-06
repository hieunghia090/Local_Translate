import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, errorMessage, qs } from './client';

afterEach(() => vi.restoreAllMocks());

function mockFetch(status: number, body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }),
  );
}

describe('api client', () => {
  it('gửi JSON và đọc kết quả', async () => {
    const f = mockFetch(200, { ok: true });
    expect(await api.post('/x', { a: 1 })).toEqual({ ok: true });
    const [url, init] = f.mock.calls[0];
    expect(url).toBe('/api/v1/x');
    expect((init as RequestInit).body).toBe('{"a":1}');
  });

  it('ném ApiError với code và message của server', async () => {
    mockFetch(409, { error: { code: 'CONFIRM_MANUAL_EDITS', message: 'Có câu sửa tay', details: { chapters: 2 } } });
    const e = (await api.post('/x').catch((err) => err)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.message, e.details.chapters]).toEqual([409, 'CONFIRM_MANUAL_EDITS', 'Có câu sửa tay', 2]);
  });

  it('204 trả undefined', async () => {
    mockFetch(204, undefined);
    expect(await api.del('/x')).toBeUndefined();
  });

  it('mất kết nối thành thông báo tiếng Việt', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'));
    const e = (await api.get('/x').catch((err) => err)) as ApiError;
    expect(e.code).toBe('NETWORK');
    expect(errorMessage(e)).toMatch(/Không kết nối được/);
  });

  it('qs bỏ giá trị rỗng', () => {
    expect(qs({ a: 1, b: undefined, c: '', d: null, e: 'x y' })).toBe('?a=1&e=x+y');
    expect(qs({})).toBe('');
  });
});
