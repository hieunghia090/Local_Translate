import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { GlossaryHealth } from '../../api/types';
import { useToast } from '../../components/Toast';

/** Spec 04 BR-4.14 (đích Việt còn chữ Hán) và BR-4.20 (âm Hán Việt nghi ngờ). */
export default function HealthPanel({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const q = useQuery({ queryKey: ['glossary-health', bookId], queryFn: () => api.get<GlossaryHealth>(`/books/${bookId}/glossary/health`), enabled: open });
  const done = (msg: string) => {
    toast(msg);
    void qc.invalidateQueries({ queryKey: ['glossary-health', bookId] });
    void qc.invalidateQueries({ queryKey: ['glossary', bookId] });
  };
  const patch = useMutation({
    mutationFn: ({ id, dst_vi }: { id: string; dst_vi: string }) => api.patch(`/glossary/${id}`, { dst_vi }),
    onSuccess: () => done('Đã lưu thuật ngữ'),
    onError: (e) => toast(errorMessage(e)),
  });
  const fix = useMutation({
    mutationFn: ({ char, readings }: { char: string; readings: string[] }) => api.put(`/hanviet/${encodeURIComponent(char)}`, { readings }),
    onSuccess: () => done('Đã lưu âm Hán Việt'),
    onError: (e) => toast(errorMessage(e)),
  });
  const h = q.data;
  return (
    <div className="panel" style={{ gap: 8 }}>
      <div className="row">
        <h2 className="grow">Kiểm tra glossary</h2>
        <button className="btn sm" onClick={() => (open ? void q.refetch() : setOpen(true))}>{open ? 'Kiểm tra lại' : 'Kiểm tra glossary'}</button>
      </div>
      {h && (
        <>
          <h3>Đích Việt còn chữ Hán ({h.han_in_dst.length})</h3>
          {h.han_in_dst.length === 0 ? <div className="hint">Không có thuật ngữ nào.</div> : h.han_in_dst.map((t) => (
            <div key={t.id} className="row">
              <span className="zh" style={{ minWidth: 80 }}>{t.src_zh}</span>
              <input type="text" defaultValue={t.dst_vi} key={`${t.id}:${t.dst_vi}`} aria-label={`Sửa đích Việt của ${t.src_zh}`}
                onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== t.dst_vi) patch.mutate({ id: t.id, dst_vi: v }); }} />
            </div>
          ))}
          <h3>Âm Hán Việt nghi ngờ ({h.suspicious_readings.length})</h3>
          <div className="hint">Chữ chỉ có âm do AI sinh, nhưng thuật ngữ đã duyệt đọc khác. Sửa ở đây sẽ ghi âm với nguồn "manual".</div>
          {h.suspicious_readings.map((s) => {
            const value = drafts[s.char] ?? [s.proposed, ...s.readings].join(', ');
            return (
              <div key={s.char} className="row" style={{ flexWrap: 'wrap' }}>
                <span className="zh">{s.char}</span>
                <span className="hint grow">AI: {s.readings.join(', ')} · {s.terms.map((t) => `${t.src_zh} = ${t.dst_vi}`).join('; ')}</span>
                <input type="text" value={value} aria-label={`Âm Hán Việt của ${s.char}`} style={{ maxWidth: 200 }}
                  onChange={(e) => setDrafts({ ...drafts, [s.char]: e.target.value })} />
                <button className="btn sm" aria-label={`Lưu âm ${s.char}`} disabled={fix.isPending}
                  onClick={() => fix.mutate({ char: s.char, readings: value.split(/[,\s]+/).filter(Boolean) })}>Lưu</button>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}
