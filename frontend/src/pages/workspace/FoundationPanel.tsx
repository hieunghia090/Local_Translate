import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { Foundation, FoundationPreview } from '../../api/types';
import { useToast } from '../../components/Toast';
import { fmtNum } from '../../lib/format';
import { useAutosave } from '../../lib/hooks';

export default function FoundationPanel({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ['foundation', bookId], queryFn: () => api.get<Foundation>(`/books/${bookId}/foundation`) });
  const loaded = q.data?.foundation_prompt ?? null;
  const [text, setText] = useState<string | null>(null); // null = chưa sửa, hiện bản server
  const [previewNo, setPreviewNo] = useState(1);
  const [preview, setPreview] = useState<FoundationPreview | null>(null);
  const value = text ?? loaded ?? '';

  const { state, rebase } = useAutosave(value, async (v) => {
    await api.put<Foundation>(`/books/${bookId}/foundation`, { foundation_prompt: v });
    void qc.invalidateQueries({ queryKey: ['foundation', bookId] });
  }, 500, text !== null && value.trim().length > 0);

  useEffect(() => {
    if (loaded !== null && text === null) rebase(loaded); // nạp từ server không tính là sửa
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  const reset = useMutation({
    mutationFn: () => api.post<Foundation>(`/books/${bookId}/foundation/reset`),
    onSuccess: (f) => { rebase(f.foundation_prompt); setText(null); qc.setQueryData(['foundation', bookId], f); toast('Đã khôi phục prompt nền mẫu'); },
    onError: (e) => toast(errorMessage(e)),
  });
  const loadPreview = useMutation({
    mutationFn: () => api.get<FoundationPreview>(`/books/${bookId}/foundation/preview?chapter_no=${previewNo}`),
    onSuccess: setPreview,
    onError: (e) => toast(errorMessage(e)),
  });

  return (
    <div className="panel">
      <div className="row">
        <h2 className="grow">Prompt nền (DeepSeek)</h2>
        <span className="saved" aria-live="polite">{state === 'saving' ? 'Đang lưu…' : state === 'saved' ? 'Đã lưu' : state === 'error' ? 'Lưu lỗi' : ''}</span>
      </div>
      <textarea aria-label="Prompt nền" rows={12} value={value} onChange={(e) => setText(e.target.value)} />
      {q.data?.honorific_block && <pre className="log-detail">{q.data.honorific_block}</pre>}
      <div className="hint">Prompt nền giống hệt nhau ở mọi chương để DeepSeek dùng lại cache. Khối Xưng hô ở trên sinh tự động từ thể loại và cấu hình xưng hô.</div>
      <div className="row">
        <button className="btn sm" disabled={reset.isPending} onClick={() => reset.mutate()}>Khôi phục mẫu</button>
        <span className="grow" />
        <label htmlFor="fPrev" className="hint">Xem prompt đầy đủ cho chương #</label>
        <input id="fPrev" type="number" min={1} value={previewNo} style={{ width: 80 }}
          onChange={(e) => setPreviewNo(Math.max(1, Number(e.target.value) || 1))} />
        <button className="btn sm" disabled={loadPreview.isPending} onClick={() => loadPreview.mutate()}>Xem</button>
      </div>
      {preview && (
        <div style={{ display: 'grid', gap: 6 }}>
          <div className="hint">
            Chương {preview.chapter_no} · {preview.model_id} · Glossary gửi: {preview.glossary.sent}/{preview.glossary.matched} term (cắt {preview.glossary.truncated}) · ~{fmtNum(preview.tokens_in_est)} token vào
          </div>
          <pre className="log-detail" aria-label="System prompt">{preview.system}</pre>
          <pre className="log-detail" aria-label="User prompt">{preview.user}</pre>
        </div>
      )}
    </div>
  );
}
