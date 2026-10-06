import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import ProgressBar from './ProgressBar';
import Segmented from './Segmented';
import StatusChip from './StatusChip';

it('StatusChip hiện nhãn tiếng Việt', () => {
  render(<StatusChip status="reviewed" />);
  expect(screen.getByText('Đã soát')).toHaveClass('chip', 's-ok2');
});

it('Segmented báo giá trị được chọn', () => {
  const onChange = vi.fn();
  render(<Segmented ariaLabel="Lọc" value="a" onChange={onChange} options={[{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }]} />);
  expect(screen.getByRole('button', { name: 'A' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'B' }));
  expect(onChange).toHaveBeenCalledWith('b');
});

it('ProgressBar chia phần đã dịch / cần soát / đang dịch', () => {
  const stats = { total: 10, todo: 4, queued: 0, translating: 1, translated: 2, needs_review: 1, reviewed: 2, error: 0 };
  const { container } = render(<ProgressBar stats={stats} />);
  const parts = [...container.querySelectorAll('.bar > i')].map((i) => (i as HTMLElement).style.width);
  expect(parts).toEqual(['40%', '10%', '10%']);
});
