import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { api, ApiError, errorMessage } from '../api/client';
import { useEvents } from '../api/events';
import type { BookDetail, ChapterCompare, ChapterDetail, ReviewFix, SegmentPatchResult } from '../api/types';
import Banner from '../components/Banner';
import Breadcrumb from '../components/Breadcrumb';
import ConfirmDialog from '../components/ConfirmDialog';
import Segmented from '../components/Segmented';
import StatusChip from '../components/StatusChip';
import { useToast } from '../components/Toast';
import { BUSY_STATUSES } from '../lib/format';
import { saveReaderView, storedReaderView, type ReaderView } from '../lib/readerView';
import AddTermDialog from './chapter/AddTermDialog';
import ChapterSide from './chapter/ChapterSide';
import CompareRow from './chapter/CompareRow';
import SegmentRow from './chapter/SegmentRow';
import VersionRow from './chapter/VersionRow';

const NO_COMPARE: ChapterCompare = { mt: 0, ai: 0, both: 0, differ: 0 };

export default function ChapterPage() {
  const { bookId = '', no = '1' } = useParams();
  const n = Number(no);
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  useEvents(bookId);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const [view, setViewState] = useState<ReaderView>(storedReaderView);
  const setView = useCallback((v: ReaderView) => { setViewState(v); saveReaderView(v); }, []);
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [curDiff, setCurDiff] = useState<number | null>(null);
  const [confirmKeep, setConfirmKeep] = useState(false);
  const [pick, setPick] = useState<{ text: string; x: number; y: number } | null>(null);
  const [adding, setAdding] = useState<string | null>(null);
  const [askRetranslate, setAskRetranslate] = useState(false);
  const [engineMenu, setEngineMenu] = useState(false);
  const [engineChoice, setEngineChoice] = useState<'ct2' | 'deepseek' | undefined>(undefined);

  const book = useQuery({ queryKey: ['book', bookId], queryFn: () => api.get<BookDetail>(`/books/${bookId}`) });
  const key = ['chapter', bookId, n];
  const detail = useQuery({
    queryKey: key,
    queryFn: () => api.get<ChapterDetail>(`/books/${bookId}/chapters/by-no/${n}`),
    refetchInterval: (q) => (q.state.data?.job ? 2_000 : false),
  });
  const d = detail.data;
  const ch = d?.chapter;
  const busy = ch ? BUSY_STATUSES.includes(ch.status) : false;
  const reviewable = ch?.status === 'translated' || ch?.status === 'needs_review';
  const translated = d?.segments.some((s) => !s.is_meta && s.dst !== null) ?? false;
  const fixes = useQuery({
    queryKey: ['review-fixes', ch?.id],
    queryFn: () => api.get<{ items: ReviewFix[] }>(`/chapters/${ch!.id}/review-fixes?status=pending`),
    enabled: !!ch,
  });
  const pendingFixes = useMemo(() => fixes.data?.items ?? [], [fixes.data]);
  const fixesByIdx = useMemo(() => {
    const m = new Map<number, ReviewFix[]>();
    for (const f of pendingFixes) m.set(f.segment_idx, [...(m.get(f.segment_idx) ?? []), f]);
    return m;
  }, [pendingFixes]);
  const canMark = reviewable && pendingFixes.length === 0; // BR-6.5b
  const isDeepSeek = (ch?.model_id ?? '').startsWith('deepseek');
  const canReview = translated && !busy && !!ch?.model_id && !isDeepSeek
    && ['translated', 'needs_review', 'reviewed'].includes(ch?.status ?? ''); // 08 mục 5.3, BR-6.5a

  const termNames = useMemo(
    () => Object.fromEntries((d?.glossary_terms ?? []).map((t) => [t.id, t.dst_vi])),
    [d?.glossary_terms],
  );

  const flushers = useRef(new Map<number, () => Promise<unknown>>());
  const inflight = useRef(new Set<Promise<unknown>>());
  const registerFlush = useCallback((idx: number, fn: (() => Promise<unknown>) | null) => {
    if (fn) flushers.current.set(idx, fn); else flushers.current.delete(idx);
  }, []);
  /** Gửi hết bản sửa đang chờ và đợi mọi lần lưu đang bay xong. */
  const flushPending = useCallback(async () => {
    (document.activeElement as HTMLElement | null)?.blur();
    const started = [...flushers.current.values()].map((f) => f());
    await Promise.all([...started, ...inflight.current]);
  }, []);

  const saveSegment = useCallback((idx: number, text: string, revert = false) => {
    const p = doSave(idx, text, revert);
    inflight.current.add(p);
    void p.finally(() => inflight.current.delete(p));
    return p;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ch?.id, bookId, n]);
  const doSave = async (idx: number, text: string, revert: boolean) => {
    if (!ch) return;
    try {
      const r = await api.patch<SegmentPatchResult>(`/segments/${ch.id}/${idx}`, revert ? { revert: true } : { dst: text });
      qc.setQueryData<ChapterDetail>(key, (old) => old && {
        ...old,
        chapter: { ...old.chapter, status: r.chapter.status },
        segments: old.segments.map((s) => (s.idx === idx ? r.segment : s)),
      });
      void qc.invalidateQueries({ queryKey: ['revisions'] });
      void qc.invalidateQueries({ queryKey: ['chapters'] });
    } catch (e) {
      toast(`Không lưu được câu ${idx}: ${errorMessage(e)}`);
    }
  };

  /** "Dùng bản này" (BR-6.14): đi đúng đường lưu của sửa tay, nên có revision manual và các bảo vệ của G5. */
  const applyVersion = useCallback(async (idx: number, text: string) => {
    // Đợi các lần lưu đang bay (vd. bản sửa dở được gửi lúc SegmentRow unmount) để chúng không đè bản vừa chọn.
    await Promise.all([...inflight.current]);
    await saveSegment(idx, text);
  }, [saveSegment]);

  const translate = useMutation({
    mutationFn: ({ keep, engine }: { keep?: boolean; engine?: 'ct2' | 'deepseek' }) => api.post(`/chapters/${ch!.id}/translate`, {
      priority: true, ...(keep === undefined ? {} : { keep_manual_edits: keep }), ...(engine ? { engine } : {}),
    }),
    onSuccess: () => { setConfirmKeep(false); toast('Đã đưa chương lên đầu hàng đợi'); void detail.refetch(); },
    onError: (e, v) => {
      if (e instanceof ApiError && e.code === 'CONFIRM_MANUAL_EDITS') { setEngineChoice(v.engine); setConfirmKeep(true); }
      else toast(errorMessage(e));
    },
  });
  const aiReview = useMutation({
    mutationFn: async () => { await flushPending(); return api.post(`/chapters/${ch!.id}/ai-review`); },
    onSuccess: () => { toast('Đã đưa chương vào hàng đợi soát DeepSeek'); void detail.refetch(); },
    onError: (e) => toast(errorMessage(e)),
  });
  const decide = useMutation({
    mutationFn: async ({ ids, action }: { ids: string[]; action: 'apply' | 'reject' }) => {
      await flushPending();
      return api.post<{ applied?: number; stale?: number; rejected?: number }>(`/review-fixes/${action}`, { ids });
    },
    onSuccess: (r, v) => {
      toast(v.action === 'apply'
        ? (r.stale ? `Đã áp ${r.applied} đề xuất, bỏ ${r.stale} vì câu đã đổi` : `Đã áp ${r.applied} đề xuất`)
        : `Đã bỏ ${r.rejected} đề xuất`);
      void qc.invalidateQueries({ queryKey: ['review-fixes'] });
      void qc.invalidateQueries({ queryKey: ['revisions'] });
      void detail.refetch();
    },
    onError: (e) => toast(errorMessage(e)),
  });
  const decideMutate = decide.mutate;
  const onDecide = useCallback((ids: string[], action: 'apply' | 'reject') => decideMutate({ ids, action }), [decideMutate]);
  const jumpTo = useCallback((idx: number) => {
    document.querySelector<HTMLElement>(`[data-row="${idx}"]`)?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  }, []);
  const shown = useMemo(() => {
    let list = d?.segments ?? [];
    if (onlyFlagged) list = list.filter((x) => x.flags.length > 0);
    if (view === 'compare' && onlyDiff) list = list.filter((x) => x.mt_ai_differ === true);
    return list;
  }, [d?.segments, onlyFlagged, onlyDiff, view]);
  const diffIdx = useMemo(() => shown.filter((x) => x.mt_ai_differ === true).map((x) => x.idx), [shown]);
  const goDiff = useCallback((dir: 1 | -1) => {
    if (!diffIdx.length) return;
    const pos = curDiff === null ? -1 : diffIdx.indexOf(curDiff);
    const next = pos < 0 ? (dir > 0 ? 0 : diffIdx.length - 1) : Math.min(diffIdx.length - 1, Math.max(0, pos + dir));
    setCurDiff(diffIdx[next]);
    jumpTo(diffIdx[next]);
  }, [diffIdx, curDiff, jumpTo]);
  useEffect(() => { setCurDiff(null); }, [ch?.id]);
  const markReviewed = useMutation({
    mutationFn: async () => { await flushPending(); return api.post(`/chapters/${ch!.id}/mark-reviewed`); },
    onSuccess: () => { toast('Đã đánh dấu đã soát'); void detail.refetch(); void qc.invalidateQueries({ queryKey: ['book'] }); },
    onError: (e) => toast(errorMessage(e)),
  });
  const rename = useMutation({
    mutationFn: (title: string) => api.patch(`/chapters/${ch!.id}`, { title_vi: title }),
    onSuccess: () => void detail.refetch(),
    onError: (e) => toast(errorMessage(e)),
  });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && e.shiftKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        // Alt+Shift+↑/↓: câu khác nhau (chỉ ở So sánh). Không bao giờ rơi xuống nhánh Alt+↑/↓ chuyển câu đang sửa.
        const active = document.activeElement as HTMLElement | null;
        // Đang gõ ở ô nhập (tiêu đề, tìm kiếm…): không cướp phím, giống nhánh Alt+↑/↓.
        const typing = !!active && active !== document.body && (active.matches('input:not([type=checkbox]):not([type=radio]), textarea, select') || active.isContentEditable);
        if (view === 'compare' && !typing) { e.preventDefault(); goDiff(e.key === 'ArrowDown' ? 1 : -1); }
        return;
      }
      if (e.altKey && e.key === 'ArrowLeft' && d?.prev_no) { e.preventDefault(); (document.activeElement as HTMLElement | null)?.blur(); navigate(`/books/${bookId}/chapters/${d.prev_no}`); }
      if (e.altKey && e.key === 'ArrowRight' && d?.next_no) { e.preventDefault(); (document.activeElement as HTMLElement | null)?.blur(); navigate(`/books/${bookId}/chapters/${d.next_no}`); }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && canMark && !markReviewed.isPending) { e.preventDefault(); markReviewed.mutate(); }
      if (e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) {
        const cells = [...document.querySelectorAll<HTMLElement>('.dst[contenteditable="true"]')];
        const active = document.activeElement as HTMLElement | null;
        const i = cells.indexOf(active as HTMLElement);
        // Đang gõ ở ô khác (tiêu đề, tìm kiếm…): không cướp focus.
        const editableElsewhere = i < 0 && !!active && active !== document.body
          && (active.matches('input, textarea, select') || active.isContentEditable);
        if (editableElsewhere) return;
        const up = e.key === 'ArrowUp';
        // Chưa chọn ô nào: Alt+↓ vào câu đầu, Alt+↑ vào câu cuối.
        const next = i < 0 ? (up ? cells[cells.length - 1] : cells[0]) : cells[up ? i - 1 : i + 1];
        if (next) { e.preventDefault(); next.focus(); }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [d?.prev_no, d?.next_no, bookId, navigate, canMark, markReviewed, view, goDiff]);

  if (detail.isError) {
    return <Banner><span className="grow">Không tải được chương: {errorMessage(detail.error)}</span>
      <button className="btn sm" onClick={() => navigate(`/books/${bookId}`)}>Về workspace</button></Banner>;
  }
  if (!d || !ch) return <div className="skeleton" />;

  const segments = shown;
  const cmp = ch.compare ?? NO_COMPARE;
  const canUse = !busy && translated;
  const bookTitle = book.data ? book.data.title_vi || book.data.title_zh : '…';

  return (
    <section style={{ display: 'grid', gap: 16 }}>
      <Breadcrumb items={[{ label: 'Thư viện', to: '/' }, { label: bookTitle, to: `/books/${bookId}` }, { label: `Chương ${ch.no}` }]} />
      <div className="head">
        <div style={{ minWidth: 0, flex: 1 }}>
          <input type="text" aria-label="Tiêu đề tiếng Việt" defaultValue={ch.title_vi ?? ''} key={ch.title_vi ?? ''}
            style={{ fontSize: 20, fontWeight: 600, border: 0, padding: '2px 0', background: 'transparent' }}
            onBlur={(e) => { if (e.target.value.trim() !== (ch.title_vi ?? '')) rename.mutate(e.target.value.trim()); }} />
          <div className="hint zh">{ch.title_zh}</div>
          <div className="hint">Phím tắt: Alt+↑/↓ câu trước/sau · Alt+←/→ chương trước/sau · Ctrl/⌘+Enter đánh dấu đã soát · Alt+Shift+↑/↓ câu khác nhau (So sánh)</div>
        </div>
        <div className="row">
          <button className="btn" disabled={!d.prev_no} onClick={() => navigate(`/books/${bookId}/chapters/${d.prev_no}`)}>← Chương trước</button>
          <button className="btn" disabled={!d.next_no} onClick={() => navigate(`/books/${bookId}/chapters/${d.next_no}`)}>Chương sau →</button>
        </div>
      </div>
      {d.source_missing && <Banner>Không đọc được file bản gốc của chương này.</Banner>}
      <div className="tr-grid">
        <div className="panel">
          <div className="row">
            <StatusChip status={ch.status} />
            {ch.model_id && <span className="mbadge">{ch.model_id}</span>}
            <Segmented ariaLabel="Chế độ xem" value={view} onChange={setView} options={[
              { value: 'main', label: 'Bản chính' }, { value: 'mt', label: `Hachimi (${cmp.mt})` },
              { value: 'ai', label: `AI (${cmp.ai})` }, { value: 'compare', label: 'So sánh' },
            ]} />
            <span className="grow" />
            <label className="check"><input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} /> Chỉ hiện câu có cờ</label>
          </div>
          {view === 'compare' && (
            <div className="row" role="toolbar" aria-label="So sánh Hachimi và AI">
              <span className="hint">{cmp.both ? `${cmp.differ}/${cmp.both} câu khác nhau` : 'Chưa có đủ hai bản để so sánh'}</span>
              <span className="cmp-legend"><span className="d-mt">chỉ Hachimi</span><span className="d-ai">chỉ AI</span></span>
              <span className="grow" />
              <label className="check"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> Chỉ câu khác nhau</label>
              <button className="btn sm" disabled={!diffIdx.length} onClick={() => goDiff(-1)}>↑ Câu khác trước</button>
              <button className="btn sm" disabled={!diffIdx.length} onClick={() => goDiff(1)}>↓ Câu khác sau</button>
            </div>
          )}
          {d.job && (
            <div style={{ display: 'grid', gap: 4 }}>
              <div className="bar"><i className="b-run" style={{ width: `${d.job.progress}%` }} /></div>
              <span className="hint">{d.job.status === 'running' ? `Đang dịch… ${d.job.progress}%` : 'Đang chờ trong hàng đợi'}</span>
            </div>
          )}
          <div className="cmp" onMouseUp={() => {
            const sel = window.getSelection();
            const text = sel?.toString().trim() ?? '';
            const node = sel?.anchorNode?.parentElement?.closest('.src');
            if (!sel || !node || !/^[㐀-鿿豈-﫿·]{1,20}$/.test(text)) { setPick(null); return; }
            const rect = sel.getRangeAt(0).getBoundingClientRect();
            setPick({ text, x: rect.left, y: rect.bottom + 6 });
          }}>
            {view === 'compare'
              ? <div className="cmp-row head vs"><span className="n">#</span><span>Bản gốc</span><span>Hachimi · AI</span></div>
              : <div className="cmp-row head"><span className="n">#</span><span>Bản gốc</span>
                  <span>{view === 'main' ? 'Bản dịch' : view === 'mt' ? 'Bản Hachimi' : 'Bản AI'}</span></div>}
            {segments.map((s) => (view === 'main' ? (
              <SegmentRow key={`${ch.id}:${s.idx}`} seg={s} editable={!busy && translated}
                registerFlush={registerFlush} terms={termNames} onSave={(idx, text) => saveSegment(idx, text)} onRevert={(idx) => void saveSegment(idx, '', true)}
                fixes={fixesByIdx.get(s.idx)} onDecide={onDecide} busy={busy} />
            ) : view === 'compare' ? (
              <CompareRow key={`${ch.id}:c:${s.idx}`} seg={s} current={curDiff === s.idx} canUse={canUse} onUse={applyVersion} />
            ) : (
              <VersionRow key={`${ch.id}:${view}:${s.idx}`} seg={s} engine={view} />
            )))}
          </div>
          <div className="row">
            <span className="hint grow">{busy ? 'Chương đang dịch, chưa sửa được.'
              : view === 'compare' ? 'Bấm "Dùng bản này" để đặt bản chính cho câu.'
              : view !== 'main' ? 'Chế độ chỉ đọc. Chọn "Bản chính" để sửa.'
              : translated ? 'Bấm vào câu dịch để sửa. Tự lưu khi rời ô.' : ''}</span>
            {canReview && (
              <button className="btn" disabled={aiReview.isPending} onClick={() => aiReview.mutate()}>✦ Soát bằng DeepSeek</button>
            )}
            <span className="dd">
              <button className="btn" disabled={busy || translate.isPending} onClick={() => translate.mutate({})}>
                {ch.status === 'todo' ? '▶ Dịch chương' : '↻ Dịch lại chương'}
              </button>
              <button className="btn" aria-label="Chọn model dịch lại" disabled={busy || translate.isPending}
                onClick={() => setEngineMenu(!engineMenu)}>▾</button>
              {engineMenu && (
                <div className="menu" onMouseLeave={() => setEngineMenu(false)}>
                  <button onClick={() => { setEngineMenu(false); translate.mutate({ engine: 'ct2' }); }}>Bằng HachimiMT-60</button>
                  <button onClick={() => { setEngineMenu(false); translate.mutate({ engine: 'deepseek' }); }}>Bằng DeepSeek</button>
                </div>
              )}
            </span>
            <button className="btn primary" disabled={!canMark || markReviewed.isPending}
              title={pendingFixes.length ? 'Còn đề xuất của DeepSeek chưa duyệt' : undefined}
              onClick={() => markReviewed.mutate()}>✓ Đánh dấu đã soát</button>
          </div>
        </div>
        <ChapterSide key={ch.id} detail={d} busy={busy} fixes={pendingFixes} onDecide={onDecide} onJump={jumpTo} />
      </div>
      {pick && (
        <div className="sel-pop" style={{ left: Math.min(pick.x, window.innerWidth - 200), top: pick.y }}>
          <button className="btn sm primary" onClick={() => { setAdding(pick.text); setPick(null); }}>＋ Thêm vào glossary</button>
        </div>
      )}
      {adding && (
        <AddTermDialog bookId={bookId} src={adding} onClose={() => setAdding(null)}
          onSaved={() => { setAdding(null); void detail.refetch(); if (translated && !busy) setAskRetranslate(true); else toast('Đã thêm thuật ngữ'); }} />
      )}
      {askRetranslate && (
        <ConfirmDialog title="Dịch lại chương này để áp thuật ngữ mới?" onClose={() => setAskRetranslate(false)}
          actions={[{ label: 'Dịch lại chương', primary: true, onClick: () => { setAskRetranslate(false); translate.mutate({}); } }]} />
      )}
      {confirmKeep && (
        <ConfirmDialog title="Chương có câu đã sửa tay" onClose={() => setConfirmKeep(false)} actions={[
          { label: 'Dịch lại cả câu đã sửa', danger: true, onClick: () => translate.mutate({ keep: false, engine: engineChoice }) },
          { label: 'Giữ câu đã sửa tay', primary: true, onClick: () => translate.mutate({ keep: true, engine: engineChoice }) },
        ]}>
          <p>Giữ lại các câu bạn đã sửa khi dịch lại chương này?</p>
        </ConfirmDialog>
      )}
    </section>
  );
}
