import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReviewFix, Segment } from '../../api/types';
import SegmentRow from './SegmentRow';

const seg: Segment = { idx: 3, is_meta: false, src: '他说。', dst: 'Hắn nói.', dst_machine: 'Hắn nói.', edited: false, flags: [] };

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function type(el: HTMLElement, text: string) {
  el.textContent = text;
  fireEvent.input(el);
}

it('lưu sau 800 ms dừng gõ', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} />);
  type(screen.getByLabelText('Bản dịch câu 3'), 'Hắn bảo.');
  act(() => { vi.advanceTimersByTime(799); });
  expect(onSave).not.toHaveBeenCalled();
  act(() => { vi.advanceTimersByTime(1); });
  expect(onSave).toHaveBeenCalledWith(3, 'Hắn bảo.');
});

it('rời ô thì lưu ngay', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  type(cell, 'Hắn bảo.');
  fireEvent.blur(cell);
  expect(onSave).toHaveBeenCalledTimes(1);
});

it('unmount khi còn bản sửa chưa lưu thì vẫn lưu (Review Focus 1)', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const { unmount } = render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} />);
  type(screen.getByLabelText('Bản dịch câu 3'), 'Hắn bảo.');
  unmount();
  expect(onSave).toHaveBeenCalledWith(3, 'Hắn bảo.');
});

it('không sửa được khi chương đang dịch', () => {
  render(<SegmentRow seg={seg} editable={false} onSave={vi.fn()} onRevert={vi.fn()} />);
  expect(screen.getByLabelText('Bản dịch câu 3')).toHaveAttribute('contenteditable', 'false');
});

it('dòng meta hiện mờ, không có ô dịch', () => {
  render(<SegmentRow seg={{ ...seg, is_meta: true, src: '=====', dst: '=====' }} editable onSave={vi.fn()} onRevert={vi.fn()} />);
  expect(screen.queryByLabelText('Bản dịch câu 3')).toBeNull();
  expect(screen.getByText('=====')).toBeInTheDocument();
});

it('câu đã sửa có dấu ✎ và nút khôi phục bản máy', () => {
  const onRevert = vi.fn();
  render(<SegmentRow seg={{ ...seg, dst: 'Sửa', edited: true }} editable onSave={vi.fn()} onRevert={onRevert} />);
  expect(screen.getByTitle('Đã sửa tay')).toHaveTextContent('✎');
  fireEvent.click(screen.getByRole('button', { name: 'Khôi phục bản máy' }));
  expect(onRevert).toHaveBeenCalledWith(3);
});

it('server đổi nội dung khi đang gõ: giữ chữ đang gõ, không mất bản lưu; blur xong mới hiện bản server', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  fireEvent.focus(cell);
  type(cell, 'Hắn bảo.');
  rerender(<SegmentRow seg={{ ...seg, dst: 'Bản mới từ server' }} editable onSave={onSave} onRevert={vi.fn()} />);
  expect(screen.getByLabelText('Bản dịch câu 3')).toBe(cell);
  expect(cell.textContent).toBe('Hắn bảo.');
  fireEvent.blur(cell);
  expect(onSave).toHaveBeenCalledWith(3, 'Hắn bảo.');
  expect(screen.getByLabelText('Bản dịch câu 3')).toHaveTextContent('Bản mới từ server');
});

it('so sánh với bản server mới nhất, không phải bản của lần render đầu', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} />);
  rerender(<SegmentRow seg={{ ...seg, dst: 'Bản mới' }} editable onSave={onSave} onRevert={vi.fn()} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  fireEvent.focus(cell);
  type(cell, 'Hắn nói.'); // bằng bản gốc cũ, khác bản server mới
  fireEvent.blur(cell);
  expect(onSave).toHaveBeenCalledWith(3, 'Hắn nói.');
});

it('khôi phục bản máy thì huỷ bản sửa đang chờ, chỉ gửi yêu cầu khôi phục', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const onRevert = vi.fn();
  render(<SegmentRow seg={{ ...seg, dst: 'Sửa', edited: true }} editable onSave={onSave} onRevert={onRevert} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  fireEvent.focus(cell);
  type(cell, 'Gõ dở');
  fireEvent.mouseDown(screen.getByRole('button', { name: 'Khôi phục bản máy' }));
  fireEvent.blur(cell);
  fireEvent.click(screen.getByRole('button', { name: 'Khôi phục bản máy' }));
  act(() => { vi.advanceTimersByTime(2000); });
  expect(onRevert).toHaveBeenCalledWith(3);
  expect(onSave).not.toHaveBeenCalled();
});

