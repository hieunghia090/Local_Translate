import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, ApiError, errorMessage, qs } from '../api/client';
import type { DeepSeekModelId, Encoding, Genre, ImportView, RunConfig, SplitRule } from '../api/types';
import Banner from '../components/Banner';
import Breadcrumb from '../components/Breadcrumb';
import Segmented from '../components/Segmented';
import { useToast } from '../components/Toast';
import { fmtNum, GENRES, slugify } from '../lib/format';
import CreateBookAiBlock, { aiExtractPayload, DEFAULT_AI, type AiExtractState } from './CreateBookAiBlock';

type Mode = 'single' | 'multi' | 'empty';
const PAGE = 500;
const WARN_LABEL: Record<string, string> = {
  SHORT: 'ngắn', AUTHOR_NOTE: 'lời tác giả', TITLE_UNTRANSLATED: 'chưa dịch được tiêu đề',
};

export default function CreateBookPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const titleRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);

  const [titleZh, setTitleZh] = useState('');
  const [titleTouched, setTitleTouched] = useState(false);
  const [titleVi, setTitleVi] = useState('');
  const [author, setAuthor] = useState('');
  const [genre, setGenre] = useState<Genre>('other');
  const [note, setNote] = useState('');
  const [mode, setMode] = useState<Mode>('multi');
  const [splitRule, setSplitRule] = useState<SplitRule>('auto');
  const [splitRegex, setSplitRegex] = useState('');
  const [encoding, setEncoding] = useState<Encoding>('auto');
  const [importId, setImportId] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [page, setPage] = useState(0);
  const [beam, setBeam] = useState(2);
  const [chunkMode, setChunkMode] = useState<'sentence' | 'paragraph'>('paragraph');
  const [hanNormalize, setHanNormalize] = useState<'auto' | 't2s' | 'none'>('auto');
  const [ai, setAi] = useState<AiExtractState>(DEFAULT_AI);
  const [engine, setEngine] = useState<'ct2' | 'deepseek'>('ct2');
  const [dsModel, setDsModel] = useState<DeepSeekModelId>('deepseek-v4-pro');
  const [concurrency, setConcurrency] = useState(3);
  const [titleError, setTitleError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [duplicateOf, setDuplicateOf] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  useEffect(() => {
    folderRef.current?.setAttribute('webkitdirectory', '');
  }, [mode]);

  const imp = useQuery({
    queryKey: ['import', importId],
    queryFn: () => api.get<ImportView>(`/imports/${importId}`),
    enabled: importId !== null,
    refetchInterval: (query) => (query.state.data?.status === 'parsing' ? 500 : false),
  });
  const view = imp.data;
  const parsing = uploading || view?.status === 'parsing';

  useEffect(() => {
    if (view?.status === 'ready' && view.suggested_title_zh && !titleTouched && !titleZh) setTitleZh(view.suggested_title_zh);
  }, [view, titleTouched, titleZh]);

  useEffect(() => { // đổi file hoặc phân tích lại thì đề xuất chạy thử cũ không còn đúng
    if (!importId || view?.status === 'parsing') setAi((a) => (a.preview ? { ...a, preview: null, picked: new Set(), edits: {} } : a));
  }, [importId, view?.status]);

  const defaults = useQuery({
    queryKey: ['run-config-defaults', genre],
    queryFn: () => api.get<RunConfig>(`/run-config/defaults${qs({ genre })}`),
  });
  const honorific = defaults.data?.honorific;

  const patchImport = useMutation({
    mutationFn: (body: object) => api.patch<ImportView>(`/imports/${importId}`, body),
    onSuccess: (data) => qc.setQueryData(['import', importId], data),
    onError: (e) => setFormError(errorMessage(e)),
  });

  async function upload(files: File[]) {
    setFormError(null);
    const accepted = files.filter((f) => /\.(txt|md)$/i.test(f.name));
    if (accepted.length === 0) {
      setFormError('Chỉ nhận file .txt hoặc .md');
      return;
    }
    const form = new FormData();
    form.set('mode', mode);
    form.set('split_rule', splitRule);
    if (splitRule === 'regex') form.set('split_regex', splitRegex);
    form.set('encoding', encoding);
    const folder = accepted[0].webkitRelativePath.split('/')[0];
    if (folder) form.set('source_name', folder);
    for (const f of mode === 'single' ? accepted.slice(0, 1) : accepted) {
      form.append('files[]', f, f.webkitRelativePath || f.name);
    }
    setUploading(true);
    try {
      const res = await api.post<{ import_id: string }>('/imports', form);
      setImportId(res.import_id);
      setPage(0);
    } catch (e) {
      setFormError(errorMessage(e));
    } finally {
      setUploading(false);
    }
  }

  function reparse(next: { split_rule?: SplitRule; split_regex?: string; encoding?: Encoding }) {
    if (!importId) return;
    if ((next.split_rule ?? splitRule) === 'regex' && !(next.split_regex ?? splitRegex)) return;
    patchImport.mutate(next);
  }

  const rows = view?.chapters ?? [];
  const pageRows = rows.slice(page * PAGE, (page + 1) * PAGE);
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const selectedCount = rows.filter((r) => r.selected).length;
  const slug = slugify(titleVi || titleZh) || 'truyen';

  const create = useMutation({
    mutationFn: (confirmDuplicate: boolean) =>
      api.post<{ id: string; slug: string }>('/books', {
        title_zh: titleZh.trim(),
        title_vi: titleVi.trim() || null,
        author: author.trim() || null,
        genre,
        note: note.trim() || null,
        import_id: mode === 'empty' ? null : importId,
        run_config: engine === 'ct2'
          ? { engine: 'ct2', model_id: 'HachimiMT-60', beam, chunk_mode: chunkMode, han_normalize: hanNormalize }
          : { engine: 'deepseek', model_id: dsModel, beam, chunk_mode: chunkMode, han_normalize: hanNormalize,
            deepseek: { model_id: dsModel, concurrency } },
        confirm_duplicate: confirmDuplicate,
        ai_extract: aiExtractPayload(ai),
      }),
    onSuccess: (res) => {
      toast(`Đã tạo workspace “${titleVi.trim() || titleZh.trim()}”`);
      void qc.invalidateQueries({ queryKey: ['books'] });
      navigate(`/books/${res.id}`);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'BOOK_DUPLICATE') setDuplicateOf(String(e.details.book_id ?? ''));
      else setFormError(errorMessage(e));
    },
  });

  function submit(confirmDuplicate = false) {
    setFormError(null);
    if (!titleZh.trim()) {
      setTitleError('Cần nhập tên gốc của truyện');
      titleRef.current?.focus();
      return;
    }
    if (mode !== 'empty' && !importId) {
      setFormError('Chưa chọn file nội dung gốc. Chọn "Trống" nếu muốn thêm chương sau.');
      return;
    }
    if (mode !== 'empty' && selectedCount === 0) {
      setFormError('Chưa chọn chương nào');
      return;
    }
    setDuplicateOf(null);
    create.mutate(confirmDuplicate);
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    void upload([...e.dataTransfer.files]);
  };

  const honorificText = useMemo(() => {
    if (!honorific) return '';
    const on = [honorific.kinship && 'thân tộc', honorific.pronoun && 'đại từ', honorific.modern_stable && 'ổn định ngôi hiện đại'].filter(Boolean);
    return on.length ? `Xưng hô mặc định: ${on.join(', ')}.` : 'Xưng hô mặc định: không chuẩn hoá.';
  }, [honorific]);

  return (
    <section style={{ display: 'grid', gap: 16 }}>
      <Breadcrumb items={[{ label: 'Thư viện', to: '/' }, { label: 'Tạo truyện' }]} />
      <div className="head">
        <h1>Workspace truyện mới</h1>
        <div className="row">
          <button className="btn" onClick={() => navigate('/')}>Huỷ</button>
          <button className="btn primary" onClick={() => submit()} disabled={parsing || create.isPending}>
            {parsing ? 'Đang phân tích file…' : 'Tạo workspace'}
          </button>
        </div>
      </div>
      {formError && <Banner>{formError}</Banner>}
      {duplicateOf !== null && (
        <Banner kind="warn">
          <span className="grow">Đã có truyện này. Vẫn tạo bản mới?</span>
          <button className="btn sm" onClick={() => setDuplicateOf(null)}>Thôi</button>
          <button className="btn sm primary" onClick={() => submit(true)}>Vẫn tạo</button>
        </Banner>
      )}
      <div className="form-grid">
        <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
          <div className="panel">
            <h2>1. Thông tin truyện</h2>
            <div className="two">
              <div className="field">
                <label htmlFor="cZh">Tên gốc (Trung) *</label>
                <input id="cZh" ref={titleRef} type="text" className="zh" value={titleZh} maxLength={200}
                  onChange={(e) => { setTitleZh(e.target.value); setTitleTouched(true); setTitleError(null); }}
                  aria-invalid={titleError ? true : undefined} />
                {titleError && <span className="err-text">{titleError}</span>}
              </div>
              <div className="field">
                <label htmlFor="cVi">Tên tiếng Việt</label>
                <input id="cVi" type="text" value={titleVi} onChange={(e) => setTitleVi(e.target.value)}
                  placeholder="Để trống sẽ tự dịch từ tên gốc" />
              </div>
              <div className="field">
                <label htmlFor="cAu">Tác giả</label>
                <input id="cAu" type="text" className="zh" value={author} onChange={(e) => setAuthor(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="cGenre">Thể loại</label>
                <select id="cGenre" value={genre} onChange={(e) => setGenre(e.target.value as Genre)}>
                  {GENRES.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
                </select>
              </div>
            </div>
            <div className="hint">{honorificText}</div>
            <div className="field">
              <label htmlFor="cNote">Ghi chú</label>
              <textarea id="cNote" rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
            </div>
          </div>

          <div className="panel">
            <h2>2. Nhập nội dung gốc</h2>
            <Segmented ariaLabel="Nguồn nội dung" full value={mode} onChange={(m) => { setMode(m); setImportId(null); }} options={[
              { value: 'single', label: 'Một file cả bộ' },
              { value: 'multi', label: 'Nhiều file, mỗi file 1 chương' },
              { value: 'empty', label: 'Trống' },
            ]} />
            {mode !== 'empty' && (
              <>
                <label className={`drop${dragOver ? ' over' : ''}`} onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                  onDragLeave={() => setDragOver(false)} onDrop={onDrop}>
                  <b>Kéo thả file .txt / .md vào đây</b>
                  <span>hoặc bấm để chọn{mode === 'multi' ? ' nhiều file' : ''}</span>
                  <input type="file" hidden accept=".txt,.md" multiple={mode === 'multi'} aria-label="Chọn file nguồn"
                    onChange={(e) => { void upload([...(e.target.files ?? [])]); e.target.value = ''; }} />
                </label>
                {mode === 'multi' && (
                  <label className="btn sm" style={{ justifySelf: 'start' }}>
                    Chọn cả thư mục…
                    <input ref={folderRef} type="file" hidden multiple aria-label="Chọn thư mục nguồn"
                      onChange={(e) => { void upload([...(e.target.files ?? [])]); e.target.value = ''; }} />
                  </label>
                )}
                <div className="two">
                  {mode === 'single' && (
                    <div className="field">
                      <label htmlFor="cSplit">Quy tắc tách chương</label>
                      <select id="cSplit" value={splitRule}
                        onChange={(e) => { const v = e.target.value as SplitRule; setSplitRule(v); reparse({ split_rule: v }); }}>
                        <option value="auto">Tự động: 第…章 / 第…回 / Chương … / tiêu đề #</option>
                        <option value="blank_lines">Cách nhau từ 2 dòng trống</option>
                        <option value="regex">Regex tuỳ chỉnh</option>
                      </select>
                      {splitRule === 'regex' && (
                        <input type="text" className="mono" placeholder="^第.+章" value={splitRegex} aria-label="Regex tách chương"
                          onChange={(e) => setSplitRegex(e.target.value)}
                          onBlur={() => reparse({ split_rule: 'regex', split_regex: splitRegex })} />
                      )}
                    </div>
                  )}
                  <div className="field">
                    <label htmlFor="cEnc">Mã hoá file</label>
                    <select id="cEnc" value={encoding}
                      onChange={(e) => { const v = e.target.value as Encoding; setEncoding(v); reparse({ encoding: v }); }}>
                      <option value="auto">Tự nhận (UTF-8 / GBK / Big5)</option>
                      <option value="utf-8">UTF-8</option>
                      <option value="gbk">GBK</option>
                      <option value="big5">Big5</option>
                    </select>
                  </div>
                </div>
                {view?.status === 'failed' && <Banner>{view.error ?? 'Phân tích file thất bại'}</Banner>}
                {view && view.file_errors.length > 0 && (
                  <Banner kind="warn">
                    <div style={{ display: 'grid', gap: 2 }}>
                      {view.file_errors.slice(0, 5).map((f) => <span key={f.file}><b>{f.file}</b>: {f.message}</span>)}
                      {view.file_errors.length > 5 && <span>… và {view.file_errors.length - 5} file khác</span>}
                    </div>
                  </Banner>
                )}
                <div className="row">
                  <span className="lbl grow">
                    {parsing ? 'Đang phân tích file…'
                      : view ? `Phát hiện ${fmtNum(rows.length)} chương · ${fmtNum(view.total_chars)} chữ Hán · giữ ${fmtNum(selectedCount)}`
                      : 'Chưa chọn file'}
                  </span>
                  <span className="hint">Bỏ chọn để loại phần không phải chương</span>
                </div>
                {rows.length > 0 && (
                  <div className="tbl detect">
                    <table>
                      <thead>
                        <tr>
                          <th><input type="checkbox" aria-label="Chọn tất cả chương" checked={selectedCount === rows.length}
                            onChange={(e) => patchImport.mutate({ chapters: rows.map((r) => ({ key: r.key, selected: e.target.checked })) })} /></th>
                          <th>#</th><th>Tiêu đề phát hiện</th><th>Tiêu đề tiếng Việt</th><th style={{ textAlign: 'right' }}>Chữ</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pageRows.map((r) => (
                          <tr key={r.key}>
                            <td><input type="checkbox" checked={r.selected} aria-label={`Giữ chương ${r.no}`}
                              onChange={(e) => patchImport.mutate({ chapters: [{ key: r.key, selected: e.target.checked }] })} /></td>
                            <td className="num">{r.no}</td>
                            <td className="zh">
                              {r.title_zh}
                              {r.warnings.map((w) => <span key={w} className="flagtag">{WARN_LABEL[w] ?? w}</span>)}
                            </td>
                            <td>
                              <input type="text" defaultValue={r.title_vi} key={`${r.key}:${r.title_vi}`} aria-label={`Tiêu đề Việt chương ${r.no}`}
                                onBlur={(e) => {
                                  const v = e.target.value.trim();
                                  if (v !== r.title_vi) patchImport.mutate({ chapters: [{ key: r.key, title_vi: v }] });
                                }} />
                            </td>
                            <td className="num">{fmtNum(r.chars)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {pages > 1 && (
                  <div className="row">
                    <button className="btn sm" disabled={page === 0} onClick={() => setPage(page - 1)}>← Trước</button>
                    <span className="hint">Trang {page + 1}/{pages}</span>
                    <button className="btn sm" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>Sau →</button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>

        <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
          <div className="panel">
            <h2>3. Cấu hình dịch mặc định</h2>
            <div className="field">
              <span className="lbl">Model dịch</span>
              <Segmented ariaLabel="Model dịch" full value={engine} onChange={setEngine}
                options={[{ value: 'ct2', label: 'HachimiMT-60 · local' }, { value: 'deepseek', label: 'DeepSeek API' }]} />
            </div>
            {engine === 'deepseek' && (
              <>
                <div className="field">
                  <label htmlFor="cDsModel">Model DeepSeek</label>
                  <select id="cDsModel" value={dsModel} onChange={(e) => setDsModel(e.target.value as DeepSeekModelId)}>
                    <option value="deepseek-v4-pro">deepseek-v4-pro</option>
                    <option value="deepseek-flash">deepseek-flash</option>
                  </select>
                </div>
                <div className="field">
                  <label htmlFor="cConc">Số chương song song</label>
                  <div className="range"><input id="cConc" type="range" min={1} max={8} value={concurrency}
                    onChange={(e) => setConcurrency(Number(e.target.value))} /><output>{concurrency}</output></div>
                </div>
                <div className="hint">Nội dung chương sẽ được gửi tới DeepSeek API và tính phí theo token. Beam và chia chunk chỉ dùng cho HachimiMT.</div>
              </>
            )}
            <div className="field">
              <label htmlFor="cBeam">Beam</label>
              <div className="range"><input id="cBeam" type="range" min={1} max={4} value={beam} onChange={(e) => setBeam(Number(e.target.value))} /><output>{beam}</output></div>
            </div>
            <div className="field">
              <span className="lbl">Chia chunk</span>
              <Segmented ariaLabel="Chia chunk" full value={chunkMode} onChange={setChunkMode}
                options={[{ value: 'sentence', label: 'Theo câu' }, { value: 'paragraph', label: 'Theo đoạn' }]} />
            </div>
            <div className="field">
              <label htmlFor="cNorm">Chuẩn hoá chữ Hán</label>
              <select id="cNorm" value={hanNormalize} onChange={(e) => setHanNormalize(e.target.value as 'auto' | 't2s' | 'none')}>
                <option value="auto">Tự động phồn → giản</option>
                <option value="t2s">Ép phồn → giản</option>
                <option value="none">Giữ nguyên</option>
              </select>
            </div>
            <div className="hint">Mỗi chương vẫn có thể ghi đè cấu hình riêng.</div>
          </div>
          <CreateBookAiBlock importId={mode === 'empty' ? null : importId} ready={view?.status === 'ready'} value={ai} onChange={setAi} />
          <div className="panel">
            <h2>Sau khi tạo</h2>
            <div className="hint">1. Tạo workspace và lưu chương vào thư mục local.</div>
            <div className="hint">2. Mở workspace. Chưa tự dịch cho tới khi bạn bấm.</div>
            {ai.enabled && <div className="hint">3. Xếp job trích glossary bằng AI cho {ai.chapters} chương đầu (đề xuất chờ duyệt ở tab Glossary).</div>}
            <div className="hint mono">~/LocalTranslate/books/{slug}/</div>
          </div>
        </div>
      </div>
    </section>
  );
}
