import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { AiModel, UsageView } from '../../api/types';
import { useToast } from '../../components/Toast';
import { fmtNum, fmtPct, fmtUsd } from '../../lib/format';

type PriceField = 'price_in_per_mtok' | 'price_in_cached_per_mtok' | 'price_out_per_mtok';
const PRICE_LABEL: Record<PriceField, string> = { price_in_per_mtok: 'Giá vào', price_in_cached_per_mtok: 'Giá vào (cache)', price_out_per_mtok: 'Giá ra' };
const SOURCE_LABEL: Record<string, string> = { translate: 'dịch', review: 'soát', glossary: 'glossary', system: 'hệ thống' };

export default function CostPanel({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const usage = useQuery({ queryKey: ['usage', bookId, ''], queryFn: () => api.get<UsageView>(`/books/${bookId}/usage`) });
  const models = useQuery({ queryKey: ['ai-models'], queryFn: () => api.get<{ items: AiModel[] }>('/ai-models') });
  const patch = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Record<PriceField, number>> }) => api.patch<AiModel>(`/ai-models/${id}`, body),
    onSuccess: () => { toast('Đã lưu giá'); void qc.invalidateQueries({ queryKey: ['ai-models'] }); void qc.invalidateQueries({ queryKey: ['usage'] }); },
    onError: (e) => toast(errorMessage(e)),
  });
  const u = usage.data;
  return (
    <div className="panel">
      <h2>Chi phí DeepSeek</h2>
      <div className="row">
        <span className="grow">
          Tháng này: <b>{fmtUsd(u?.total?.cost_usd ?? 0)}</b> · {fmtNum(u?.total?.tokens_in ?? 0)} token vào ({fmtNum(u?.total?.tokens_in_cached ?? 0)} trúng cache) · {fmtNum(u?.total?.tokens_out ?? 0)} token ra
        </span>
        <Link to={`/books/${bookId}/logs`}>Xem Console logs</Link>
      </div>
      {(u?.items ?? []).length > 0 && (
        <div className="tbl"><table>
          <thead><tr><th>Model</th><th>Loại</th><th style={{ textAlign: 'right' }}>Request</th><th style={{ textAlign: 'right' }}>Token vào</th><th style={{ textAlign: 'right' }}>Token ra</th><th style={{ textAlign: 'right' }}>Chi phí</th></tr></thead>
          <tbody>{(u?.items ?? []).map((r) => (
            <tr key={`${r.model}:${r.source}`}>
              <td>{r.model}</td><td>{SOURCE_LABEL[r.source] ?? r.source}</td><td className="num">{fmtNum(r.requests)}</td>
              <td className="num">{fmtNum(r.tokens_in)}</td><td className="num">{fmtNum(r.tokens_out)}</td><td className="num">{fmtUsd(r.cost_usd)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <h3>Ước tính và thực tế</h3>
      <div className="tbl"><table>
        <thead><tr><th>Model</th><th style={{ textAlign: 'right' }}>Request</th><th style={{ textAlign: 'right' }}>Sai số vào</th><th style={{ textAlign: 'right' }}>Sai số ra</th><th style={{ textAlign: 'right' }}>Chữ / token</th><th style={{ textAlign: 'right' }}>Ra / vào</th></tr></thead>
        <tbody>{(u?.accuracy ?? []).map((a) => (
          <tr key={a.model}>
            <td>{a.model}</td><td className="num">{fmtNum(a.requests)}</td><td className="num">{fmtPct(a.err_in_pct)}</td><td className="num">{fmtPct(a.err_out_pct)}</td>
            <td className="num">{a.coefficients.han_per_token}{a.coefficients.calibrated ? '' : ' (mặc định)'}</td><td className="num">{a.coefficients.out_ratio}</td>
          </tr>
        ))}</tbody>
      </table></div>
      <div className="hint">Hệ số tự hiệu chỉnh từ 50 request gần nhất của truyện; dưới 10 request thì dùng mặc định 1,8 và 3,3.</div>
      <h3>Bảng giá (USD / 1 triệu token)</h3>
      {(models.data?.items ?? []).map((m) => (
        <div key={m.id} className="row">
          <span className="grow">{m.label} {m.prices_are_samples && <span className="flagtag">giá mẫu</span>}</span>
          {(Object.keys(PRICE_LABEL) as PriceField[]).map((f) => (
            <input key={`${f}:${m[f]}`} type="number" min={0} step="0.01" style={{ width: 90 }} defaultValue={m[f]}
              aria-label={`${PRICE_LABEL[f]} ${m.id}`} title={PRICE_LABEL[f]}
              onBlur={(e) => { const v = Number(e.target.value); if (e.target.value !== '' && v >= 0 && v !== m[f]) patch.mutate({ id: m.id, body: { [f]: v } }); }} />
          ))}
        </div>
      ))}
    </div>
  );
}
