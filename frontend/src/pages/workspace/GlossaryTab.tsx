import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { api, ApiError, errorMessage, qs } from '../../api/client';
import type { BookItem, BulkResult, GlossaryCategory, GlossaryListView, GlossaryTerm, ImportResult, NameLang } from '../../api/types';
import Banner from '../../components/Banner';
import ConfirmDialog from '../../components/ConfirmDialog';
import { useToast } from '../../components/Toast';
import { CATEGORY_LABEL, fmtNum, NAME_LANG_LABEL } from '../../lib/format';
import { useDebouncedValue } from '../../lib/hooks';
import HealthPanel from './HealthPanel';
import SuggestionsPanel from './SuggestionsPanel';

const CATEGORIES = Object.keys(CATEGORY_LABEL) as GlossaryCategory[];
const NAME_LANGS = Object.keys(NAME_LANG_LABEL) as NameLang[];
type SendFilter = 'all' | 'send' | 'predictable';
const HV_TITLE = 'Tự đoán được: trùng âm Hán Việt, không gửi cho DeepSeek';

export default function GlossaryTab({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState('');
  const dq = useDebouncedValue(q.trim(), 250);
  const [category, setCategory] = useState<GlossaryCategory | ''>('');
  const [sort, setSort] = useState<'src' | 'occurrence'>('occurrence');
  const [send, setSend] = useState<SendFilter>('all');
  const [draft, setDraft] = useState({ src_zh: '', dst_vi: '', category: 'character' as GlossaryCategory });
  const [formError, setFormError] = useState<string | null>(null);
  const [affected, setAffected] = useState<string[]>([]);
  const [askKeep, setAskKeep] = useState(false);
  const [deleting, setDeleting] = useState<GlossaryTerm | null>(null);
  const [conflicts, setConflicts] = useState<{ file: File; items: ImportResult['conflicts']; pick: Set<string> } | null>(null);
  const [copyFrom, setCopyFrom] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const key = ['glossary', bookId, dq, category, sort, send];
  const refresh = () => void qc.invalidateQueries({ queryKey: ['glossary', bookId] });
  const onError = (e: unknown) => toast(errorMessage(e));

  const terms = useQuery({
    queryKey: key,
    queryFn: () => api.get<GlossaryListView>(`/books/${bookId}/glossary${qs({
      q: dq || undefined, category: category || undefined, sort, send: send === 'all' ? undefined : send,
    })}`),
  });
  const books = useQuery({ queryKey: ['books', 'for-copy'], queryFn: () => api.get<{ items: BookItem[] }>('/books') });

  const add = useMutation({
    mutationFn: () => api.post<GlossaryTerm>(`/books/${bookId}/glossary`, draft),
    onSuccess: () => { setDraft({ ...draft, src_zh: '', dst_vi: '' }); setFormError(null); refresh(); },
    onError: (e) => setFormError(errorMessage(e)),
  });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: object }) =>
      api.patch<{ term: GlossaryTerm; affected_chapter_ids: string[] }>(`/glossary/${id}`, body),
    onSuccess: (r) => { if (r.affected_chapter_ids.length) setAffected((cur) => [...new Set([...cur, ...r.affected_chapter_ids])]); refresh(); },
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/glossary/${id}`),
    onSuccess: () => { setDeleting(null); refresh(); },
    onError,
  });
  const retranslate = useMutation({
    mutationFn: (keep: boolean | undefined) => api.post<BulkResult>(`/books/${bookId}/chapters/bulk`, {
      action: 'retranslate', chapter_ids: affected, ...(keep === undefined ? {} : { keep_manual_edits: keep }),
    }),
    onSuccess: (r) => { setAffected([]); setAskKeep(false); toast(`Đã thêm ${r.affected} chương vào hàng đợi`, { to: `/books/${bookId}/queue`, label: 'Xem hàng đợi' }); },
    onError: (e) => (e instanceof ApiError && e.code === 'CONFIRM_MANUAL_EDITS' ? setAskKeep(true) : onError(e)),
  });
  const importFile = useMutation({
    mutationFn: ({ file, mode, overwrite }: { file: File; mode: 'ask' | 'keep'; overwrite?: string[] }) => {
      const form = new FormData();
      form.set('file', file);
      form.set('on_conflict', mode);
      if (overwrite) form.set('overwrite', JSON.stringify(overwrite));
      return api.post<ImportResult>(`/books/${bookId}/glossary/import`, form);
    },
    onSuccess: (r, vars) => {
      if (vars.mode === 'ask' && r.conflicts.length) { setConflicts({ file: vars.file, items: r.conflicts, pick: new Set() }); return; }
      if (vars.mode === 'ask') { importFile.mutate({ file: vars.file, mode: 'keep' }); return; }
      setConflicts(null);
      toast(`Đã thêm ${r.added}, cập nhật ${r.updated}, giữ nguyên ${r.kept} thuật ngữ`);
      refresh();
    },
    onError: (e) => {
      const details = e instanceof ApiError ? (e.details.errors as { line: number; message: string }[] | undefined) : undefined;
      toast(details?.length ? `${errorMessage(e)}: dòng ${details[0].line} – ${details[0].message}` : errorMessage(e));
    },
  });
  const copy = useMutation({
    mutationFn: () => api.post<{ added: number; skipped: number }>(`/books/${bookId}/glossary/copy`, { from_book_id: copyFrom }),
    onSuccess: (r) => { toast(`Đã sao ${r.added} thuật ngữ, bỏ qua ${r.skipped} đã có`); setCopyFrom(''); refresh(); },
    onError,
  });

  const submit = () => {
    if (!draft.src_zh.trim() || !draft.dst_vi.trim()) { setFormError('Cần nhập nguồn Trung và đích Việt'); return; }
    add.mutate();
  };
  const items = terms.data?.items ?? [];
  const summary = terms.data?.summary;

  return (
    <div style={{ display: 'grid', gap: 10 }}>
      <div className="row">
        <span className="hint grow">Glossary riêng của truyện này. HachimiMT dùng mọi term đang bật; DeepSeek chỉ nhận term không tự đoán được (không có HV) hoặc bật Luôn gửi.</span>
        <button className="btn sm" onClick={() => fileRef.current?.click()}>Nhập</button>
        <a className="btn sm" href={`/api/v1/books/${bookId}/glossary/export?format=tsv`} download>Xuất .tsv</a>
        <a className="btn sm" href={`/api/v1/books/${bookId}/glossary/export?format=json`} download>Xuất .json</a>
        <input ref={fileRef} type="file" accept=".tsv,.txt,.json" hidden aria-label="Chọn file glossary"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile.mutate({ file: f, mode: 'ask' }); e.target.value = ''; }} />
      </div>
      {affected.length > 0 && (
        <Banner kind="info">
          <span className="grow">{affected.length} chương đã dịch dùng tên cũ. Dịch lại các chương này?</span>
          <button className="btn sm" onClick={() => setAffected([])}>Để sau</button>
          <button className="btn sm primary" onClick={() => retranslate.mutate(undefined)}>Dịch lại {affected.length} chương</button>
        </Banner>
      )}
      <div className="panel" style={{ gap: 8 }}>
        <div className="row">
          <input type="text" className="zh" placeholder="Nguồn Trung" aria-label="Nguồn Trung mới" style={{ maxWidth: 160 }}
            value={draft.src_zh} onChange={(e) => setDraft({ ...draft, src_zh: e.target.value })} />
          <input type="text" placeholder="Đích Việt" aria-label="Đích Việt mới" style={{ maxWidth: 200 }}
            value={draft.dst_vi} onChange={(e) => setDraft({ ...draft, dst_vi: e.target.value })}
            onKeyDown={(e) => e.key === 'Enter' && submit()} />
          <select value={draft.category} aria-label="Loại mới" style={{ width: 'auto' }}
            onChange={(e) => setDraft({ ...draft, category: e.target.value as GlossaryCategory })}>
            {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
          </select>
          <button className="btn sm primary" onClick={submit} disabled={add.isPending}>＋ Thêm</button>
        </div>
        {formError && <span className="err-text">{formError}</span>}
      </div>
      <div className="lib-tools">
        <input type="search" placeholder="Tìm nguồn hoặc đích…" aria-label="Tìm thuật ngữ" style={{ maxWidth: 240 }}
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={category} aria-label="Lọc loại" style={{ width: 'auto' }} onChange={(e) => setCategory(e.target.value as GlossaryCategory | '')}>
          <option value="">Mọi loại</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
        </select>
        <select value={send} aria-label="Lọc gửi cho DeepSeek" style={{ width: 'auto' }} onChange={(e) => setSend(e.target.value as SendFilter)}>
          <option value="all">Mọi term</option><option value="send">Gửi cho DeepSeek</option><option value="predictable">Tự đoán được</option>
        </select>
        <select value={sort} aria-label="Sắp xếp" style={{ width: 'auto' }} onChange={(e) => setSort(e.target.value as 'src' | 'occurrence')}>
          <option value="occurrence">Xuất hiện nhiều nhất</option><option value="src">Theo nguồn</option>
        </select>
        <span className="grow" />
        <span className="hint">
          {summary ? `${fmtNum(summary.total)} term · ${fmtNum(summary.will_send)} term sẽ gửi khi gặp (không tự đoán được hoặc luôn gửi)`
            : `${fmtNum(items.length)} thuật ngữ`}
        </span>
      </div>
      <div className="tbl">
        <table>
          <thead><tr>
            <th>Nguồn Trung</th><th>Đích Việt</th><th>Loại</th><th>Gốc tên</th><th>Ghi chú</th><th>Alias dự phòng</th>
            <th style={{ textAlign: 'right' }}>Xuất hiện</th><th>Bật</th><th>Luôn gửi</th><th>Gửi ghi chú</th><th />
          </tr></thead>
          <tbody>
            {items.map((t) => (
              <tr key={t.id}>
                <td className="zh">{t.src_zh}{t.predictable && <span className="hvtag" title={HV_TITLE}>HV</span>}</td>
                <td><input type="text" defaultValue={t.dst_vi} key={`${t.id}:${t.dst_vi}`} aria-label={`Đích Việt của ${t.src_zh}`}
                  onBlur={(e) => {
                    const v = e.target.value.trim();
                    if (!v) e.target.value = t.dst_vi;
                    else if (v !== t.dst_vi) patch.mutate({ id: t.id, body: { dst_vi: v } });
                  }} /></td>
                <td><select value={t.category} aria-label={`Loại của ${t.src_zh}`} style={{ width: 'auto' }}
                  onChange={(e) => patch.mutate({ id: t.id, body: { category: e.target.value } })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                </select></td>
                <td><select value={t.name_lang ?? ''} aria-label={`Gốc tên của ${t.src_zh}`} style={{ width: 'auto' }}
                  onChange={(e) => patch.mutate({ id: t.id, body: { name_lang: e.target.value || null } })}>
                  <option value="">—</option>
                  {NAME_LANGS.map((l) => <option key={l} value={l}>{NAME_LANG_LABEL[l]}</option>)}
                </select></td>
                <td><input type="text" defaultValue={t.notes ?? ''} key={`${t.id}:n:${t.notes ?? ''}`} aria-label={`Ghi chú của ${t.src_zh}`}
                  onBlur={(e) => { const v = e.target.value.trim(); if (v !== (t.notes ?? '')) patch.mutate({ id: t.id, body: { notes: v || null } }); }} /></td>
                <td><input type="text" defaultValue={t.aliases.join('|')} key={`${t.id}:${t.aliases.join('|')}`} aria-label={`Alias của ${t.src_zh}`}
                  onBlur={(e) => { const v = e.target.value; if (v !== t.aliases.join('|')) patch.mutate({ id: t.id, body: { aliases: v.split('|') } }); }} /></td>
                <td className="num">{fmtNum(t.occurrence_count)}</td>
                <td><input type="checkbox" checked={t.enabled} aria-label={`Bật ${t.src_zh}`}
                  onChange={(e) => patch.mutate({ id: t.id, body: { enabled: e.target.checked } })} /></td>
                <td><input type="checkbox" checked={t.always_send} aria-label={`Luôn gửi ${t.src_zh}`}
                  title={t.miss_count ? `Bị dịch sai ${t.miss_count} lần` : undefined}
                  onChange={(e) => patch.mutate({ id: t.id, body: { always_send: e.target.checked } })} /></td>
                <td><input type="checkbox" checked={t.prompt_note} aria-label={`Gửi ghi chú ${t.src_zh}`}
                  onChange={(e) => patch.mutate({ id: t.id, body: { prompt_note: e.target.checked } })} /></td>
                <td><button className="btn sm" aria-label={`Xoá ${t.src_zh}`} onClick={() => setDeleting(t)}>✕</button></td>
              </tr>
            ))}
            {!terms.isPending && items.length === 0 && <tr><td colSpan={11} className="hint" style={{ textAlign: 'center' }}>Chưa có thuật ngữ nào.</td></tr>}
          </tbody>
        </table>
      </div>
      <SuggestionsPanel bookId={bookId} />
      <HealthPanel bookId={bookId} />
      <div className="row">
        <span className="hint grow">Sao toàn bộ glossary từ truyện khác (bỏ qua term đã có).</span>
        <select value={copyFrom} aria-label="Sao từ truyện" style={{ width: 'auto' }} onChange={(e) => setCopyFrom(e.target.value)}>
          <option value="">Chọn truyện…</option>
          {(books.data?.items ?? []).filter((b) => b.id !== bookId).map((b) => <option key={b.id} value={b.id}>{b.title_vi || b.title_zh}</option>)}
        </select>
        <button className="btn sm" disabled={!copyFrom || copy.isPending} onClick={() => copy.mutate()}>Sao</button>
      </div>

      {deleting && (
        <ConfirmDialog title={`Xoá “${deleting.src_zh}”?`} onClose={() => setDeleting(null)}
          actions={[{ label: 'Xoá', danger: true, onClick: () => remove.mutate(deleting.id) }]}>
          <p>Chương đã dịch không đổi. Lần dịch sau sẽ không áp thuật ngữ này.</p>
        </ConfirmDialog>
      )}
      {askKeep && (
        <ConfirmDialog title="Có câu đã sửa tay" onClose={() => setAskKeep(false)} actions={[
          { label: 'Dịch lại cả câu đã sửa', danger: true, onClick: () => retranslate.mutate(false) },
          { label: 'Giữ câu đã sửa tay', primary: true, onClick: () => retranslate.mutate(true) },
        ]}><p>Một số chương có câu bạn đã sửa tay. Giữ lại các câu đó?</p></ConfirmDialog>
      )}
      {conflicts && (
        <ConfirmDialog title={`${conflicts.items.length} thuật ngữ đã có`} onClose={() => setConflicts(null)} actions={[
          { label: 'Ghi đè tất cả', onClick: () => importFile.mutate({ file: conflicts.file, mode: 'keep', overwrite: conflicts.items.map((c) => c.src_zh) }) },
          { label: conflicts.pick.size ? `Ghi đè ${conflicts.pick.size} mục đã chọn` : 'Giữ bản hiện có', primary: true,
            onClick: () => importFile.mutate({ file: conflicts.file, mode: 'keep', overwrite: [...conflicts.pick] }) },
        ]}>
          <div className="tbl" style={{ maxHeight: 300 }}>
            <table>
              <thead><tr><th>Ghi đè</th><th>Nguồn</th><th>Hiện có</th><th>Trong file</th></tr></thead>
              <tbody>
                {conflicts.items.map((c) => (
                  <tr key={c.src_zh}>
                    <td><input type="checkbox" checked={conflicts.pick.has(c.src_zh)} aria-label={`Ghi đè ${c.src_zh}`}
                      onChange={(e) => { const pick = new Set(conflicts.pick); if (e.target.checked) pick.add(c.src_zh); else pick.delete(c.src_zh); setConflicts({ ...conflicts, pick }); }} /></td>
                    <td className="zh">{c.src_zh}</td><td>{c.current}</td><td>{c.incoming}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ConfirmDialog>
      )}
    </div>
  );
}
