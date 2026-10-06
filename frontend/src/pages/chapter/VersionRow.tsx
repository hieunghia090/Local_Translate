import { memo } from 'react';
import type { Segment } from '../../api/types';

export type Engine = 'mt' | 'ai';
export const ENGINE_LABEL: Record<Engine, string> = { mt: 'Hachimi', ai: 'AI' };
export const versionOf = (seg: Segment, engine: Engine): string | null =>
  (engine === 'mt' ? seg.dst_mt : seg.dst_ai) ?? null;

/** Chế độ Hachimi / AI: chỉ đọc (spec 06 mục 4a). */
function VersionRow({ seg, engine }: { seg: Segment; engine: Engine }) {
  if (seg.is_meta) {
    return (
      <div className="cmp-row meta">
        <span className="n">{seg.idx}</span>
        <div className="src">{seg.src || ' '}</div>
      </div>
    );
  }
  const text = versionOf(seg, engine);
  const label = ENGINE_LABEL[engine];
  return (
    <div className="cmp-row" data-row={seg.idx}>
      <span className="n">{seg.idx}</span>
      <div className="src" lang="zh" data-idx={seg.idx}>{seg.src}</div>
      <div className={`dst ro${text === null ? ' pending' : ''}`} aria-label={`Bản ${label} câu ${seg.idx}`}>
        {text ?? `chưa có bản dịch ${label}`}
      </div>
    </div>
  );
}

export default memo(VersionRow);
