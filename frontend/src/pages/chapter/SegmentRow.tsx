import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import type { GlossarySpan, HonorificEdit, ReviewFix, Segment } from '../../api/types';
import { FIX_TYPE_LABEL } from '../../lib/format';

const FLAG_LABEL: Record<string, string> = {
  truncated: 'bị cắt', placeholder_lost: 'mất thuật ngữ', retried: 'dịch lại', empty_output: 'trống',
  glossary_miss: 'bỏ qua glossary', glossary_autofixed: 'tự sửa glossary', residual_han: 'còn chữ Hán',
  fallback_ct2: 'dịch bằng HachimiMT', review_pending: 'có đề xuất',
};
const FLAG_TITLE: Record<string, string> = {
  glossary_autofixed: 'DeepSeek dùng sai thuật ngữ, app đã tự thay bằng bản trong glossary',
  residual_han: 'Câu dịch còn nhiều chữ Hán',
  fallback_ct2: 'DeepSeek bỏ sót dòng này, đã dịch bằng HachimiMT',
  review_pending: 'DeepSeek có đề xuất sửa câu này',
};
const SAVE_DELAY = 800;

function highlight(text: string, spans: { range: [number, number]; title?: string }[], tag: 'u' | 'mark'): ReactNode[] {
  const out: ReactNode[] = [];
  let pos = 0;
  [...spans].sort((a, b) => a.range[0] - b.range[0]).forEach(({ range: [s, e], title }, i) => {
    if (s < pos || e > text.length) return;
    if (s > pos) out.push(text.slice(pos, s));
    out.push(tag === 'u'
      ? <u key={i} className="gl" title={title}>{text.slice(s, e)}</u>
      : <mark key={i}>{text.slice(s, e)}</mark>);
    pos = e;
  });
  out.push(text.slice(pos));
  return out;
}

const RULE_LABEL = (rule: string) => rule.startsWith('kinship') ? 'thân tộc' : rule.startsWith('pronoun') ? 'đại từ' : 'ngôi hiện đại';

interface ShownEdit { edit: HonorificEdit; pos: number }

/**
 * Vị trí hiện tại của từng chỗ chuẩn hoá trong `text`. Offset của server tính trên bản máy; mỗi chỗ đã bỏ
 * (đang là `from`) làm các chỗ sau lệch `len(from) - len(to)`. Chỉ giữ chỗ mà `to` thật sự nằm đúng vị trí.
 */
function locateEdits(text: string, edits: HonorificEdit[]): ShownEdit[] {
  const out: ShownEdit[] = [];
  let shift = 0;
  for (const edit of [...edits].sort((a, b) => a.offset - b.offset)) {
    const pos = edit.offset + shift;
    if (text.slice(pos, pos + edit.to.length) === edit.to) out.push({ edit, pos });
    else if (text.slice(pos, pos + edit.from.length) === edit.from) shift += edit.from.length - edit.to.length;
  }
  return out;
}

/** Cột dịch: mark (glossary) + span.hon (xưng hô đã chuẩn hoá). Chồng nhau thì mark thắng. */
function buildDst(text: string, marks: [number, number][], edits: HonorificEdit[], onOpen: (e: HonorificEdit) => void): ReactNode[] {
  const hons = locateEdits(text, edits)
    .map(({ edit, pos }) => ({ s: pos, e: pos + edit.to.length, edit }))
    .filter((h) => !marks.some(([ms, me]) => h.s < me && ms < h.e));
  const items = [...marks.map(([s, e]) => ({ s, e, edit: null as HonorificEdit | null })), ...hons].sort((a, b) => a.s - b.s);
  const out: ReactNode[] = [];
  let pos = 0;
  items.forEach(({ s, e, edit }, i) => {
    if (s < pos || e > text.length) return;
    if (s > pos) out.push(text.slice(pos, s));
    out.push(edit
      ? <span key={i} className="hon" title={`${edit.from} → ${edit.to} · ${RULE_LABEL(edit.rule)}`} onClick={() => onOpen(edit)}>{text.slice(s, e)}</span>
      : <mark key={i}>{text.slice(s, e)}</mark>);
    pos = e;
  });
  out.push(text.slice(pos));
  return out;
}

interface Props {
  seg: Segment;
  editable: boolean;
  onSave: (idx: number, text: string) => Promise<unknown>;
  onRevert: (idx: number) => void;
  /** Cho trang cha gọi flush bản sửa đang chờ (vd. trước khi đánh dấu đã soát). */
  registerFlush?: (idx: number, flush: (() => Promise<unknown>) | null) => void;
  /** term_id -> dst_vi, để làm tooltip cho span glossary ở cột gốc. */
  terms?: Record<string, string>;
  /** Đề xuất sửa đang chờ duyệt của câu này (spec 06 mục 4). */
  fixes?: ReviewFix[];
  onDecide?: (ids: string[], action: 'apply' | 'reject') => void;
  /** Chương đang trong hàng đợi / đang dịch: không áp / bỏ đề xuất được (server trả 409). */
  busy?: boolean;
}

const EMPTY: Record<string, string> = {};

