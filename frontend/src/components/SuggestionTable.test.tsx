import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { expect, it, vi } from 'vitest';
import SuggestionTable, { defaultPicked, type SuggestionEdit, type SuggestionRow } from './SuggestionTable';

const rows: SuggestionRow[] = [
  { id: 's1', src_zh: '赵楷', dst_vi: 'Triệu Khải', category: 'character', confidence: 95, occurrence_count: 41, context: '赵楷来了' },
  { id: 's2', src_zh: '苏清雪', dst_vi: 'Tô Thanh Tuyết', category: 'character', confidence: 70, occurrence_count: 12, context: null },
];

function Harness({ onState }: { onState: (p: Set<string>, e: Record<string, SuggestionEdit>) => void }) {
  const [picked, setPicked] = useState(defaultPicked(rows));
  const [edits, setEdits] = useState<Record<string, SuggestionEdit>>({});
  onState(picked, edits);
  return <SuggestionTable rows={rows} picked={picked} onPick={setPicked} edits={edits}
    onEdit={(id, e) => setEdits({ ...edits, [id]: { ...edits[id], ...e } })} />;
}

it('mục dưới 80% không được chọn sẵn và hiện màu cam (BR-4.7)', () => {
  const onState = vi.fn();
  render(<Harness onState={onState} />);
  expect(screen.getByLabelText('Chọn đề xuất 赵楷')).toBeChecked();
  expect(screen.getByLabelText('Chọn đề xuất 苏清雪')).not.toBeChecked();
  expect(screen.getByText('70')).toHaveClass('conf-low');
  expect(screen.getByText('95')).not.toHaveClass('conf-low');
  const input = screen.getByLabelText('Đề xuất Việt cho 赵楷');
  fireEvent.change(input, { target: { value: 'Triệu Khải Đế' } });
  fireEvent.blur(input);
  fireEvent.click(screen.getByLabelText('Chọn đề xuất 苏清雪'));
  const [picked, edits] = onState.mock.calls.at(-1)!;
  expect([...picked].sort()).toEqual(['s1', 's2']);
  expect(edits).toEqual({ s1: { dst_vi: 'Triệu Khải Đế' } });
});

const many = (n: number): SuggestionRow[] => Array.from({ length: n }, (_, i) => ({
  id: `m${i}`, src_zh: `词${i}`, dst_vi: `Từ ${i}`, category: 'term' as const, confidence: 90, occurrence_count: n - i, context: null,
}));

it('danh sách dài được chia trang 200 mục (phía client)', () => {
  const list = many(450);
  render(<SuggestionTable rows={list} picked={new Set()} onPick={() => {}} edits={{}} onEdit={() => {}} />);
  expect(screen.getAllByLabelText(/^Chọn đề xuất 词/)).toHaveLength(200);
  expect(screen.getByText(/Trang 1\/3 · 450 mục/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  expect(screen.getByLabelText('Chọn đề xuất 词200')).toBeInTheDocument();
  expect(screen.queryByLabelText('Chọn đề xuất 词0')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  expect(screen.getAllByLabelText(/^Chọn đề xuất 词/)).toHaveLength(50);
  expect(screen.getByRole('button', { name: 'Trang sau' })).toBeDisabled();
});

it('chế độ phân trang phía server: hiện trang hiện tại, gọi onPage', () => {
  const onPage = vi.fn();
  render(<SuggestionTable rows={many(200)} picked={new Set()} onPick={() => {}} edits={{}} onEdit={() => {}}
    pager={{ page: 1, pageSize: 200, total: 450, onPage }} />);
  expect(screen.getByText(/Trang 2\/3 · 450 mục/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Trang trước' }));
  expect(onPage).toHaveBeenCalledWith(0);
  fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
  expect(onPage).toHaveBeenCalledWith(2);
});

it('ô chọn tất cả chỉ áp dụng cho trang đang xem', () => {
  const onPick = vi.fn();
  render(<SuggestionTable rows={many(450)} picked={new Set(['m300'])} onPick={onPick} edits={{}} onEdit={() => {}} />);
  fireEvent.click(screen.getByLabelText('Chọn tất cả đề xuất'));
  const next: Set<string> = onPick.mock.calls.at(-1)![0];
  expect(next.size).toBe(201); // 200 mục của trang 1 + mục m300 đã chọn ở trang khác
  expect(next.has('m0') && next.has('m199') && next.has('m300')).toBe(true);
});
