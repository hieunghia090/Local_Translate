import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, errorMessage, qs } from '../../api/client';
import type { BookDetail, DeepSeekModelId, Genre, RunConfig } from '../../api/types';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';
import { GENRES } from '../../lib/format';
import { useAutosave } from '../../lib/hooks';
import CostPanel from './CostPanel';
import DeepSeekKeyLine from './DeepSeekKeyLine';
import FoundationPanel from './FoundationPanel';

interface Draft {
  title_zh: string; title_vi: string; author: string; genre: Genre; note: string;
  beam: number; batchAuto: boolean; batchSize: number; chunk: 'sentence' | 'paragraph'; han: 'auto' | 't2s' | 'none';
  kinship: boolean; pronoun: boolean; modern: boolean;
  engine: 'ct2' | 'deepseek'; dsModel: DeepSeekModelId; concurrency: number; chain: boolean;
  reviewAuto: boolean; reviewModel: DeepSeekModelId; reviewApply: 'none' | 'high_confidence';
}

const fromBook = (b: BookDetail): Draft => ({
  title_zh: b.title_zh, title_vi: b.title_vi ?? '', author: b.author ?? '', genre: b.genre, note: b.note ?? '',
  beam: b.run_config.beam, batchAuto: b.run_config.batch.auto, batchSize: b.run_config.batch.size,
  chunk: b.run_config.chunk_mode, han: b.run_config.han_normalize,
  kinship: b.run_config.honorific.kinship, pronoun: b.run_config.honorific.pronoun, modern: b.run_config.honorific.modern_stable,
  engine: b.run_config.engine, dsModel: b.run_config.deepseek?.model_id ?? 'deepseek-v4-pro',
  concurrency: b.run_config.deepseek?.concurrency ?? 3, chain: b.run_config.deepseek?.chain_context ?? true,
  reviewAuto: b.run_config.review?.auto_after_ct2 ?? false, reviewModel: b.run_config.review?.model_id ?? 'deepseek-flash',
  reviewApply: b.run_config.review?.auto_apply ?? 'none',
});

/** Chỉ các trường đã đổi so với bản đã lưu; run_config chỉ gồm khóa đổi (backend deep-merge). */
function diffDraft(d: Draft, p: Draft): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (d.title_zh.trim() !== p.title_zh.trim()) body.title_zh = d.title_zh.trim();
  for (const k of ['title_vi', 'author', 'genre', 'note'] as const) if (d[k] !== p[k]) body[k] = d[k];
  const rc: Record<string, unknown> = {};
  if (d.beam !== p.beam) rc.beam = d.beam;
  if (d.chunk !== p.chunk) rc.chunk_mode = d.chunk;
  if (d.han !== p.han) rc.han_normalize = d.han;
  const batch: Record<string, unknown> = {};
  if (d.batchAuto !== p.batchAuto) batch.auto = d.batchAuto;
  if (d.batchSize !== p.batchSize) batch.size = d.batchSize;
  if (Object.keys(batch).length) rc.batch = batch;
  const hon: Record<string, unknown> = {};
  if (d.kinship !== p.kinship) hon.kinship = d.kinship;
  if (d.pronoun !== p.pronoun) hon.pronoun = d.pronoun;
  if (d.modern !== p.modern) hon.modern_stable = d.modern;
  if (Object.keys(hon).length) rc.honorific = hon;
  if (d.engine !== p.engine || (d.engine === 'deepseek' && d.dsModel !== p.dsModel)) {
    rc.engine = d.engine;
    rc.model_id = d.engine === 'ct2' ? 'HachimiMT-60' : d.dsModel;
  }
  const ds: Record<string, unknown> = {};
  if (d.dsModel !== p.dsModel) ds.model_id = d.dsModel;
  if (d.concurrency !== p.concurrency) ds.concurrency = d.concurrency;
  if (d.chain !== p.chain) ds.chain_context = d.chain;
  if (Object.keys(ds).length) rc.deepseek = ds;
  const rv: Record<string, unknown> = {};
  if (d.reviewAuto !== p.reviewAuto) rv.auto_after_ct2 = d.reviewAuto;
  if (d.reviewModel !== p.reviewModel) rv.model_id = d.reviewModel;
  if (d.reviewApply !== p.reviewApply) rv.auto_apply = d.reviewApply;
  if (Object.keys(rv).length) rc.review = rv;
  if (Object.keys(rc).length) body.run_config = rc;
  return body;
}