it('tô sáng thuật ngữ ở cả hai cột khi không sửa', () => {
  const s = { ...seg, src: '赵楷说。', dst: 'Triệu Khải nói.', glossary_spans: [{ term_id: 't1', src: [0, 2] as [number, number], dst: [0, 10] as [number, number] }] };
  render(<SegmentRow seg={s} editable onSave={vi.fn()} onRevert={vi.fn()} terms={{ t1: 'Triệu Khải' }} />);
  expect(screen.getByText('赵楷')).toHaveAttribute('title', '→ Triệu Khải');
  expect(screen.getByText('Triệu Khải').tagName).toBe('MARK');
});

it('đang sửa thì span mới từ server không dựng lại ô dịch (giữ chữ đang gõ)', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const s = { ...seg, src: '赵楷说。', dst: 'Triệu Khải nói.', glossary_spans: [{ term_id: 't1', src: [0, 2] as [number, number], dst: [0, 10] as [number, number] }] };
  const { rerender } = render(<SegmentRow seg={s} editable onSave={onSave} onRevert={vi.fn()} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  fireEvent.focus(cell);
  type(cell, 'Đang gõ dở');
  rerender(<SegmentRow seg={{ ...s, glossary_spans: [] }} editable onSave={onSave} onRevert={vi.fn()} />);
  expect(screen.getByLabelText('Bản dịch câu 3')).toHaveTextContent('Đang gõ dở');
});

it('cờ placeholder_lost hiện nhãn mất thuật ngữ', () => {
  render(<SegmentRow seg={{ ...seg, flags: ['placeholder_lost'] }} editable onSave={vi.fn()} onRevert={vi.fn()} />);
  expect(screen.getByText('mất thuật ngữ')).toBeInTheDocument();
});

const honSeg = (): Segment => ({ ...seg, src: '他说。', dst: 'Hắn nói.', honorific_edits: [{ from: 'Anh ta', to: 'Hắn', rule: 'pronoun.他', offset: 0, src_token: '他' }] });

it('gạch chân chấm chỗ chuẩn hoá xưng hô và bỏ được từng chỗ (AC-6.9)', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SegmentRow seg={honSeg()} editable onSave={onSave} onRevert={vi.fn()} />);
  fireEvent.click(screen.getByTitle('Anh ta → Hắn · đại từ'));
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ thay đổi này' }));
  expect(onSave).toHaveBeenCalledWith(3, 'Anh ta nói.');
});

it('không gạch chân xưng hô khi ô đang được sửa', () => {
  render(<SegmentRow seg={honSeg()} editable onSave={vi.fn()} onRevert={vi.fn()} />);
  const cell = screen.getByRole('textbox', { name: 'Bản dịch câu 3' });
  expect(screen.queryByTitle('Anh ta → Hắn · đại từ')).not.toBeNull();
  fireEvent.focus(cell);
  type(cell, 'Hắn nói to.');
  expect(screen.queryByTitle('Anh ta → Hắn · đại từ')).toBeNull();
});

it('glossary chồng span xưng hô thì glossary thắng', () => {
  const s = { ...honSeg(), glossary_spans: [{ term_id: 't1', src: [0, 1] as [number, number], dst: [0, 3] as [number, number] }] };
  render(<SegmentRow seg={s} editable onSave={vi.fn()} onRevert={vi.fn()} />);
  expect(screen.queryByTitle('Anh ta → Hắn · đại từ')).toBeNull();
  expect(document.querySelector('.dst mark')?.textContent).toBe('Hắn');
});

it('ô đang có focus mà bấm gạch chân thì vẫn hiện nút bỏ thay đổi, và bỏ được', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  render(<SegmentRow seg={honSeg()} editable onSave={onSave} onRevert={vi.fn()} />);
  fireEvent.focus(screen.getByRole('textbox', { name: 'Bản dịch câu 3' }));
  fireEvent.click(screen.getByTitle('Anh ta → Hắn · đại từ'));
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ thay đổi này' }));
  expect(onSave).toHaveBeenCalledWith(3, 'Anh ta nói.');
});

it('nút bỏ thay đổi nằm ngoài ô contentEditable', () => {
  render(<SegmentRow seg={honSeg()} editable onSave={vi.fn()} onRevert={vi.fn()} />);
  fireEvent.click(screen.getByTitle('Anh ta → Hắn · đại từ'));
  expect(screen.getByRole('button', { name: 'Bỏ thay đổi này' }).closest('[contenteditable]')).toBeNull();
});

