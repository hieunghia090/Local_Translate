import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import type { Segment } from '../../api/types';
import CompareRow from './CompareRow';
import VersionRow from './VersionRow';

const base: Segment = {
  idx: 3, is_meta: false, src: '他走了。', dst: 'Lúc Triệu Khải sắp rời', dst_machine: 'Lúc Triệu Khải sắp rời', edited: false,
  flags: [], dst_mt: 'Khi Triệu Khải chuẩn bị rời', dst_ai: 'Lúc Triệu Khải sắp rời', mt_ai_differ: true,
};
const texts = (els: NodeListOf<Element>) => [...els].map((e) => e.textContent);

it('VersionRow hiện bản của engine, chỉ đọc', () => {
  render(<VersionRow seg={base} engine="mt" />);
  const cell = screen.getByLabelText('Bản Hachimi câu 3');
  expect(cell).toHaveTextContent('Khi Triệu Khải chuẩn bị rời');
  expect(cell).not.toHaveAttribute('contenteditable');
});

it('VersionRow: câu chưa có bản đó thì hiện mờ', () => {
  render(<VersionRow seg={{ ...base, dst_ai: null, mt_ai_differ: null }} engine="ai" />);
  expect(screen.getByLabelText('Bản AI câu 3')).toHaveTextContent('chưa có bản dịch AI');
  expect(screen.getByLabelText('Bản AI câu 3')).toHaveClass('pending');
});

it('AC-6.14: CompareRow tô chữ chỉ có ở mỗi bên và có viền', () => {
  const { container } = render(<CompareRow seg={base} current={false} canUse onUse={() => {}} />);
  expect(texts(container.querySelectorAll('.d-mt'))).toEqual(['Khi', 'chuẩn bị']);
  expect(texts(container.querySelectorAll('.d-ai'))).toEqual(['Lúc', 'sắp']);
  expect(container.querySelector('.cmp-row')).toHaveClass('diff');
});

it('bên trùng bản chính hiện ✓ Đang dùng, bên kia có nút Dùng bản này', () => {
  const onUse = vi.fn();
  render(<CompareRow seg={base} current={false} canUse onUse={onUse} />);
  expect(within(screen.getByRole('group', { name: 'AI câu 3' })).getByText('✓ Đang dùng')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Dùng bản Hachimi cho câu 3' }));
  expect(onUse).toHaveBeenCalledWith(3, 'Khi Triệu Khải chuẩn bị rời');
  expect(screen.queryByRole('button', { name: 'Dùng bản AI cho câu 3' })).toBeNull();
});

it('hai bản giống nhau thì không tô, không viền', () => {
  const same = { ...base, dst_mt: 'Hắn đi.', dst_ai: 'Hắn  đi.', dst: 'Hắn đi.', mt_ai_differ: false };
  const { container } = render(<CompareRow seg={same} current={false} canUse onUse={() => {}} />);
  expect(container.querySelector('.d-mt, .d-ai')).toBeNull();
  expect(container.querySelector('.cmp-row')).not.toHaveClass('diff');
});

it('thiếu bản AI: bên AI hiện mờ, không có nút', () => {
  render(<CompareRow seg={{ ...base, dst_ai: null, mt_ai_differ: null }} current={false} canUse onUse={() => {}} />);
  expect(within(screen.getByRole('group', { name: 'AI câu 3' })).getByText('chưa có bản dịch AI')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Dùng bản AI cho câu 3' })).toBeNull();
});

it('canUse = false thì khoá nút, current thì có class current', () => {
  const { container } = render(<CompareRow seg={base} current canUse={false} onUse={() => {}} />);
  expect(screen.getByRole('button', { name: 'Dùng bản Hachimi cho câu 3' })).toBeDisabled();
  expect(container.querySelector('.cmp-row')).toHaveClass('current');
});

it('dòng meta chỉ hiện bản gốc', () => {
  const meta: Segment = { idx: 1, is_meta: true, src: '=====', dst: '=====', dst_machine: '=====', edited: false, flags: [] };
  const { container } = render(<CompareRow seg={meta} current={false} canUse onUse={() => {}} />);
  expect(container.querySelector('.cmp-row')).toHaveClass('meta');
  expect(screen.queryByRole('group')).toBeNull();
});
