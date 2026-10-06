import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api, errorMessage, qs } from '../../api/client';
import type { BookDetail, ExportFormat, ExportJob, ExportPreview, ExportScope } from '../../api/types';
import Banner from '../../components/Banner';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';
import { fmtNum } from '../../lib/format';

const SCOPES: { value: ExportScope; label: string }[] = [
  { value: 'translated', label: 'Chương đã dịch' },
  { value: 'reviewed', label: 'Chỉ chương đã soát' },
  { value: 'range', label: 'Khoảng chương' },
];
const FORMATS: { value: ExportFormat; label: string }[] = [
  { value: 'txt', label: '.txt gộp' },
  { value: 'zip', label: '.txt mỗi chương (.zip)' },
  { value: 'epub', label: '.epub' },
  { value: 'bilingual', label: 'Song ngữ (.txt)' },
];

/** BR-3.16: xuất xong thì trình duyệt tự tải file về. */
function startDownload(url: string, name: string | null) {
  const a = document.createElement('a');
  a.href = url;
  a.download = name ?? '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export default function ExportTab({ book }: { book: BookDetail }) {
  const toast = useToast();
  const [scope, setScope] = useState<ExportScope>('translated');
  const [fromNo, setFromNo] = useState('1');
  const [toNo, setToNo] = useState(String(Math.max(book.stats.total, 1)));
  const [format, setFormat] = useState<ExportFormat>('txt');
  const [includeTitles, setIncludeTitles] = useState(true);
  const [keepMeta, setKeepMeta] = useState(false);
  const [exportId, setExportId] = useState<string | null>(null);
  const downloaded = useRef<string | null>(null);

  const isRange = scope === 'range';
  const from = Number(fromNo);
  const to = Number(toNo);
  const rangeOk = !isRange || (Number.isInteger(from) && Number.isInteger(to) && from >= 1 && to >= from);
  const range = isRange ? { from_no: from, to_no: to } : {};

  const preview = useQuery({
    queryKey: ['export-preview', book.id, scope, isRange ? from : null, isRange ? to : null, book.stats],
    queryFn: () => api.get<ExportPreview>(`/books/${book.id}/exports/preview${qs({ scope, ...range })}`),
    enabled: rangeOk,
    placeholderData: keepPreviousData,
  });
  const job = useQuery({
    queryKey: ['export', exportId],
    queryFn: () => api.get<ExportJob>(`/exports/${exportId}`),
    enabled: exportId !== null,
    refetchInterval: (q) => (q.state.status === 'error' || (q.state.data && q.state.data.status !== 'running') ? false : 1000),
  });
  const start = useMutation({
    mutationFn: () => api.post<{ export_id: string }>(`/books/${book.id}/exports`, {
      scope, ...range, format, include_titles: includeTitles, keep_meta: keepMeta,
    }),
    onSuccess: (r) => setExportId(r.export_id),
    onError: (e) => toast(errorMessage(e)),
  });

  const j = job.data;
  useEffect(() => {
    if (j?.status === 'done' && j.download_url && downloaded.current !== j.id) {
      downloaded.current = j.id;
      startDownload(j.download_url, j.file_name);
    }
  }, [j]);

  const p = preview.data;
  const busy = start.isPending || (exportId !== null && !job.isError && (!j || j.status === 'running'));

  return (
    <div style={{ display: 'grid', gap: 12, maxWidth: 720 }}>
      <div className="panel">
        <h2>Phạm vi</h2>
        <Segmented ariaLabel="Phạm vi xuất" value={scope} onChange={setScope} options={SCOPES} />
        {isRange && (
          <div className="two">
            <div className="field">
              <label htmlFor="exFrom">Từ chương</label>
              <input id="exFrom" type="number" min={1} value={fromNo} onChange={(e) => setFromNo(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="exTo">Đến chương</label>
              <input id="exTo" type="number" min={1} value={toNo} onChange={(e) => setToNo(e.target.value)} />
            </div>
          </div>
        )}
        {!rangeOk && <div className="hint" role="alert">Khoảng chương không hợp lệ.</div>}
      </div>
      <div className="panel">
        <h2>Định dạng</h2>
        <Segmented ariaLabel="Định dạng xuất" value={format} onChange={setFormat} options={FORMATS} />
        <label className="check">
          <input type="checkbox" checked={includeTitles} onChange={(e) => setIncludeTitles(e.target.checked)} /> Chèn tiêu đề chương tiếng Việt
        </label>
        <label className="check">
          <input type="checkbox" checked={keepMeta} onChange={(e) => setKeepMeta(e.target.checked)} /> Giữ dòng meta (vd. “Nguồn: …”)
        </label>
        <div className="hint">
          Chương chưa dịch xong hoặc nằm ngoài phạm vi sẽ bị bỏ qua. File được lưu ở <span className="mono">{book.source_dir}/exports</span>.
        </div>
      </div>
      <div className="row">
        <span className="grow" aria-live="polite">{rangeOk && p && !preview.isError ? `Sẽ xuất ${fmtNum(p.chapters)} chương, bỏ qua ${fmtNum(p.skipped)}` : ''}</span>
        <button className="btn primary" disabled={!rangeOk || !p || p.chapters === 0 || busy} onClick={() => start.mutate()}>⤓ Xuất file</button>
      </div>
      {job.isError && <Banner>Không theo dõi được lần xuất: {errorMessage(job.error)}</Banner>}
      {j?.status === 'running' && !job.isError && <Banner kind="info">Đang xuất {fmtNum(j.chapters)} chương…</Banner>}
      {j?.status === 'done' && j.download_url && (
        <Banner kind="info">
          <span className="grow">Đã xuất {fmtNum(j.chapters)} chương: <span className="mono">{j.file_name}</span></span>
          <a className="btn sm" href={j.download_url} download={j.file_name ?? undefined}>Tải lại</a>
        </Banner>
      )}
      {j?.status === 'failed' && <Banner>Xuất file lỗi: {j.error}</Banner>}
    </div>
  );
}
