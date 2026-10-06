import { afterEach, expect, it, vi } from 'vitest';
import { READER_VIEW_KEY, saveReaderView, storedReaderView } from './readerView';

afterEach(() => { vi.restoreAllMocks(); localStorage.clear(); });

it('mặc định là Bản chính, nhớ lựa chọn hợp lệ', () => {
  expect(storedReaderView()).toBe('main');
  saveReaderView('compare');
  expect(localStorage.getItem(READER_VIEW_KEY)).toBe('compare');
  expect(storedReaderView()).toBe('compare');
  localStorage.setItem(READER_VIEW_KEY, 'lạ');
  expect(storedReaderView()).toBe('main');
});

it('localStorage lỗi thì không ném, dùng Bản chính', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
  expect(storedReaderView()).toBe('main');
  expect(() => saveReaderView('ai')).not.toThrow();
});
