import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { ChapterDetail, Route, RunConfig, RestoreResult, ReviewFix, Revision } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';
import { FIX_TYPE_LABEL, fmtDateTime } from '../../lib/format';
import NotesPanel from './NotesPanel';

const ROUTE_LABEL: Record<Route, string> = { ancient: 'Cổ trang', modern: 'Hiện đại', mixed: 'Hỗn hợp', unknown: 'Không rõ' };

export default function ChapterSide({ detail, busy, fixes = [], onDecide, onJump }: {
  detail: ChapterDetail; busy: boolean; fixes?: ReviewFix[];
  onDecide?: (ids: string[], action: 'apply' | 'reject') => void; onJump?: (idx: number) => void;
}) {
  const ch = detail.chapter;
  const qc = useQueryClient();
  const toast = useToast();
  const [restoring, setRestoring] = useState<Revision | null>(null);
  const useBook = ch.run_config_override === null;
  const effective = ch.run_config;
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['chapter'] });
    void qc.invalidateQueries({ queryKey: ['revisions'] });
  };

  const revisions = useQuery({ queryKey: ['revisions', ch.id], queryFn: () => api.get<Revision[]>(`/chapters/${ch.id}/revisions`) });

  // Bản nháp cục bộ: slider/chunk phản hồi ngay, PUT gộp (debounce) và tuần tự.
  type Override = { beam: number; chunk_mode: RunConfig['chunk_mode'] };
  const [draft, setDraft] = useState<Override>({ beam: effective.beam, chunk_mode: effective.chunk_mode });
  const draftRef = useRef(draft);
  const timer = useRef<number>();
  const timerSet = useRef(false);
  const inflight = useRef(false);
  const queued = useRef<{ body: Override | null } | null>(null);
  const send = (body: Override | null) => {
    if (inflight.current) { queued.current = { body }; return; }
    inflight.current = true;
    api.put(`/chapters/${ch.id}/run-config`, body)
      .then(refresh, (e) => toast(errorMessage(e)))
      .finally(() => {
        inflight.current = false;
        const q = queued.current;
        queued.current = null;
        if (q) send(q.body);
      });
  };
  const sendNow = () => { window.clearTimeout(timer.current); timerSet.current = false; send(draftRef.current); };
  const update = (patch: Partial<Override>) => {
    draftRef.current = { ...draftRef.current, ...patch };
    setDraft(draftRef.current);
    window.clearTimeout(timer.current);
    timerSet.current = true;
    timer.current = window.setTimeout(sendNow, 400);
  };
  const toggleBook = (useBookCfg: boolean) => {
    window.clearTimeout(timer.current);
    timerSet.current = false;
    send(useBookCfg ? null : draftRef.current);
  };
  useEffect(() => {
    // Server đổi (refetch) và không còn thay đổi đang chờ/bay: đồng bộ lại nháp.
    if (timerSet.current || inflight.current) return;
    draftRef.current = { beam: effective.beam, chunk_mode: effective.chunk_mode };
    setDraft(draftRef.current);
  }, [effective.beam, effective.chunk_mode]);
  useEffect(() => () => { if (timerSet.current) { window.clearTimeout(timer.current); send(draftRef.current); } }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const setRegister = useMutation({
    mutationFn: (value: string) => api.put(`/chapters/${ch.id}/register`, { route: value || null }),
    onSuccess: refresh,
    onError: (e) => toast(errorMessage(e)),
  });
  const restore = useMutation({
    mutationFn: (rid: string) => api.post<RestoreResult>(`/chapters/${ch.id}/revisions/${rid}/restore`),
    onSuccess: (r) => {
      setRestoring(null);
      const skipped = r.skipped ? `, bỏ qua ${r.skipped} câu có bản gốc đã đổi` : '';
      // revision_id null: không có gì khác bản hiện tại, backend không tạo revision mới
      toast(r.revision_id === null ? `Bản này giống nội dung hiện tại, không có gì thay đổi${skipped}` : `Đã khôi phục ${r.restored} câu${skipped}`);
      refresh();
    },
    onError: (e) => toast(errorMessage(e)),
  });

  return (
    <div style={{ display: 'grid', gap: 12, minWidth: 0 }}>
      {fixes.length > 0 && (
        <div className="panel">
          <h2>Đề xuất sửa của DeepSeek ({fixes.length})</h2>
          <div className="row">
            <button className="btn sm primary" disabled={busy} onClick={() => onDecide?.(fixes.map((f) => f.id), 'apply')}>Áp tất cả</button>
            <button className="btn sm" disabled={busy} onClick={() => onDecide?.(fixes.map((f) => f.id), 'reject')}>Bỏ tất cả</button>
          </div>
          <div className="side-list">
            {fixes.map((f) => (
              <button key={f.id} className="it fix-link" onClick={() => onJump?.(f.segment_idx)}>
                <span>Câu {f.segment_idx} · {FIX_TYPE_LABEL[f.type] ?? f.type}</span>
                <span className="hint">tin cậy {f.confidence}{f.reason ? ` · ${f.reason}` : ''}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="panel">
        <h2>Cấu hình chương này</h2>
        <label className="check">
          <input type="checkbox" checked={useBook}
            onChange={(e) => toggleBook(e.target.checked)} />
          Dùng cấu hình của truyện
        </label>
        <div className="field">
          <label htmlFor="chBeam">Beam</label>
          <div className="range">
            <input id="chBeam" type="range" min={1} max={4} disabled={useBook} value={draft.beam}
              onChange={(e) => update({ beam: Number(e.target.value) })} />
            <output>{draft.beam}</output>
          </div>
        </div>
        <div className="field">
          <span className="lbl">Chia chunk</span>
          {useBook ? <span className="hint">{effective.chunk_mode === 'paragraph' ? 'Theo đoạn' : 'Theo câu'}</span> : (
            <Segmented ariaLabel="Chia chunk của chương" value={draft.chunk_mode}
              onChange={(v) => update({ chunk_mode: v })}
              options={[{ value: 'sentence', label: 'Theo câu' }, { value: 'paragraph', label: 'Theo đoạn' }]} />
          )}
        </div>
      </div>
      <div className="panel">
        <h2>Văn phong</h2>
        <div className="row">
          <span className="chip">{ch.register_route ? ROUTE_LABEL[ch.register_route] : 'Chưa phân loại'}</span>
          {typeof ch.register_score === 'number' && <span className="hint">{ch.register_score.toFixed(2).replace('.', ',')}</span>}
        </div>
        <div className="field">
          <label htmlFor="chRoute">Ép văn phong</label>
          <select id="chRoute" value={ch.register_override ?? ''} disabled={busy || setRegister.isPending}
            onChange={(e) => setRegister.mutate(e.target.value)}>
            <option value="">Tự động</option>
            <option value="ancient">Ép cổ trang</option>
            <option value="modern">Ép hiện đại</option>
            <option value="mixed">Ép hỗn hợp</option>
            <option value="unknown">Không áp</option>
          </select>
        </div>
      </div>
      <div className="panel">
        <h2>Glossary trong chương</h2>
        <div className="side-list">
          {(detail.glossary_terms ?? []).map((t) => (
            <div key={t.id} className="it"><span><span className="zh">{t.src_zh}</span> → {t.dst_vi}</span><span className="hint">{t.count} lần</span></div>
          ))}
          {(detail.glossary_terms ?? []).length === 0 && <span className="hint">Chưa có thuật ngữ nào khớp chương này.</span>}
        </div>
        <span className="hint">Bôi đen chữ Hán ở cột gốc để thêm thuật ngữ.</span>
      </div>
      <NotesPanel chapterId={ch.id} />
      <div className="panel">
        <h2>Lịch sử</h2>
        <div className="side-list">
          {(revisions.data ?? []).map((r) => (
            <div key={r.id} className="it">
              <span><b>{r.kind === 'machine' ? 'Máy' : 'Sửa tay'}</b> · {fmtDateTime(r.created_at)}</span>
              <span className="hint">
                {r.model_id ?? '–'}{r.beam ? ` · beam ${r.beam}` : ''} · {r.segments_changed} câu{r.note ? ` · ${r.note === 'review' ? 'áp đề xuất DeepSeek' : r.note}` : ''}
              </span>
              {r.restorable && (
                <button className="btn sm" style={{ justifySelf: 'start' }} disabled={busy} onClick={() => setRestoring(r)}>Khôi phục</button>
              )}
            </div>
          ))}
          {revisions.data?.length === 0 && <span className="hint">Chưa có lịch sử.</span>}
        </div>
      </div>
      {restoring && (
        <ConfirmDialog title="Khôi phục bản này?" onClose={() => setRestoring(null)}
          actions={[{ label: 'Khôi phục', primary: true, onClick: () => restore.mutate(restoring.id) }]}>
          <p>Nội dung hiện tại vẫn được giữ trong lịch sử. Không có gì bị xoá.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
