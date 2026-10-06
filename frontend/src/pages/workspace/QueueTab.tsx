import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { JobView, QueueView } from '../../api/types';
import { useToast } from '../../components/Toast';
import { fmtDuration, fmtNum } from '../../lib/format';

const JOB_STATUS: Record<JobView['status'], string> = {
  queued: 'Đang chờ', running: 'Đang dịch', paused: 'Tạm dừng', done: 'Xong', failed: 'Lỗi', cancelled: 'Đã huỷ',
};

const REASON_LABEL: Record<string, string> = {
  auth: 'key bị từ chối / hết số dư', no_key: 'chưa có key', token_anomaly: 'token ra bất thường', failures: '5 chương lỗi liên tiếp',
};

export default function QueueTab({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const mine = useQuery({ queryKey: ['queue', bookId], queryFn: () => api.get<QueueView>(`/queue?book_id=${bookId}`), refetchInterval: 5_000 });
  const global = useQuery({ queryKey: ['queue', 'all'], queryFn: () => api.get<QueueView>('/queue'), refetchInterval: 5_000 });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['queue'] });
  const onError = (e: unknown) => toast(errorMessage(e));

  const pause = useMutation({ mutationFn: (paused: boolean) => api.post(`/queue/${paused ? 'pause' : 'resume'}`, { engine: 'ct2' }), onSuccess: refresh, onError });
  const pauseDs = useMutation({ mutationFn: (paused: boolean) => api.post(`/queue/${paused ? 'pause' : 'resume'}`, { engine: 'deepseek' }), onSuccess: refresh, onError });
  const cancel = useMutation({ mutationFn: (id: string) => api.del(`/jobs/${id}`), onSuccess: refresh, onError });
  const move = useMutation({ mutationFn: ({ id, position }: { id: string; position: number }) => api.patch(`/jobs/${id}`, { position }), onSuccess: refresh, onError });

  const data = mine.data;
  const waitingGlobal = (global.data?.active ?? []).filter((j) => j.engine === 'ct2' && j.status !== 'running');
  const indexOf = (id: string) => waitingGlobal.findIndex((j) => j.id === id);
  const active = data?.active ?? [];
  const paused = data?.paused.ct2 ?? false;
  const dsPaused = data?.paused.deepseek ?? false;
  const dsReason = data?.paused_reason?.deepseek ?? null;
  const waiting = active.filter((j) => j.status !== 'running');

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="row">
        <span className="hint grow">
          {active.length === 0 ? 'Hàng đợi trống. Chọn chương rồi bấm Dịch.'
            : `${fmtNum(waiting.length)} chương đang chờ${data?.eta_seconds ? ` · ước tính ~${fmtDuration(data.eta_seconds)}` : ''}`}
          {data?.running_elsewhere && <> · Đang dịch truyện <Link to={`/books/${data.running_elsewhere.book_id}/queue`}>{data.running_elsewhere.title}</Link></>}
        </span>
        <button className="btn sm" onClick={() => pause.mutate(!paused)}>{paused ? '▶ Tiếp tục worker CPU' : '⏸ Tạm dừng worker CPU'}</button>
        <button className="btn sm" onClick={() => pauseDs.mutate(!dsPaused)}>{dsPaused ? '▶ Tiếp tục pool DeepSeek' : '⏸ Tạm dừng pool DeepSeek'}</button>
        <button className="btn sm" disabled={waiting.length === 0}
          onClick={() => { for (const j of waiting) cancel.mutate(j.id); }}>Huỷ tất cả</button>
      </div>
      {paused && <div className="banner warn">Worker CPU đang tạm dừng. Job đang chạy sẽ dừng sau bước hiện tại.</div>}
      {dsPaused && <div className="banner warn">Pool DeepSeek đang tạm dừng{dsReason ? ` (${REASON_LABEL[dsReason] ?? dsReason})` : ''}. Job DeepSeek đang chạy sẽ dừng sau request hiện tại.</div>}
      <div className="tbl">
        <table>
          <thead><tr><th>#</th><th>Chương</th><th>Engine</th><th>Tiến độ</th><th>Trạng thái</th><th /></tr></thead>
          <tbody>
            {active.map((j, i) => (
              <tr key={j.id}>
                <td className="num">{i + 1}</td>
                <td>{j.chapter_no !== null ? <Link to={`/books/${bookId}/chapters/${j.chapter_no}`}>{j.chapter_title}</Link> : '–'}</td>
                <td><span className="mbadge">{j.engine === 'ct2' ? 'HachimiMT' : j.kind === 'review' ? 'DeepSeek · soát' : j.kind === 'ai_extract' ? 'DeepSeek · trích glossary' : 'DeepSeek'}</span></td>
                <td style={{ minWidth: 120 }}>
                  <div className="bar"><i className="b-run" style={{ width: `${j.progress}%` }} /></div>
                  <span className="hint mono">{j.progress}%</span>
                </td>
                <td>{JOB_STATUS[j.status]}</td>
                <td>
                  <div className="row" style={{ flexWrap: 'nowrap' }}>
                    <button className="btn sm" aria-label="Lên" disabled={j.status === 'running' || indexOf(j.id) <= 0}
                      onClick={() => move.mutate({ id: j.id, position: indexOf(j.id) - 1 })}>↑</button>
                    <button className="btn sm" aria-label="Xuống" disabled={j.status === 'running' || indexOf(j.id) < 0 || indexOf(j.id) >= waitingGlobal.length - 1}
                      onClick={() => move.mutate({ id: j.id, position: indexOf(j.id) + 1 })}>↓</button>
                    <button className="btn sm" aria-label="Huỷ" onClick={() => cancel.mutate(j.id)}>✕</button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Đã xong gần đây</h2>
      <div className="tbl">
        <table>
          <thead><tr><th>Chương</th><th>Kết quả</th><th style={{ textAlign: 'right' }}>Thời gian</th><th style={{ textAlign: 'right' }}>Token vào → ra</th></tr></thead>
          <tbody>
            {(data?.recent ?? []).map((j) => (
              <tr key={j.id}>
                <td>{j.chapter_title ?? '–'}</td>
                <td>{JOB_STATUS[j.status]}{j.error && <span className="err-text"> · {j.error}</span>}</td>
                <td className="num">{j.duration_ms !== null ? `${(j.duration_ms / 1000).toFixed(1)}s` : '–'}</td>
                <td className="num">{fmtNum(j.tokens_in)} → {fmtNum(j.tokens_out)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="hint">Worker CPU chạy HachimiMT từng chương một. Pool DeepSeek chạy song song theo "Số chương song song" trong Cấu hình.</div>
    </div>
  );
}
