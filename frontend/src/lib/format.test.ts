import { describe, expect, it } from 'vitest';
import { fmtDuration, relativeTime, slugify, STATUS } from './format';

describe('slugify', () => {
  it('khớp với slugify của backend', () => {
    expect(slugify('Đại Tống Hữu Chủng')).toBe('dai-tong-huu-chung');
    expect(slugify('  Ngươi qua sông, ta phá cầu!! ')).toBe('nguoi-qua-song-ta-pha-cau');
    expect(slugify('大宋有种')).toBe('');
  });
});

describe('relativeTime', () => {
  const now = Date.parse('2026-10-04T12:00:00Z');
  it('mô tả khoảng cách thời gian', () => {
    expect(relativeTime(null, now)).toBe('chưa mở');
    expect(relativeTime('2026-10-04T11:59:30Z', now)).toBe('vừa xong');
    expect(relativeTime('2026-10-04T11:30:00Z', now)).toBe('30 phút trước');
    expect(relativeTime('2026-10-04T09:00:00Z', now)).toBe('3 giờ trước');
    expect(relativeTime('2026-10-01T12:00:00Z', now)).toBe('3 ngày trước');
  });
});

describe('fmtDuration', () => {
  it('đổi giây ra chữ', () => {
    expect(fmtDuration(null)).toBe('–');
    expect(fmtDuration(42)).toBe('42 giây');
    expect(fmtDuration(600)).toBe('10 phút');
    expect(fmtDuration(3900)).toBe('1 giờ 5 phút');
  });
});

it('có nhãn cho mọi trạng thái chương', () => {
  expect(STATUS.needs_review.label).toBe('Cần soát');
  expect(STATUS.queued.cls).toBe('s-q');
});
