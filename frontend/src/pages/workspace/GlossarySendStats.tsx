import { useQuery } from '@tanstack/react-query';
import { api } from '../../api/client';
import type { SendStats } from '../../api/types';
import { fmtNum, fmtPct } from '../../lib/format';

/** AC-8.13: số liệu glossary gửi cho DeepSeek, để quyết định có làm BR-8.3e không. */
export default function GlossarySendStats({ bookId }: { bookId: string }) {
  const q = useQuery({ queryKey: ['glossary-send-stats', bookId], queryFn: () => api.get<SendStats>(`/books/${bookId}/glossary/send-stats`) });
  const s = q.data;
  if (!s || !s.chapters) return null;
  return (
    <div className="hint">
      Glossary gửi DeepSeek ({fmtNum(s.chapters)} chương gần nhất): trung bình ~{fmtNum(Math.round(s.avg_tokens_est ?? 0))} token ·
      gửi {s.avg_sent} term · bỏ {s.avg_skipped_predictable} term tự đoán được · cắt {s.avg_truncated} ·
      glossary_miss {fmtPct(s.miss_pct)} · tự sửa {fmtPct(s.autofixed_pct)}
    </div>
  );
}
