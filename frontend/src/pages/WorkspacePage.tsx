import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, errorMessage } from '../api/client';
import { useEvents } from '../api/events';
import type { BookDetail, BulkResult, ChapterStatus, LogSummary, QueueView, UsageView } from '../api/types';
import Banner from '../components/Banner';
import Breadcrumb from '../components/Breadcrumb';
import ConfirmDialog from '../components/ConfirmDialog';
import ProgressBar from '../components/ProgressBar';
import StatTiles from '../components/StatTiles';
import { useToast } from '../components/Toast';
import { fmtNum, fmtUsd, genreLabel } from '../lib/format';
import AddChapterDialog from './workspace/AddChapterDialog';
import ChaptersTab from './workspace/ChaptersTab';
import DeepSeekBanner from './workspace/DeepSeekBanner';
import GlossaryTab from './workspace/GlossaryTab';
import LogsTab from './workspace/LogsTab';
import QueueTab from './workspace/QueueTab';
import SettingsTab from './workspace/SettingsTab';
import ExportTab from './workspace/ExportTab';

const TABS = ['chapters', 'glossary', 'queue', 'logs', 'settings', 'export'] as const;
type Tab = (typeof TABS)[number];

export default function WorkspacePage() {
  const { bookId = '', tab: rawTab } = useParams();
  const tab: Tab = TABS.includes(rawTab as Tab) ? (rawTab as Tab) : 'chapters';
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  useEvents(bookId);
  const [adding, setAdding] = useState(false);
  const [confirmAll, setConfirmAll] = useState(false);

  useEffect(() => {
    void api.post(`/books/${bookId}/open`).catch(() => undefined); // BR-1.2
  }, [bookId]);

  const book = useQuery({ queryKey: ['book', bookId], queryFn: () => api.get<BookDetail>(`/books/${bookId}`) });
  const queue = useQuery({ queryKey: ['queue', bookId], queryFn: () => api.get<QueueView>(`/queue?book_id=${bookId}`) });
  const logSummary = useQuery({ queryKey: ['log-summary', bookId], queryFn: () => api.get<LogSummary>(`/books/${bookId}/logs/summary`) });

  const usage = useQuery({ queryKey: ['usage', bookId, ''], queryFn: () => api.get<UsageView>(`/books/${bookId}/usage`) });

  const translateAll = useMutation({
    mutationFn: () => api.post<BulkResult>(`/books/${bookId}/chapters/bulk`, { action: 'translate', filter: { status: ['todo', 'error'] } }),
    onSuccess: (r) => {
      toast(`Đã thêm ${fmtNum(r.affected)} chương vào hàng đợi`, { to: `/books/${bookId}/queue`, label: 'Xem hàng đợi' });
      void qc.invalidateQueries({ queryKey: ['chapters'] });
    },
    onError: (e) => toast(errorMessage(e)),
  });

  if (book.isError) {
    return (
      <Banner>
        <span className="grow">Không tải được truyện: {errorMessage(book.error)}</span>
        <button className="btn sm" onClick={() => navigate('/')}>Về Thư viện</button>
      </Banner>
    );
  }
  const b = book.data;
  if (!b) return <div className="skeleton" />;

  const s = b.stats;
  const untranslated = s.todo + s.error;
  const goFilter = (f: string) => navigate(`/books/${bookId}/chapters?f=${f}`);
  const startAll = () => (untranslated > 100 ? setConfirmAll(true) : translateAll.mutate());
  const title = b.title_vi || b.title_zh;
  const waiting = queue.data?.active.length ?? 0;
  const counts: Record<Tab, string> = {
    chapters: fmtNum(s.total),
    glossary: '',
    queue: fmtNum(waiting),
    logs: logSummary.data ? `${fmtNum(logSummary.data.total)}${logSummary.data.errors ? ` · ${logSummary.data.errors} lỗi` : ''}` : '',
    settings: '',
    export: '',
  };
  const tabLabel: Record<Tab, string> = { chapters: 'Chương', glossary: 'Glossary', queue: 'Hàng đợi dịch', logs: 'Console logs', settings: 'Cấu hình', export: 'Xuất bản dịch' };
  const filterOf: Partial<Record<ChapterStatus | 'done', string>> = {
    done: 'done', needs_review: 'rev', translating: 'all', todo: 'todo', error: 'err',
  };

  return (
    <section style={{ display: 'grid', gap: 16 }}>
      <Breadcrumb items={[{ label: 'Thư viện', to: '/' }, { label: title }]} />
      <div className="head">
        <div style={{ display: 'grid', gridTemplateColumns: '56px minmax(0,1fr)', gap: 14, alignItems: 'center', minWidth: 0 }}>
          <div className="cover" aria-hidden>{b.title_zh.slice(0, 1)}</div>
          <div style={{ minWidth: 0 }}>
            <h1>{title}</h1>
            <div className="hint">
              <span className="zh">{b.title_zh}</span>{b.author ? ` · ${b.author}` : ''} · {genreLabel(b.genre)} · {b.run_config.model_id} · beam {b.run_config.beam}
              {usage.data && usage.data.total.cost_usd > 0 && <> · Đã dùng {fmtUsd(usage.data.total.cost_usd)} tháng này</>}
            </div>
          </div>
        </div>
        <div className="row">
          <button className="btn" onClick={() => setAdding(true)}>＋ Thêm chương</button>
          <button className="btn primary" disabled={untranslated === 0 || translateAll.isPending} onClick={startAll}>
            ▶ Dịch tất cả chương chưa dịch
          </button>
        </div>
      </div>
      <DeepSeekBanner bookId={bookId} />
      <div className="panel" style={{ gap: 10 }}>
        <div className="row">
          <span className="lbl grow">Tiến độ</span>
          <span className="hint mono">{b.progress_pct}% · {fmtNum(b.total_chars)} chữ Hán</span>
        </div>
        <ProgressBar stats={s} height={10} />
        <StatTiles items={[
          { label: 'Tổng', value: fmtNum(s.total), onClick: () => goFilter('all') },
          { label: 'Đã dịch', value: fmtNum(s.translated + s.reviewed), onClick: () => goFilter(filterOf.done!) },
          { label: 'Cần soát', value: fmtNum(s.needs_review), onClick: () => goFilter('rev') },
          { label: 'Đang dịch', value: fmtNum(s.translating + s.queued), onClick: () => navigate(`/books/${bookId}/queue`) },
          { label: 'Chưa dịch', value: fmtNum(s.todo), onClick: () => goFilter('todo') },
          { label: 'Lỗi', value: fmtNum(s.error), onClick: () => goFilter('err') },
        ]} />
      </div>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} role="tab" aria-selected={t === tab} onClick={() => navigate(`/books/${bookId}/${t}`)}>
            {tabLabel[t]}{counts[t] && <span className="count">{counts[t]}</span>}
          </button>
        ))}
      </div>
      {tab === 'chapters' && <ChaptersTab book={b} />}
      {tab === 'glossary' && <GlossaryTab bookId={bookId} />}
      {tab === 'queue' && <QueueTab bookId={bookId} />}
      {tab === 'logs' && <LogsTab bookId={bookId} />}
      {tab === 'settings' && <SettingsTab book={b} />}
      {tab === 'export' && <ExportTab book={b} />}
      {adding && <AddChapterDialog bookId={bookId} total={s.total} onClose={() => setAdding(false)} />}
      {confirmAll && (
        <ConfirmDialog title={`Dịch ${fmtNum(untranslated)} chương?`} onClose={() => setConfirmAll(false)}
          actions={[{ label: 'Thêm vào hàng đợi', primary: true, onClick: () => { setConfirmAll(false); translateAll.mutate(); } }]}>
          <p className="hint">Các chương được dịch lần lượt theo số chương. Có thể tạm dừng ở tab Hàng đợi.</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
