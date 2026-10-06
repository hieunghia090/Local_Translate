import { useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, ApiError, errorMessage, qs } from '../../api/client';
import type { BookDetail, BulkResult, ChapterPage, ChapterRow, ChapterStatus } from '../../api/types';
import Banner from '../../components/Banner';
import ConfirmDialog from '../../components/ConfirmDialog';
import CostEstimateDialog, { ESTIMATE_MIN_CHAPTERS } from '../../components/CostEstimateDialog';
import Segmented from '../../components/Segmented';
import StatusChip from '../../components/StatusChip';
import { useToast } from '../../components/Toast';
import { BUSY_STATUSES, fmtNum, relativeTime } from '../../lib/format';
import { useDebouncedValue } from '../../lib/hooks';

type Filter = 'all' | 'todo' | 'done' | 'rev' | 'err';
const FILTER_STATUSES: Record<Filter, ChapterStatus[] | undefined> = {
  all: undefined, todo: ['todo'], done: ['translated', 'reviewed'], rev: ['needs_review'], err: ['error'],
};
const ALL_STATUSES: ChapterStatus[] = ['todo', 'queued', 'translating', 'translated', 'needs_review', 'reviewed', 'error'];
type Action = 'translate' | 'retranslate' | 'mark_reviewed' | 'review';
type Pending = { action: Action; ids: string[] | null; statuses: ChapterStatus[] | null; editedChapters: number; engine?: 'ct2' | 'deepseek' };