function SegmentRow({ seg, editable, onSave, onRevert, registerFlush, terms = EMPTY, fixes, onDecide, busy = false }: Props) {
  const [shown, setShown] = useState(seg.dst);
  const focused = useRef(false);
  const timer = useRef<number>();
  const dirty = useRef<string | null>(null);
  const lastServer = useRef(seg.dst);
  const content = useRef<ReactNode>();
  const [openEdit, setOpenEdit] = useState<HonorificEdit | null>(null);
  const saveRef = useRef(onSave);
  saveRef.current = onSave;
  const idxRef = useRef(seg.idx);
  idxRef.current = seg.idx;

  const flush = (): Promise<unknown> => {
    window.clearTimeout(timer.current);
    const text = dirty.current;
    dirty.current = null;
    if (text !== null && text !== (lastServer.current ?? '')) return saveRef.current(idxRef.current, text);
    return Promise.resolve();
  };

  const discard = () => { window.clearTimeout(timer.current); dirty.current = null; };

  useEffect(() => {
    // Bản server đổi (lưu xong, dịch lại, khôi phục): chỉ hiện ngay khi ô không đang được sửa.
    lastServer.current = seg.dst;
    if (!focused.current) setShown(seg.dst);
  }, [seg.dst]);

  useEffect(() => {
    registerFlush?.(seg.idx, flush);
    return () => registerFlush?.(seg.idx, null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seg.idx, registerFlush]);

  useEffect(() => () => { void flush(); }, []); // unmount: gửi bản sửa còn chờ (Review Focus 1)
  // eslint-disable-line react-hooks/exhaustive-deps

  if (seg.is_meta) {
    return (
      <div className="cmp-row meta">
        <span className="n">{seg.idx}</span>
        <div className="src">{seg.src || ' '}</div>
      </div>
    );
  }
  const untranslated = shown === null;
  const spans: GlossarySpan[] = seg.glossary_spans ?? [];
  const flagTitle = (f: string) => (f === 'glossary_miss'
    ? `Bỏ qua: ${spans.filter((sp) => sp.dst === null).map((sp) => terms[sp.term_id] ?? sp.term_id).join(', ') || 'thuật ngữ glossary'}`
    : FLAG_TITLE[f] ?? f);
  // Span dịch chỉ khớp khi chữ đang hiện chính là bản server hiện tại; đang sửa thì giữ nguyên nội dung cũ (không patch DOM đang gõ).
  if (!focused.current || content.current === undefined) {
    const fits = shown === seg.dst;
    const marks = fits ? spans.filter((sp) => sp.dst).map((sp) => sp.dst as [number, number]) : [];
    content.current = untranslated ? 'Chưa dịch' : buildDst(shown ?? '', marks, fits ? (seg.honorific_edits ?? []) : [], setOpenEdit);
  }
  const text = shown ?? '';
  const located = openEdit !== null && dirty.current === null && shown === seg.dst
    ? locateEdits(text, seg.honorific_edits ?? []).find((l) => l.edit === openEdit) : undefined;
  const undoEdit = () => {
    if (!openEdit || !located) return;
    const { edit, pos } = located;
    const next = text.slice(0, pos) + edit.from + text.slice(pos + edit.to.length);
    setOpenEdit(null);
    focused.current = false; // bấm nút nghĩa là rời ô: bản server mới được hiện ngay
    discard();
    void onSave(seg.idx, next);
  };
  return (
    <div className={`cmp-row${seg.flags.length ? ' flag' : ''}${fixes?.length ? ' fix' : ''}`} data-row={seg.idx}>
      <span className="n">{seg.idx}</span>
      <div className="src" lang="zh" data-idx={seg.idx}>
        {highlight(seg.src, spans.map((sp) => ({ range: sp.src, title: terms[sp.term_id] ? `→ ${terms[sp.term_id]}` : undefined })), 'u')}
      </div>
      <div>
        <div
          key={`v:${shown ?? ''}`}
          className={`dst${untranslated ? ' pending' : ''}`}
          data-idx={seg.idx}
          aria-label={`Bản dịch câu ${seg.idx}`}
          role="textbox"
          contentEditable={editable && !untranslated}
          suppressContentEditableWarning
          onInput={(e) => {
            dirty.current = (e.currentTarget.textContent ?? '').replace(/\n/g, ' ');
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(flush, SAVE_DELAY);
          }}
          onFocus={() => { focused.current = true; }}
          onBlur={() => { focused.current = false; void flush(); setShown(lastServer.current); }}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) e.preventDefault(); }}
        >
          {content.current}
        </div>
        {located && editable && (
          <div className="tools"><button className="btn sm" onClick={undoEdit}>Bỏ thay đổi này</button></div>
        )}
        {(seg.edited || seg.flags.length > 0) && (
          <div className="tools">
            {seg.edited && <span className="edited-mark" title="Đã sửa tay">✎</span>}
            {seg.flags.map((f) => <span key={f} className="flagtag" title={flagTitle(f)}>{FLAG_LABEL[f] ?? f}</span>)}
            {seg.edited && editable && (
              <button className="btn sm" onMouseDown={discard} onClick={() => { discard(); onRevert(seg.idx); }}>Khôi phục bản máy</button>
            )}
          </div>
        )}
        {(fixes ?? []).map((f) => (
          <div key={f.id} className="fixbox" role="group" aria-label={`Đề xuất sửa câu ${seg.idx}`}>
            <div className="hint">{FIX_TYPE_LABEL[f.type] ?? f.type} · tin cậy {f.confidence}{f.reason ? ` · ${f.reason}` : ''}</div>
            <div><del>{f.before}</del></div>
            <div><ins>{f.after}</ins></div>
            {onDecide && (
              <div className="row">
                <button className="btn sm primary" disabled={busy} title={busy ? 'Chương đang dịch, chưa áp được' : undefined}
                  onClick={() => onDecide([f.id], 'apply')}>Áp</button>
                <button className="btn sm" disabled={busy} onClick={() => onDecide([f.id], 'reject')}>Bỏ</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

export default memo(SegmentRow);
