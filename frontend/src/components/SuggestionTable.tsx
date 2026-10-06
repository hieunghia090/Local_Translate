import { useState } from 'react';
import type { GlossaryCategory } from '../api/types';
import { CATEGORY_LABEL, fmtNum, LOW_CONFIDENCE } from '../lib/format';

export interface SuggestionRow {
  id: string; src_zh: string; dst_vi: string; category: GlossaryCategory; confidence: number;
  occurrence_count: number; context: string | null;
}
export type SuggestionEdit = { dst_vi?: string; category?: GlossaryCategory };

const CATEGORIES = Object.keys(CATEGORY_LABEL) as GlossaryCategory[];

/** BR-4.7: mặc định chỉ chọn mục có độ tin cậy từ 80. */
export const defaultPicked = (rows: SuggestionRow[]) => new Set(rows.filter((r) => r.confidence >= LOW_CONFIDENCE).map((r) => r.id));

export const PAGE_SIZE = 200;

/** `pager`: phân trang phía server (rows là đúng trang hiện tại). Không có thì tự chia trang 200 mục phía client. */
export interface SuggestionPager { page: number; pageSize: number; total: number; onPage: (page: number) => void }

export default function SuggestionTable({ rows: allRows, picked, onPick, edits, onEdit, onReject, pager }: {
  rows: SuggestionRow[]; picked: Set<string>; onPick: (next: Set<string>) => void;
  edits: Record<string, SuggestionEdit>; onEdit: (id: string, edit: SuggestionEdit) => void; onReject?: (id: string) => void;
  pager?: SuggestionPager;
}) {
  const [clientPage, setClientPage] = useState(0);
  const total = pager ? pager.total : allRows.length;
  const pageSize = pager ? pager.pageSize : PAGE_SIZE;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = pager ? pager.page : Math.min(clientPage, pages - 1);
  const rows = pager ? allRows : allRows.slice(page * pageSize, (page + 1) * pageSize);
  const goto = (p: number) => (pager ? pager.onPage(p) : setClientPage(p));
  const all = rows.length > 0 && rows.every((r) => picked.has(r.id));
  const toggle = (id: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(id); else next.delete(id);
    onPick(next);
  };
  return (
    <div className="tbl">
      <table>
        <thead>
          <tr>
            <th><input type="checkbox" aria-label="Chọn tất cả đề xuất" checked={all}
              onChange={(e) => {
                const next = new Set(picked); // chỉ đổi các mục của trang đang xem
                for (const r of rows) { if (e.target.checked) next.add(r.id); else next.delete(r.id); }
                onPick(next);
              }} /></th>
            <th>Nguồn</th><th>Đề xuất Việt</th><th>Loại</th><th style={{ textAlign: 'right' }}>Tin cậy</th>
            <th style={{ textAlign: 'right' }}>Xuất hiện</th><th>Ngữ cảnh</th>{onReject && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const e = edits[r.id] ?? {};
            const dst = e.dst_vi ?? r.dst_vi;
            return (
              <tr key={r.id}>
                <td><input type="checkbox" aria-label={`Chọn đề xuất ${r.src_zh}`} checked={picked.has(r.id)}
                  onChange={(ev) => toggle(r.id, ev.target.checked)} /></td>
                <td className="zh">{r.src_zh}</td>
                <td><input type="text" aria-label={`Đề xuất Việt cho ${r.src_zh}`} defaultValue={dst} key={`${r.id}:${dst}`}
                  onBlur={(ev) => {
                    const v = ev.target.value.trim();
                    if (!v) ev.target.value = dst;
                    else if (v !== dst) onEdit(r.id, { dst_vi: v });
                  }} /></td>
                <td><select aria-label={`Loại đề xuất ${r.src_zh}`} value={e.category ?? r.category} style={{ width: 'auto' }}
                  onChange={(ev) => onEdit(r.id, { category: ev.target.value as GlossaryCategory })}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
                </select></td>
                <td className={`num${r.confidence < LOW_CONFIDENCE ? ' conf-low' : ''}`}>{r.confidence}</td>
                <td className="num">{fmtNum(r.occurrence_count ?? 0)}</td>
                <td className="zh hint">{r.context ?? ''}</td>
                {onReject && <td><button className="btn sm" aria-label={`Bỏ đề xuất ${r.src_zh}`} onClick={() => onReject(r.id)}>✕</button></td>}
              </tr>
            );
          })}
        </tbody>
      </table>
      {total > pageSize && (
        <div className="row" style={{ justifyContent: 'center', padding: 8 }}>
          <button className="btn sm" aria-label="Trang trước" disabled={page <= 0} onClick={() => goto(page - 1)}>‹</button>
          <span className="hint">Trang {page + 1}/{pages} · {fmtNum(total)} mục</span>
          <button className="btn sm" aria-label="Trang sau" disabled={page >= pages - 1} onClick={() => goto(page + 1)}>›</button>
        </div>
      )}
    </div>
  );
}