export default function ChaptersTab({ book }: { book: BookDetail }) {
  const [params, setParams] = useSearchParams();
  const filter = (['all', 'todo', 'done', 'rev', 'err'].includes(params.get('f') ?? '') ? params.get('f') : 'all') as Filter;
  const setFilter = (f: Filter) => { setParams(f === 'all' ? {} : { f }); setSelected(new Set()); setAllMatching(false); };
  const [q, setQ] = useState('');
  const dq = useDebouncedValue(q.trim(), 200);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [allMatching, setAllMatching] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [estimating, setEstimating] = useState<Pending | null>(null);
  const [renaming, setRenaming] = useState<ChapterRow | null>(null);
  const [deleting, setDeleting] = useState<ChapterRow | null>(null);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const [replaceFor, setReplaceFor] = useState<ChapterRow | null>(null);
  const toast = useToast();
  const qc = useQueryClient();
  const statuses = FILTER_STATUSES[filter];

  const list = useInfiniteQuery({
    queryKey: ['chapters', book.id, filter, dq],
    queryFn: ({ pageParam }) => api.get<ChapterPage>(
      `/books/${book.id}/chapters${qs({ status: statuses?.join(','), q: dq || undefined, cursor: pageParam, limit: 50 })}`),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const rows = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);
  const total = list.data?.pages[0]?.total ?? 0;
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['chapters'] });
    void qc.invalidateQueries({ queryKey: ['book'] });
    void qc.invalidateQueries({ queryKey: ['queue'] });
  };

  const bulk = useMutation({
    mutationFn: (p: Pending & { keep?: boolean }) => api.post<BulkResult>(`/books/${book.id}/chapters/bulk`, {
      action: p.action,
      ...(p.ids ? { chapter_ids: p.ids } : { filter: { status: p.statuses } }),
      ...(p.keep === undefined ? {} : { keep_manual_edits: p.keep }),
      ...(p.engine ? { engine: p.engine } : {}),
    }),
    onSuccess: (r, p) => {
      setPending(null);
      setSelected(new Set());
      setAllMatching(false);
      const done = p.action === 'mark_reviewed' ? `Đã đánh dấu ${r.affected} chương đã soát`
        : p.action === 'review' ? `Đã thêm ${r.affected} chương vào hàng đợi soát DeepSeek`
          : `Đã thêm ${r.affected} chương vào hàng đợi`;
      const skipped = p.action === 'translate' ? `Bỏ qua ${r.skipped} chương đã dịch`
        : p.action === 'review' ? `Bỏ qua ${r.skipped} chương không soát được (chưa dịch hoặc đã dịch bằng DeepSeek)`
          : `Bỏ qua ${r.skipped} chương`;
      toast(r.skipped ? (r.affected ? `${done}. ${skipped}` : skipped) : done);
      refresh();
    },
    onError: (e, p) => {
      if (e instanceof ApiError && e.code === 'CONFIRM_MANUAL_EDITS') setPending({ ...p, editedChapters: Number(e.details.chapters ?? 1) });
      else toast(errorMessage(e));
    },
  });
  const pendingFor = (action: Action, ids?: string[], engine?: 'ct2' | 'deepseek'): Pending => ({
    action,
    ids: ids ?? (allMatching ? null : [...selected]),
    statuses: allMatching ? (statuses ?? ALL_STATUSES) : null,
    editedChapters: 0,
    engine,
  });
  /** BR-8.26: engine hiệu lực là engine được chọn riêng cho lần này, không thì engine của truyện; soát luôn là DeepSeek. */
  const usesDeepSeek = (p: Pending) => p.action === 'review'
    || ((p.action === 'translate' || p.action === 'retranslate') && (p.engine ?? book.run_config.engine) === 'deepseek');
  /** BR-3.2a, BR-3.2b, BR-8.26: mọi thao tác hàng loạt qua DeepSeek từ 10 chương trở lên đều hỏi chi phí trước. */
  const submit = (p: Pending) => {
    const count = p.ids ? p.ids.length : total;
    if (usesDeepSeek(p) && count >= ESTIMATE_MIN_CHAPTERS) setEstimating(p);
    else bulk.mutate(p);
  };
  const run = (action: Action, ids?: string[]) => submit(pendingFor(action, ids));
  const runDeepSeek = (action: 'translate' | 'review') => submit(pendingFor(action, undefined, action === 'translate' ? 'deepseek' : undefined));

  const rename = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) => api.patch(`/chapters/${id}`, { title_vi: title }),
    onSuccess: () => { setRenaming(null); refresh(); },
    onError: (e) => toast(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/chapters/${id}`),
    onSuccess: () => { setDeleting(null); toast('Đã xoá chương'); refresh(); },
    onError: (e) => toast(errorMessage(e)),
  });
  const replace = useMutation({
    mutationFn: ({ id, file }: { id: string; file: File }) => {
      const form = new FormData();
      form.set('file', file);
      return api.put<{ changed: boolean }>(`/chapters/${id}/source`, form);
    },
    onSuccess: (r) => { toast(r.changed ? 'Đã thay bản gốc, chương về Chưa dịch' : 'Bản gốc giống hệt, không có gì thay đổi'); refresh(); },
    onError: (e) => toast(errorMessage(e)),
  });

  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
    setAllMatching(false);
  };
  const visibleAll = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const chosen = allMatching ? total : selected.size;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="lib-tools">
        <Segmented ariaLabel="Lọc chương" value={filter} onChange={setFilter} options={[
          { value: 'all', label: `Tất cả · ${fmtNum(book.stats.total)}` },
          { value: 'todo', label: `Chưa dịch · ${fmtNum(book.stats.todo)}` },
          { value: 'done', label: `Đã dịch · ${fmtNum(book.stats.translated + book.stats.reviewed)}` },
          { value: 'rev', label: `Cần soát · ${fmtNum(book.stats.needs_review)}` },
          { value: 'err', label: `Lỗi · ${fmtNum(book.stats.error)}` },
        ]} />
        <span className="grow" />
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tìm chương…"
          aria-label="Tìm chương" style={{ maxWidth: 220 }} />
      </div>
      {chosen > 0 && (
        <div className="bulk">
          <b>{fmtNum(chosen)}</b> chương đã chọn
          {!allMatching && visibleAll && total > rows.length && !dq && (
            <button className="btn sm" onClick={() => setAllMatching(true)}>Chọn cả {fmtNum(total)} chương khớp bộ lọc</button>
          )}
          <span className="grow" />
          <button className="btn sm" disabled={bulk.isPending} onClick={() => run('translate')}>▶ Dịch</button>
          <button className="btn sm" disabled={bulk.isPending} onClick={() => run('retranslate')}>↻ Dịch lại</button>
          <button className="btn sm" disabled={bulk.isPending} onClick={() => runDeepSeek('translate')}>▶ Dịch bằng DeepSeek</button>
          <button className="btn sm" disabled={bulk.isPending} onClick={() => runDeepSeek('review')}>✦ Soát bằng DeepSeek</button>
          <button className="btn sm" disabled={bulk.isPending} onClick={() => run('mark_reviewed')}>✓ Đánh dấu đã soát</button>
          <button className="btn sm" onClick={() => { setSelected(new Set()); setAllMatching(false); }}>Bỏ chọn</button>
        </div>
      )}
      {list.isError && <Banner>Không tải được danh sách chương: {errorMessage(list.error)}</Banner>}
      <div className="tbl">
        <table>
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="Chọn tất cả chương đang hiện" checked={visibleAll}
                onChange={(e) => { setSelected(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set()); setAllMatching(false); }} /></th>
              <th>#</th><th>Chương</th><th>Trạng thái</th><th style={{ textAlign: 'right' }}>Chữ</th><th>Model</th><th>Cập nhật</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const busy = BUSY_STATUSES.includes(r.status);
              const differs = r.model_id && r.model_id !== book.run_config.model_id;
              return (
                <tr key={r.id}>
                  <td><input type="checkbox" aria-label={`Chọn chương ${r.no}`} checked={allMatching || selected.has(r.id)} onChange={() => toggle(r.id)} /></td>
                  <td className="num">{r.no}</td>
                  <td>
                    <div className="ch-title">
                      <Link to={`/books/${book.id}/chapters/${r.no}`}>{r.title_vi || '(chưa có tên Việt)'}</Link>
                      <span className="z">{r.title_zh}</span>
                      {r.error && <span className="err-text">{r.error}</span>}
                    </div>
                  </td>
                  <td><StatusChip status={r.status} /></td>
                  <td className="num">{fmtNum(r.char_count)}</td>
                  <td>
                    {r.model_id ? (
                      <span className="mbadge" title={r.last_run ? `beam ${r.last_run.beam} · ${r.last_run.chunk_mode} · ${new Date(r.last_run.at).toLocaleString('vi-VN')}` : undefined}>
                        {r.model_id}{differs && <i className="dot-warn" aria-label="khác model mặc định" />}
                      </span>
                    ) : <span className="hint">–</span>}
                    {r.compare && r.compare.both > 0 && (
                      <span className="cmpbadge" title={`Có bản Hachimi và AI · ${r.compare.differ}/${r.compare.both} câu khác nhau`}>
                        ≠ {r.compare.differ}
                      </span>
                    )}
                  </td>
                  <td className="hint">{relativeTime(r.updated_at)}</td>
                  <td>
                    <div className="row" style={{ flexWrap: 'nowrap' }}>
                      {r.status === 'todo' || r.status === 'error'
                        ? <button className="btn sm" aria-label={`Dịch chương ${r.no}`} disabled={bulk.isPending} onClick={() => run('translate', [r.id])}>▶ Dịch</button>
                        : <Link className="btn sm" to={`/books/${book.id}/chapters/${r.no}`}>Mở</Link>}
                      <span className="dd">
                        <button className="btn sm" aria-label={`Thao tác chương ${r.no}`} onClick={() => setMenuFor(menuFor === r.id ? null : r.id)}>…</button>
                        {menuFor === r.id && (
                          <div className="menu" onMouseLeave={() => setMenuFor(null)}>
                            <button disabled={busy || r.status === 'todo'} onClick={() => { setMenuFor(null); run('retranslate', [r.id]); }}>Dịch lại</button>
                            <button onClick={() => { setMenuFor(null); setRenaming(r); }}>Sửa tiêu đề</button>
                            <button disabled={busy} onClick={() => { setMenuFor(null); setReplaceFor(r); replaceRef.current?.click(); }}>Thay bản gốc</button>
                            <button disabled={busy} onClick={() => { setMenuFor(null); setDeleting(r); }}>Xoá chương</button>
                          </div>
                        )}
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
            {!list.isPending && rows.length === 0 && (
              <tr><td colSpan={8} className="hint" style={{ textAlign: 'center' }}>Không có chương nào khớp.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="row">
        <span className="hint grow">Hiển thị {fmtNum(rows.length)}/{fmtNum(total)} chương</span>
        {list.hasNextPage && (
          <button className="btn sm" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>Xem thêm 50 chương</button>
        )}
      </div>
      <input ref={replaceRef} type="file" accept=".txt,.md" hidden aria-label="Chọn file bản gốc mới"
        onChange={(e) => { const f = e.target.files?.[0]; if (f && replaceFor) replace.mutate({ id: replaceFor.id, file: f }); e.target.value = ''; }} />

      {estimating && (
        <CostEstimateDialog bookId={book.id}
          target={{ action: estimating.action === 'review' ? 'review' : 'translate',
            ...(estimating.ids ? { chapter_ids: estimating.ids } : { filter: { status: estimating.statuses ?? ALL_STATUSES } }) }}
          title={estimating.action === 'review' ? 'Soát bằng DeepSeek?' : 'Dịch bằng DeepSeek?'}
          confirmLabel="Thêm vào hàng đợi"
          onClose={() => setEstimating(null)}
          onConfirm={() => { const p = estimating; setEstimating(null); bulk.mutate(p); }} />
      )}
      {pending && (
        <ConfirmDialog title="Có câu đã sửa tay" onClose={() => setPending(null)} actions={[
          { label: 'Dịch lại cả câu đã sửa', danger: true, onClick: () => bulk.mutate({ ...pending, keep: false }) },
          { label: 'Giữ câu đã sửa tay', primary: true, onClick: () => bulk.mutate({ ...pending, keep: true }) },
        ]}>
          <p>{pending.editedChapters} chương có câu bạn đã sửa tay. Giữ lại các câu đó khi dịch lại?</p>
        </ConfirmDialog>
      )}
      {renaming && (
        <RenameDialog row={renaming} onClose={() => setRenaming(null)} onSave={(title) => rename.mutate({ id: renaming.id, title })} />
      )}
      {deleting && (
        <ConfirmDialog title={`Xoá chương ${deleting.no}?`} onClose={() => setDeleting(null)}
          actions={[{ label: 'Xoá chương', danger: true, onClick: () => remove.mutate(deleting.id) }]}>
          <p>Bản gốc và bản dịch của chương này bị xoá. Các chương sau được đánh số lại.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

function RenameDialog({ row, onClose, onSave }: { row: ChapterRow; onClose: () => void; onSave: (title: string) => void }) {
  const [title, setTitle] = useState(row.title_vi ?? '');
  return (
    <ConfirmDialog title={`Sửa tiêu đề chương ${row.no}`} onClose={onClose} actions={[{ label: 'Lưu', primary: true, onClick: () => onSave(title) }]}>
      <div className="field">
        <label htmlFor="renameInput">Tiêu đề tiếng Việt</label>
        <input id="renameInput" type="text" value={title} autoFocus onChange={(e) => setTitle(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSave(title)} />
      </div>
    </ConfirmDialog>
  );
}
