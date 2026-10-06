import { memo, useMemo } from 'react';
import type { Segment } from '../../api/types';
import { diffTokens, normCmp } from '../../lib/diff';
import { ENGINE_LABEL, versionOf, type Engine } from './VersionRow';

interface Props {
  seg: Segment;
  /** Câu khác nhau đang đứng (nút / phím Câu khác trước / sau). */
  current: boolean;
  /** Chương đã dịch và không bận (BR-6.14). */
  canUse: boolean;
  onUse: (idx: number, text: string) => void;
}

function CompareRow({ seg, current, canUse, onUse }: Props) {
  const diff = useMemo(
    () => (seg.mt_ai_differ && seg.dst_mt != null && seg.dst_ai != null ? diffTokens(seg.dst_mt, seg.dst_ai) : null),
    [seg.dst_mt, seg.dst_ai, seg.mt_ai_differ],
  );
  if (seg.is_meta) {
    return (
      <div className="cmp-row meta">
        <span className="n">{seg.idx}</span>
        <div className="src">{seg.src || ' '}</div>
      </div>
    );
  }
  const main = seg.dst === null ? null : normCmp(seg.dst);
  const side = (engine: Engine) => {
    const text = versionOf(seg, engine);
    const label = ENGINE_LABEL[engine];
    const parts = diff ? diff[engine === 'mt' ? 'a' : 'b'] : null;
    const inUse = text !== null && main !== null && normCmp(text) === main;
    return (
      <div className={`ver ver-${engine}`} role="group" aria-label={`${label} câu ${seg.idx}`}>
        <span className="ver-tag">{label}</span>
        <div className={`ver-text${text === null ? ' pending' : ''}`}>
          {text === null
            ? `chưa có bản dịch ${label}`
            : parts
              ? parts.map((p, i) => (p.changed ? <span key={i} className={`d-${engine}`}>{p.text}</span> : p.text))
              : text}
        </div>
        {text !== null && (inUse
          ? <span className="hint">✓ Đang dùng</span>
          : (
            <div>
              <button className="btn sm" disabled={!canUse} aria-label={`Dùng bản ${label} cho câu ${seg.idx}`}
                title={canUse ? undefined : 'Chương đang dịch hoặc chưa dịch'} onClick={() => onUse(seg.idx, text)}>
                Dùng bản này
              </button>
            </div>
          ))}
      </div>
    );
  };
  return (
    <div className={`cmp-row vs${seg.mt_ai_differ ? ' diff' : ''}${current ? ' current' : ''}`} data-row={seg.idx}>
      <span className="n">{seg.idx}</span>
      <div className="src" lang="zh" data-idx={seg.idx}>{seg.src}</div>
      <div className="vers">{side('mt')}{side('ai')}</div>
    </div>
  );
}

export default memo(CompareRow);
