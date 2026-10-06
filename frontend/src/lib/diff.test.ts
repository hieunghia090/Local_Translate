import { expect, it } from 'vitest';
import { diffTokens, normCmp, tokenize, type DiffPart } from './diff';

const changed = (parts: DiffPart[]) => parts.filter((p) => p.changed).map((p) => p.text);
const joined = (parts: DiffPart[]) => parts.map((p) => p.text).join('');

it('tách âm tiết, dấu câu, khoảng trắng', () => {
  expect(tokenize('Khi Triệu Khải, rời.')).toEqual(['Khi', ' ', 'Triệu', ' ', 'Khải', ',', ' ', 'rời', '.']);
  expect(tokenize('')).toEqual([]);
});

it('AC-6.14: chỉ tô chữ khác, khoảng trắng giữa hai chữ khác được gộp', () => {
  const a = 'Khi Triệu Khải chuẩn bị rời';
  const b = 'Lúc Triệu Khải sắp rời';
  const d = diffTokens(a, b);
  expect(changed(d.a)).toEqual(['Khi', 'chuẩn bị']);
  expect(changed(d.b)).toEqual(['Lúc', 'sắp']);
  expect(joined(d.a)).toBe(a);
  expect(joined(d.b)).toBe(b);
});

it('giống nhau, hoặc chỉ khác khoảng trắng / dạng Unicode, thì không tô', () => {
  expect(changed(diffTokens('Hắn đi.', 'Hắn đi.').a)).toEqual([]);
  expect(changed(diffTokens('Hắn  đi.', ' Hắn đi.').b)).toEqual([]);
  expect(changed(diffTokens('Lâm Phàm'.normalize('NFD'), 'Lâm Phàm').a)).toEqual([]);
});

it('dấu câu được tô riêng', () => {
  const d = diffTokens('Hắn nói.', 'Hắn nói,');
  expect(changed(d.a)).toEqual(['.']);
  expect(changed(d.b)).toEqual([',']);
});

it('một bên rỗng thì bên kia tô hết', () => {
  const d = diffTokens('', 'Hắn đi.');
  expect(d.a).toEqual([]);
  expect(changed(d.b)).toEqual(['Hắn đi.']);
});

it('quá nhiều ô thì tô cả phần giữa, giữ phần đầu và cuối chung', () => {
  const mid = (w: string) => Array.from({ length: 600 }, (_, i) => `${w}${i}`).join(' ');
  const d = diffTokens(`Đầu ${mid('a')} cuối.`, `Đầu ${mid('b')} cuối.`);
  expect(changed(d.a)).toEqual([mid('a')]);
  expect(changed(d.b)).toEqual([mid('b')]);
});

it('normCmp giống quy tắc server (BR-6.11)', () => {
  expect(normCmp('  Hắn  \t đi.\n')).toBe('Hắn đi.');
  expect(normCmp('Lâm'.normalize('NFD'))).toBe('Lâm');
  expect(normCmp('Hắn đi.')).toBe('Hắn đi.');
});
