import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../api/client';
import type { ConnectionTest, DeepSeekModelId, DeepSeekStatus, ExtractEstimate, GlossaryCategory, PreviewResult } from '../api/types';
import Banner from '../components/Banner';
import CategoryPicker, { DEFAULT_CATEGORIES } from '../components/CategoryPicker';
import SuggestionTable, { defaultPicked, type SuggestionEdit } from '../components/SuggestionTable';
import { fmtNum, fmtUsd } from '../lib/format';

export interface AiExtractState {
  enabled: boolean; model: DeepSeekModelId; chapters: number; categories: GlossaryCategory[];
  preview: PreviewResult | null; picked: Set<string>; edits: Record<string, SuggestionEdit>;
}
export const DEFAULT_AI: AiExtractState = {
  enabled: true, model: 'deepseek-v4-pro', chapters: 20, categories: DEFAULT_CATEGORIES, preview: null, picked: new Set(), edits: {},
};

/** Khối ai_extract của POST /books (spec 02 mục 7). */
export function aiExtractPayload(s: AiExtractState) {
  if (!s.enabled) return { enabled: false };
  const ids = s.preview ? s.preview.items.filter((i) => s.picked.has(i.id)).map((i) => i.id) : [];
  return {
    enabled: s.enabled, provider: 'deepseek', model: s.model, chapters: s.chapters, categories: s.categories,
    accepted_preview_ids: ids, preview_edits: Object.fromEntries(Object.entries(s.edits).filter(([id]) => ids.includes(id))),
  };
}

/** Spec 02 mục 4, "Glossary ban đầu" (phần AI). */
export default function CreateBookAiBlock({ importId, ready, value, onChange }: {
  importId: string | null; ready: boolean; value: AiExtractState; onChange: (v: AiExtractState) => void;
}) {
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const status = useQuery({ queryKey: ['deepseek-status'], queryFn: () => api.get<DeepSeekStatus>('/deepseek/status') });
  const s = status.data;
  const set = (patch: Partial<AiExtractState>) => onChange({ ...value, ...patch });
  const test = useMutation({
    mutationFn: () => api.post<ConnectionTest>('/deepseek/test'),
    onSuccess: (r) => setTestMsg(r.ok ? { ok: true, text: `Kết nối được (${r.model}, ${r.latency_ms} ms)` }
      : { ok: false, text: `Lỗi${r.status ? ` ${r.status}` : ''}: ${r.message ?? 'không rõ'}` }),
    onError: (e) => setTestMsg({ ok: false, text: errorMessage(e) }),
  });
  const estimateBody = { provider: 'deepseek', model: value.model, chapters: value.chapters, categories: value.categories };
  const est = useQuery({
    queryKey: ['preview-estimate', importId, estimateBody],
    queryFn: () => api.post<ExtractEstimate>(`/imports/${importId}/glossary-preview/estimate`, estimateBody),
    enabled: value.enabled && !!importId && ready && value.categories.length > 0,
    retry: false,
  });
  const e = est.data;
  const run = useMutation({
    mutationFn: () => api.post<PreviewResult>(`/imports/${importId}/glossary-preview`, {
      provider: 'deepseek', model: value.model, chapters: value.chapters, categories: value.categories,
    }),
    onSuccess: (r) => { setRunError(null); onChange({ ...value, preview: r, picked: defaultPicked(r.items), edits: {} }); },
    onError: (e) => setRunError(errorMessage(e)),
  });
  return (
    <div className="panel">
      <h2>4. Glossary ban đầu</h2>
      <label className="check">
        <input type="checkbox" checked={value.enabled} onChange={(e) => set({ enabled: e.target.checked })} /> Trích xuất thuật ngữ bằng AI sau khi tạo
      </label>
      <div className="row">
        <span className="hint grow">{s ? (s.key_present ? `Đã có key · ${s.key_masked}` : 'Chưa có key') : ''}</span>
        <button className="btn sm" disabled={!s?.key_present || test.isPending} onClick={() => test.mutate()}>Thử kết nối</button>
      </div>
      {s && !s.key_present && (
        <Banner kind="warn">Chưa có key: thêm DEEPSEEK_API_KEY vào file .env rồi khởi động lại app. Vẫn tạo được truyện; job AI chờ tới khi có key.</Banner>
      )}
      {testMsg && <div className={testMsg.ok ? 'hint' : 'err-text'} role="status">{testMsg.text}</div>}
      {value.enabled && (
        <>
          <div className="two">
            <div className="field">
              <label htmlFor="aiModel">Model AI trích glossary</label>
              <select id="aiModel" value={value.model} onChange={(e) => set({ model: e.target.value as DeepSeekModelId })}>
                <option value="deepseek-v4-pro">deepseek-v4-pro</option>
                <option value="deepseek-flash">deepseek-flash</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="aiN">Số chương đầu cho AI đọc</label>
              <input id="aiN" type="number" min={5} max={100} value={value.chapters}
                onChange={(e) => set({ chapters: Math.min(100, Math.max(5, Number(e.target.value) || 5)) })} />
            </div>
          </div>
          <CategoryPicker value={value.categories} onChange={(categories) => set({ categories })} />
          <Banner kind="warn">Nội dung các chương được chọn sẽ gửi tới DeepSeek.</Banner>
          {importId && (
            <p className="hint" role="status">
              {e ? `Chi phí ước tính cho ${fmtNum(e.chapters)} chương: ~${fmtNum(e.tokens_in)} token vào · ~${fmtNum(e.tokens_out)} token ra · ~${fmtUsd(e.cost_usd)}${e.prices_are_samples ? ' (giá mẫu)' : ''} mỗi lượt. Chạy thử và job sau khi tạo mỗi cái tốn cỡ này.`
                : est.isError ? `Chưa ước tính được chi phí: ${errorMessage(est.error)}` : ready ? 'Đang ước tính chi phí…' : 'Chi phí ước tính sẽ hiện khi file phân tích xong.'}
            </p>
          )}
          <div className="row">
            <button className="btn sm" disabled={!importId || !ready || !s?.key_present || !value.categories.length || run.isPending}
              onClick={() => run.mutate()}>{run.isPending ? 'Đang chạy thử… (tối đa 120 giây)' : 'Chạy thử'}</button>
            <span className="hint">Chạy ngay trên {value.chapters} chương đầu, chưa lưu gì.</span>
          </div>
          {runError && <div className="err-text">{runError}</div>}
          {value.preview && (
            <>
              <div className="hint">
                {value.preview.items.length} đề xuất · {fmtNum(value.preview.tokens_in)} token vào · {fmtNum(value.preview.tokens_out)} token ra · {fmtUsd(value.preview.cost_usd)}.
                Mục đã chọn sẽ thành thuật ngữ khi tạo truyện.
              </div>
              <SuggestionTable rows={value.preview.items} picked={value.picked} onPick={(picked) => set({ picked })}
                edits={value.edits} onEdit={(id, e) => set({ edits: { ...value.edits, [id]: { ...value.edits[id], ...e } } })} />
            </>
          )}
        </>
      )}
    </div>
  );
}