export default function SettingsTab({ book }: { book: BookDetail }) {
  const [draft, setDraft] = useState<Draft>(() => fromBook(book));
  const [genreHint, setGenreHint] = useState(false);
  const [confirmName, setConfirmName] = useState('');
  const [showReapply, setShowReapply] = useState(false);
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const valid = draft.title_zh.trim().length > 0;

  const { state, rebase, dirty } = useAutosave(draft, async (d, prev) => {
    const body = diffDraft(d, prev);
    if (Object.keys(body).length === 0) return;
    await api.patch(`/books/${book.id}`, body);
    // xưng hô đổi, hoặc thể loại đổi (thể loại ảnh hưởng tự chọn route)
    if ((body.run_config as { honorific?: unknown } | undefined)?.honorific || body.genre !== undefined) setShowReapply(true);
    void qc.invalidateQueries({ queryKey: ['book', book.id] });
  }, 500, valid); // BR-3.13

  // Server đổi (book.updated_at) và không còn sửa chưa lưu: nạp lại nháp từ server.
  const seen = useRef(book.updated_at);
  useEffect(() => {
    if (seen.current === book.updated_at) return;
    seen.current = book.updated_at;
    if (dirty()) return;
    const next = fromBook(book);
    rebase(next);
    setDraft(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [book.updated_at]);

  const applyGenreDefaults = async () => {
    const cfg = await api.get<RunConfig>(`/run-config/defaults${qs({ genre: draft.genre })}`);
    setDraft((d) => ({ ...d, kinship: cfg.honorific.kinship, pronoun: cfg.honorific.pronoun, modern: cfg.honorific.modern_stable }));
    setGenreHint(false);
  };

  const reapply = useMutation({
    mutationFn: () => api.post(`/books/${book.id}/honorific/reapply`, {}),
    onSuccess: () => { setShowReapply(false); toast('Đã xếp việc áp lại xưng hô'); void qc.invalidateQueries({ queryKey: ['book', book.id] }); },
    onError: (e) => toast(errorMessage(e)),
  });
  const doneCount = (book.stats?.translated ?? 0) + (book.stats?.needs_review ?? 0) + (book.stats?.reviewed ?? 0);

  const remove = useMutation({
    mutationFn: () => api.del(`/books/${book.id}${qs({ confirm: confirmName })}`),
    onSuccess: () => { toast('Đã xoá workspace (thư mục đã vào thùng rác)'); void qc.invalidateQueries({ queryKey: ['books'] }); navigate('/'); },
    onError: (e) => toast(errorMessage(e)),
  });
  const bookName = book.title_vi || book.title_zh;

  return (
    <div style={{ display: 'grid', gap: 12, maxWidth: 720 }}>
      <div className="row"><span className="grow" /><span className="saved" aria-live="polite">
        {!valid ? 'Tên gốc không được trống' : state === 'saving' ? 'Đang lưu…' : state === 'saved' ? 'Đã lưu' : state === 'error' ? 'Lưu lỗi' : ''}
      </span></div>
      <div className="panel">
        <h2>HachimiMT-60</h2>
        <div className="two">
          <div className="field">
            <label htmlFor="sBeam">Beam</label>
            <div className="range"><input id="sBeam" type="range" min={1} max={4} value={draft.beam} onChange={(e) => set('beam', Number(e.target.value))} /><output>{draft.beam}</output></div>
          </div>
          <div className="field">
            <label htmlFor="sBatch">Batch</label>
            <label className="check"><input type="checkbox" checked={draft.batchAuto} onChange={(e) => set('batchAuto', e.target.checked)} /> Tự động</label>
            <div className="range"><input id="sBatch" type="range" min={4} max={128} step={4} disabled={draft.batchAuto} value={draft.batchSize}
              onChange={(e) => set('batchSize', Number(e.target.value))} /><output>{draft.batchSize}</output></div>
          </div>
        </div>
        <div className="field"><span className="lbl">Chia chunk</span>
          <Segmented ariaLabel="Chia chunk" value={draft.chunk} onChange={(v) => set('chunk', v)}
            options={[{ value: 'sentence', label: 'Theo câu' }, { value: 'paragraph', label: 'Theo đoạn' }]} /></div>
        <div className="field">
          <label htmlFor="sNorm">Chuẩn hoá chữ Hán</label>
          <select id="sNorm" value={draft.han} onChange={(e) => set('han', e.target.value as Draft['han'])}>
            <option value="auto">Tự động phồn → giản</option><option value="t2s">Ép phồn → giản</option><option value="none">Giữ nguyên</option>
          </select>
        </div>
        <div className="hint">Cấu hình mới chỉ áp cho job tạo sau đó.</div>
      </div>
      <div className="panel">
        <h2>Model dịch mặc định</h2>
        <Segmented ariaLabel="Model dịch mặc định" value={draft.engine} onChange={(v) => set('engine', v)}
          options={[{ value: 'ct2', label: 'HachimiMT-60 (local)' }, { value: 'deepseek', label: 'DeepSeek API' }]} />
        <div className="two">
          <div className="field">
            <label htmlFor="sDsModel">Model DeepSeek</label>
            <select id="sDsModel" value={draft.dsModel} onChange={(e) => set('dsModel', e.target.value as DeepSeekModelId)}>
              <option value="deepseek-v4-pro">deepseek-v4-pro</option>
              <option value="deepseek-flash">deepseek-flash</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="sConc">Số chương song song</label>
            <div className="range"><input id="sConc" type="range" min={1} max={8} value={draft.concurrency}
              onChange={(e) => set('concurrency', Number(e.target.value))} /><output>{draft.concurrency}</output></div>
          </div>
        </div>
        <label className="check"><input type="checkbox" checked={draft.chain} onChange={(e) => set('chain', e.target.checked)} /> Nối ngữ cảnh chương trước</label>
        <DeepSeekKeyLine />
        <div className="hint">Dịch bằng DeepSeek gửi nội dung chương tới DeepSeek API và tính phí theo token. Chương DeepSeek không chạy lớp chuẩn hoá xưng hô; xưng hô do prompt nền điều khiển.</div>
      </div>
      <FoundationPanel bookId={book.id} />
      <div className="panel">
        <h2>Soát bằng DeepSeek</h2>
        <label className="check"><input type="checkbox" checked={draft.reviewAuto} onChange={(e) => set('reviewAuto', e.target.checked)} /> Tự soát sau khi HachimiMT dịch xong</label>
        <div className="two">
          <div className="field">
            <label htmlFor="sRvModel">Model soát</label>
            <select id="sRvModel" value={draft.reviewModel} onChange={(e) => set('reviewModel', e.target.value as DeepSeekModelId)}>
              <option value="deepseek-flash">deepseek-flash</option>
              <option value="deepseek-v4-pro">deepseek-v4-pro</option>
            </select>
          </div>
          <div className="field">
            <span className="lbl">Tự áp đề xuất</span>
            <Segmented ariaLabel="Tự áp đề xuất" value={draft.reviewApply} onChange={(v) => set('reviewApply', v)}
              options={[{ value: 'none', label: 'Không' }, { value: 'high_confidence', label: 'Chỉ fix tin cậy cao' }]} />
          </div>
        </div>
      </div>
      <CostPanel bookId={book.id} />
      <div className="panel">
        <h2>Chuẩn hoá xưng hô</h2>
        <label className="check"><input type="checkbox" checked={draft.kinship} onChange={(e) => set('kinship', e.target.checked)} /> Thân tộc (tỷ / muội / ca ca…)</label>
        <label className="check"><input type="checkbox" checked={draft.pronoun} onChange={(e) => set('pronoun', e.target.checked)} /> Đại từ (ngươi / hắn / nàng / ta)</label>
        <label className="check"><input type="checkbox" checked={draft.modern} onChange={(e) => set('modern', e.target.checked)} /> Ổn định ngôi hiện đại</label>
        {showReapply && doneCount > 0 && (
          <div className="banner info">
            <span className="grow">Áp lại cho {doneCount} chương đã dịch?</span>
            <button className="btn sm" onClick={() => setShowReapply(false)}>Để sau</button>
            <button className="btn sm primary" disabled={reapply.isPending} onClick={() => reapply.mutate()}>Áp lại</button>
          </div>
        )}
      </div>
      <div className="panel">
        <h2>Thông tin truyện</h2>
        <div className="two">
          <div className="field"><label htmlFor="sZh">Tên gốc</label><input id="sZh" type="text" className="zh" value={draft.title_zh} onChange={(e) => set('title_zh', e.target.value)} /></div>
          <div className="field"><label htmlFor="sVi">Tên tiếng Việt</label><input id="sVi" type="text" value={draft.title_vi} onChange={(e) => set('title_vi', e.target.value)} /></div>
          <div className="field"><label htmlFor="sAu">Tác giả</label><input id="sAu" type="text" value={draft.author} onChange={(e) => set('author', e.target.value)} /></div>
          <div className="field">
            <label htmlFor="sGenre">Thể loại</label>
            <select id="sGenre" value={draft.genre} onChange={(e) => { set('genre', e.target.value as Genre); setGenreHint(true); }}>
              {GENRES.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
            </select>
          </div>
        </div>
        {genreHint && (
          <div className="banner info">
            <span className="grow">Áp cấu hình xưng hô mặc định cho thể loại này?</span>
            <button className="btn sm" onClick={() => setGenreHint(false)}>Không</button>
            <button className="btn sm primary" onClick={() => void applyGenreDefaults()}>Áp</button>
          </div>
        )}
        <div className="field"><label htmlFor="sNote">Ghi chú</label><textarea id="sNote" rows={2} value={draft.note} onChange={(e) => set('note', e.target.value)} /></div>
        <div className="hint mono">{book.source_dir}</div>
      </div>
      <div className="panel" style={{ borderColor: 'var(--err)' }}>
        <h2>Vùng nguy hiểm</h2>
        <div className="field">
          <label htmlFor="sDel">Gõ lại tên truyện “{bookName}” để xoá workspace</label>
          <input id="sDel" type="text" value={confirmName} onChange={(e) => setConfirmName(e.target.value)} />
        </div>
        <button className="btn danger" style={{ justifySelf: 'start' }}
          disabled={confirmName.trim() !== bookName.trim() || remove.isPending} onClick={() => remove.mutate()}>Xoá workspace</button>
        <div className="hint">Thư mục truyện được chuyển vào thùng rác, không xoá hẳn.</div>
      </div>
    </div>
  );
}
