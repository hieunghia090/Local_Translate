import { useQuery } from '@tanstack/react-query';
import { api, errorMessage } from '../api/client';
import type { ChapterStatus, CostEstimate } from '../api/types';
import { fmtNum, fmtUsd } from '../lib/format';
import ConfirmDialog from './ConfirmDialog';

export const ESTIMATE_MIN_CHAPTERS = 10; // BR-8.26

export interface EstimateTarget {
  action: 'translate' | 'review';
  chapter_ids?: string[];
  filter?: { status: ChapterStatus[] };
  model_id?: string;
}

export function estimateLine(e: CostEstimate): string {
  return `${fmtNum(e.chapters)} chương · ~${fmtNum(e.tokens_in)} token vào · ~${fmtNum(e.tokens_out)} token ra · ~${fmtUsd(e.cost_usd)} (giả định trúng cache prompt nền)`;
}

export default function CostEstimateDialog(props: {
  bookId: string; target: EstimateTarget; title: string; confirmLabel: string; onConfirm: () => void; onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ['estimate', props.bookId, props.target],
    queryFn: () => api.post<CostEstimate>(`/books/${props.bookId}/estimate`, props.target),
    staleTime: 30_000,
  });
  const e = q.data;
  return (
    <ConfirmDialog title={props.title} onClose={props.onClose}
      actions={[{ label: props.confirmLabel, primary: true, disabled: !e || e.chapters === 0, onClick: props.onConfirm }]}>
      {q.isPending && <p className="hint">Đang ước tính chi phí…</p>}
      {q.isError && <p className="err-text">Không ước tính được: {errorMessage(q.error)}</p>}
      {e && (
        <>
          <p>{estimateLine(e)}</p>
          {e.skipped > 0 && <p className="hint">Bỏ qua {fmtNum(e.skipped)} chương không đủ điều kiện.</p>}
          <p className="hint">
            Model {e.model_id} · hệ số {e.coefficients.calibrated ? `đã hiệu chỉnh từ ${e.coefficients.samples} request` : 'mặc định'}
            {e.prices_are_samples ? ' · đang dùng giá mẫu, sửa ở Cấu hình → Chi phí' : ''}
          </p>
        </>
      )}
    </ConfirmDialog>
  );
}
