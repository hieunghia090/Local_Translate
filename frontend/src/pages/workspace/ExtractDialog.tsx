import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { DeepSeekModelId, ExtractBody, ExtractEstimate, GlossaryCategory } from '../../api/types';
import Banner from '../../components/Banner';
import CategoryPicker, { DEFAULT_CATEGORIES } from '../../components/CategoryPicker';
import ConfirmDialog from '../../components/ConfirmDialog';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';
import { fmtNum, fmtUsd } from '../../lib/format';

/** Spec 04 mục 6 "Cấu hình": model, phạm vi chương, loại cần trích, chi phí ước tính, cảnh báo gửi nội dung. */
export default function ExtractDialog({ bookId, onClose }: { bookId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [model, setModel] = useState<DeepSeekModelId>('deepseek-v4-pro');
  const [mode, setMode] = useState<'unscanned' | 'range'>('unscanned');
  const [from, setFrom] = useState(1);
  const [to, setTo] = useState(20);
  const [categories, setCategories] = useState<GlossaryCategory[]>(DEFAULT_CATEGORIES);
  const body: ExtractBody = { provider: 'deepseek', model, scope: mode === 'range' ? { mode, from, to } : { mode }, categories };
  const est = useQuery({
    queryKey: ['extract-estimate', bookId, body],
    queryFn: () => api.post<ExtractEstimate>(`/books/${bookId}/glossary/extract/estimate`, body),
    enabled: categories.length > 0 && (mode === 'unscanned' || from <= to),
    retry: false,
  });
  const start = useMutation({
    mutationFn: () => api.post<{ job_id: string; chapters: number }>(`/books/${bookId}/glossary/extract`, body),
    onSuccess: (r) => {
      toast(`Đã thêm job trích glossary cho ${r.chapters} chương`, { to: `/books/${bookId}/queue`, label: 'Xem hàng đợi' });
      void qc.invalidateQueries({ queryKey: ['glossary-suggestions', bookId] });
      onClose();
    },
    onError: (e) => toast(errorMessage(e)),
  });
  const e = est.data;
  return (
    <ConfirmDialog title="Trích xuất thuật ngữ bằng AI" onClose={onClose} actions={[{
      label: 'Bắt đầu trích xuất', primary: true, onClick: () => start.mutate(),
      disabled: !categories.length || start.isPending || e?.chapters === 0,
    }]}>
      <div className="field">
        <label htmlFor="exModel">Model</label>
        <select id="exModel" value={model} onChange={(ev) => setModel(ev.target.value as DeepSeekModelId)}>
          <option value="deepseek-v4-pro">deepseek-v4-pro</option>
          <option value="deepseek-flash">deepseek-flash</option>
        </select>
      </div>
      <div className="field">
        <span className="lbl">Phạm vi chương</span>
        <Segmented ariaLabel="Phạm vi chương" value={mode} onChange={setMode}
          options={[{ value: 'unscanned', label: 'Các chương chưa quét' }, { value: 'range', label: 'Khoảng chương' }]} />
      </div>
      {mode === 'range' && (
        <div className="row">
          <label htmlFor="exFrom">Từ chương</label>
          <input id="exFrom" type="number" min={1} value={from} style={{ width: 90 }} onChange={(ev) => setFrom(Math.max(1, Number(ev.target.value) || 1))} />
          <label htmlFor="exTo">Đến chương</label>
          <input id="exTo" type="number" min={1} value={to} style={{ width: 90 }} onChange={(ev) => setTo(Math.max(1, Number(ev.target.value) || 1))} />
        </div>
      )}
      <CategoryPicker value={categories} onChange={setCategories} />
      <p className="hint">
        {e ? `${fmtNum(e.chapters)} chương · ${e.batches} lô · ~${fmtNum(e.tokens_in)} token vào · ~${fmtNum(e.tokens_out)} token ra · ~${fmtUsd(e.cost_usd)}${e.prices_are_samples ? ' (giá mẫu)' : ''}`
          : est.isError ? errorMessage(est.error) : 'Đang ước tính chi phí…'}
      </p>
      <Banner kind="warn">Nội dung các chương được chọn sẽ gửi tới DeepSeek.</Banner>
    </ConfirmDialog>
  );
}
