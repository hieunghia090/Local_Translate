import type { BookState, ChapterStatus, GlossaryCategory, Genre } from '../api/types';

export const STATUS: Record<ChapterStatus, { label: string; cls: string }> = {
  todo: { label: 'Chưa dịch', cls: 's-todo' },
  queued: { label: 'Đang chờ', cls: 's-q' },
  translating: { label: 'Đang dịch', cls: 's-run' },
  translated: { label: 'Đã dịch', cls: 's-done' },
  needs_review: { label: 'Cần soát', cls: 's-rev' },
  reviewed: { label: 'Đã soát', cls: 's-ok2' },
  error: { label: 'Lỗi', cls: 's-err' },
};
export const DONE_STATUSES: ChapterStatus[] = ['translated', 'needs_review', 'reviewed'];
export const BUSY_STATUSES: ChapterStatus[] = ['queued', 'translating'];

export const STATE_LABEL: Record<BookState, { label: string; cls: string }> = {
  not_started: { label: 'Chưa bắt đầu', cls: 's-todo' },
  in_progress: { label: 'Đang dịch', cls: 's-run' },
  completed: { label: 'Hoàn tất', cls: 's-done' },
};

export const GENRES: { value: Genre; label: string }[] = [
  { value: 'xianxia', label: 'Tiên hiệp / Cổ trang' },
  { value: 'urban', label: 'Đô thị / Hiện đại' },
  { value: 'modern_war', label: 'Chiến tranh hiện đại' },
  { value: 'xuanhuan', label: 'Huyền huyễn' },
  { value: 'romance', label: 'Ngôn tình' },
  { value: 'other', label: 'Khác' },
];
export const genreLabel = (g: Genre) => GENRES.find((x) => x.value === g)?.label ?? g;

export const fmtNum = (n: number) => n.toLocaleString('vi-VN');

export function relativeTime(iso: string | null, now: number = Date.now()): string {
  if (!iso) return 'chưa mở';
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return 'vừa xong';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} phút trước`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} giờ trước`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d} ngày trước`;
  return new Date(iso).toLocaleDateString('vi-VN');
}

export const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString('vi-VN', { hour12: false });
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('vi-VN', { hour12: false, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });

export function fmtDuration(seconds: number | null): string {
  if (seconds === null) return '–';
  if (seconds < 60) return `${Math.round(seconds)} giây`;
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m} phút`;
  return `${Math.floor(m / 60)} giờ ${m % 60} phút`;
}

/** Giống app.core.slug.slugify ở backend. */
export function slugify(text: string, maxLen = 60): string {
  const folded = text.replace(/đ/g, 'd').replace(/Đ/g, 'D').normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase();
  return folded.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, maxLen).replace(/-+$/, '');
}

export const CATEGORY_LABEL: Record<GlossaryCategory, string> = {
  character: 'Nhân vật', location: 'Địa danh', organization: 'Tổ chức / Môn phái', term: 'Thuật ngữ',
  rank: 'Quân hàm / Chức vụ', realm: 'Cảnh giới', item: 'Vật phẩm / Công pháp', abbreviation: 'Viết tắt',
};

export const FIX_TYPE_LABEL: Record<string, string> = {
  name_mismatch: 'Sai tên riêng', missing_content: 'Sót ý', mistranslation: 'Sai nghĩa', honorific: 'Sai xưng hô', grammar: 'Ngữ pháp',
};
export const fmtUsd = (n: number) => `$${n > 0 && n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
export const fmtPct = (n: number | null | undefined) => (n === null || n === undefined ? '–' : `${n.toLocaleString('vi-VN')}%`);
export const LOW_CONFIDENCE = 80; // BR-4.7
export const NAME_LANG_LABEL: Record<'zh' | 'ja' | 'foreign', string> = {
  zh: 'Trung (Hán Việt)', ja: 'Nhật (Romaji)', foreign: 'Nga / Âu (tên gốc)',
};