it('hai chỗ trong một câu: bỏ chỗ đầu thì chỗ sau vẫn gạch chân và bỏ được (offset lệch được tính lại)', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const two: Segment = {
    ...seg, src: '他看着她。', dst: 'Hắn nhìn nàng.', edited: false,
    honorific_edits: [
      { from: 'Anh ta', to: 'Hắn', rule: 'pronoun.他', offset: 0, src_token: '他' },
      { from: 'cô ấy', to: 'nàng', rule: 'pronoun.她', offset: 9, src_token: '她' },
    ],
  };
  const { rerender } = render(<SegmentRow seg={two} editable onSave={onSave} onRevert={vi.fn()} />);
  fireEvent.click(screen.getByTitle('Anh ta → Hắn · đại từ'));
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ thay đổi này' }));
  expect(onSave).toHaveBeenLastCalledWith(3, 'Anh ta nhìn nàng.');
  rerender(<SegmentRow seg={{ ...two, dst: 'Anh ta nhìn nàng.', edited: true }} editable onSave={onSave} onRevert={vi.fn()} />);
  expect(screen.queryByTitle('Anh ta → Hắn · đại từ')).toBeNull();
  fireEvent.click(screen.getByTitle('cô ấy → nàng · đại từ'));
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ thay đổi này' }));
  expect(onSave).toHaveBeenLastCalledWith(3, 'Anh ta nhìn cô ấy.');
});

const fix: ReviewFix = {
  id: 'f1', chapter_id: 'c1', segment_idx: 3, type: 'name_mismatch', before: 'Hắn nói.', after: 'Cao Cầu nói.', reason: 'sai tên',
  confidence: 92, status: 'pending', model_id: 'deepseek-flash', created_at: 'x', decided_at: null,
};

it('đề xuất DeepSeek: viền tím, before gạch ngang, after tô xanh, áp và bỏ từng đề xuất (spec 06 mục 4)', () => {
  const onDecide = vi.fn();
  const { container } = render(<SegmentRow seg={seg} editable onSave={vi.fn()} onRevert={vi.fn()} fixes={[fix]} onDecide={onDecide} />);
  expect(container.querySelector('.cmp-row.fix[data-row="3"]')).not.toBeNull();
  expect(screen.getByText('Hắn nói.', { selector: 'del' })).toBeInTheDocument();
  expect(screen.getByText('Cao Cầu nói.', { selector: 'ins' })).toBeInTheDocument();
  expect(screen.getByText(/Sai tên riêng · tin cậy 92 · sai tên/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Áp' }));
  expect(onDecide).toHaveBeenCalledWith(['f1'], 'apply');
  fireEvent.click(screen.getByRole('button', { name: 'Bỏ' }));
  expect(onDecide).toHaveBeenCalledWith(['f1'], 'reject');
});

it('cờ DeepSeek có nhãn; glossary_miss nêu term bị bỏ qua', () => {
  const s = { ...seg, src: '高俅说。', dst: 'Cao Cừu nói.', flags: ['glossary_miss', 'fallback_ct2', 'residual_han'],
    glossary_spans: [{ term_id: 't1', src: [0, 2] as [number, number], dst: null }] };
  render(<SegmentRow seg={s} editable onSave={vi.fn()} onRevert={vi.fn()} terms={{ t1: 'Cao Cầu' }} />);
  expect(screen.getByText('bỏ qua glossary')).toHaveAttribute('title', 'Bỏ qua: Cao Cầu');
  expect(screen.getByText('dịch bằng HachimiMT')).toHaveAttribute('title', 'DeepSeek bỏ sót dòng này, đã dịch bằng HachimiMT');
  expect(screen.getByText('còn chữ Hán')).toBeInTheDocument();
});

it('đang sửa câu có đề xuất thì vẫn giữ chữ đang gõ (G5)', () => {
  const onSave = vi.fn().mockResolvedValue(undefined);
  const { rerender } = render(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} fixes={[fix]} />);
  const cell = screen.getByLabelText('Bản dịch câu 3');
  fireEvent.focus(cell);
  type(cell, 'Đang gõ dở');
  rerender(<SegmentRow seg={seg} editable onSave={onSave} onRevert={vi.fn()} fixes={[]} />);
  expect(screen.getByLabelText('Bản dịch câu 3')).toHaveTextContent('Đang gõ dở');
});

it('chương đang bận: nút Áp và Bỏ của đề xuất bị khoá, không gọi onDecide', () => {
  const onDecide = vi.fn();
  render(<SegmentRow seg={seg} editable={false} onSave={vi.fn()} onRevert={vi.fn()} fixes={[fix]} onDecide={onDecide} busy />);
  expect(screen.getByRole('button', { name: 'Áp' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Bỏ' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Áp' }));
  expect(onDecide).not.toHaveBeenCalled();
});
