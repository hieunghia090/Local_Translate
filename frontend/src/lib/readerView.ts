/** Chế độ xem ở trang Dịch chương (spec 06 mục 4a), nhớ trong trình duyệt cho mọi chương. */
export type ReaderView = 'main' | 'mt' | 'ai' | 'compare';
export const READER_VIEW_KEY = 'lt.reader.view';
const VIEWS: readonly ReaderView[] = ['main', 'mt', 'ai', 'compare'];

export function storedReaderView(): ReaderView {
  try {
    const v = localStorage.getItem(READER_VIEW_KEY);
    return (VIEWS as readonly string[]).includes(v ?? '') ? (v as ReaderView) : 'main';
  } catch {
    return 'main';
  }
}

export function saveReaderView(view: ReaderView): void {
  try {
    localStorage.setItem(READER_VIEW_KEY, view);
  } catch {
    /* trình duyệt chặn localStorage: chỉ không nhớ được lựa chọn */
  }
}
