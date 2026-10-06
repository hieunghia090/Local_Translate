import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, errorMessage, qs } from '../../api/client';
import type { LogEntry, LogLevel, LogPage, LogSource } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';
import { fmtNum, fmtTime } from '../../lib/format';
import { useDebouncedValue } from '../../lib/hooks';
import GlossarySendStats from './GlossarySendStats';

const SOURCE_LABEL: Record<LogSource, string> = { translate: 'dịch', glossary: 'glossary', review: 'soát', system: 'hệ thống' };

function LogDetail({ id }: { id: string }) {
  const q = useQuery({ queryKey: ['log', id], queryFn: () => api.get<LogEntry>(`/logs/${id}`) });
  if (!q.data) return <div className="log-detail">Đang tải…</div>;
  const text = JSON.stringify({ params: q.data.params, detail: q.data.detail }, null, 2);
  return (
    <div className="log-detail" onClick={(e) => e.stopPropagation()}>
      <button className="btn sm" onClick={() => void navigator.clipboard?.writeText(text)}>Sao chép</button>
      {'\n'}{text}
    </div>
  );
}

export default function LogsTab({ bookId }: { bookId: string }) {
  const [params] = useSearchParams();
  const highlight = params.get('log');
  const [level, setLevel] = useState<LogLevel | 'all'>(highlight ? 'error' : 'all');
  const [source, setSource] = useState<LogSource | 'all'>('all');
  const [q, setQ] = useState('');
  const dq = useDebouncedValue(q.trim(), 300);
  const [follow, setFollow] = useState(true);
  const [openId, setOpenId] = useState<string | null>(highlight);
  const [confirmClear, setConfirmClear] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const filters = { level: level === 'all' ? undefined : level, source: source === 'all' ? undefined : source, q: dq || undefined };

  const logs = useInfiniteQuery({
    queryKey: ['logs', bookId, filters],
    queryFn: ({ pageParam }) => api.get<LogPage>(`/books/${bookId}/logs${qs({ ...filters, before: pageParam, limit: 200 })}`),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = useMemo(() => [...(logs.data?.pages.flatMap((p) => p.items) ?? [])].reverse(), [logs.data]);

  // Chỉ cuộn xuống đáy khi dòng mới nhất đổi; trang cũ chèn lên đầu thì giữ nguyên vị trí cuộn.
  const newestId = rows.length ? rows[rows.length - 1].id : null;
  useEffect(() => {
    if (follow && !openId && box.current) box.current.scrollTop = box.current.scrollHeight; // BR-5.3
  }, [newestId, follow, openId]);

  const clear = useMutation({
    mutationFn: () => api.del(`/books/${bookId}/logs`),
    onSuccess: () => { setConfirmClear(false); void qc.invalidateQueries({ queryKey: ['logs'] }); void qc.invalidateQueries({ queryKey: ['log-summary'] }); },
    onError: (e) => toast(errorMessage(e)),
  });

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <GlossarySendStats bookId={bookId} />
      <div className="lib-tools">
        <Segmented ariaLabel="Mức log" value={level} onChange={setLevel} options={[
          { value: 'all', label: 'Tất cả' }, { value: 'info', label: 'Info' }, { value: 'warn', label: 'Warn' }, { value: 'error', label: 'Error' },
        ]} />
        <select value={source} onChange={(e) => setSource(e.target.value as LogSource | 'all')} aria-label="Nguồn log" style={{ width: 'auto' }}>
          <option value="all">Mọi nguồn</option><option value="translate">Dịch</option>
          <option value="system">Hệ thống</option><option value="glossary">Glossary</option><option value="review">Soát</option>
        </select>
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm trong nội dung…" aria-label="Tìm log" style={{ maxWidth: 240 }} />
        <span className="grow" />
        <label className="check"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> Tự cuộn</label>
        <button className="btn sm" onClick={() => setConfirmClear(true)}>Xoá log</button>
        <a className="btn sm" href={`/api/v1/books/${bookId}/logs/export${qs(filters)}`} download>Xuất .jsonl</a>
      </div>
      <div className="log" ref={box}>
        {logs.hasNextPage && (
          <div style={{ padding: 6 }}><button className="btn sm" onClick={() => void logs.fetchNextPage()}>Tải thêm log cũ hơn</button></div>
        )}
        {rows.map((r) => (
          <div key={r.id} className={`log-row${r.id === highlight ? ' hl' : ''}`} onClick={() => setOpenId(openId === r.id ? null : r.id)}>
            <span>{fmtTime(r.ts)}</span>
            <span className={`lv-${r.level}`}>{r.level.toUpperCase()}</span>
            <span>{SOURCE_LABEL[r.source]}</span>
            <span>{r.model ?? ''}</span>
            <span className="msg" title={r.message}>
              {r.chapter_no !== null
                ? <Link to={`/books/${bookId}/chapters/${r.chapter_no}`} onClick={(e) => e.stopPropagation()}>{r.message}</Link>
                : r.message}
            </span>
            <span className="tok">{r.tokens_in !== null ? `${fmtNum(r.tokens_in)} → ${fmtNum(r.tokens_out ?? 0)}` : ''}</span>
            <span className="ms">{r.latency_ms !== null ? `${(r.latency_ms / 1000).toFixed(1)}s` : ''}</span>
            {openId === r.id && <LogDetail id={r.id} />}
          </div>
        ))}
        {!logs.isPending && rows.length === 0 && <div style={{ padding: 10 }} className="hint">Chưa có log khớp bộ lọc.</div>}
      </div>
      <div className="hint">Log lưu trong Postgres, giữ 30 ngày. Token với HachimiMT đếm bằng tokenizer: vào → ra.</div>
      {confirmClear && (
        <ConfirmDialog title="Xoá toàn bộ log của truyện?" onClose={() => setConfirmClear(false)}
          actions={[{ label: 'Xoá log', danger: true, onClick: () => clear.mutate() }]}>
          <p>Giữ lại một dòng ghi thời điểm xoá.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
