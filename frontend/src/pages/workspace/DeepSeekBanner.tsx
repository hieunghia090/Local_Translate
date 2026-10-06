import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../../api/client';
import type { DeepSeekStatus } from '../../api/types';
import Banner from '../../components/Banner';
import { useToast } from '../../components/Toast';

/** BR-8.13, G5, BR-8.14: banner đỏ khi pool DeepSeek tự tạm dừng. Tạm dừng tay thì không hiện. */
export default function DeepSeekBanner({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ['deepseek-status'], queryFn: () => api.get<DeepSeekStatus>('/deepseek/status'), refetchInterval: 15_000 });
  const resume = useMutation({
    mutationFn: () => api.post('/queue/resume', { engine: 'deepseek' }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['deepseek-status'] }); void qc.invalidateQueries({ queryKey: ['queue'] }); },
    onError: (e) => toast(errorMessage(e)),
  });
  const s = status.data;
  if (!s?.paused || !s.paused_reason) return null;
  return (
    <Banner>
      <span className="grow">{s.message}</span>
      <Link className="btn sm" to={`/books/${bookId}/logs`}>Xem log</Link>
      <Link className="btn sm" to={`/books/${bookId}/settings`}>Cài đặt</Link>
      <button className="btn sm" disabled={resume.isPending} onClick={() => resume.mutate()}>▶ Tiếp tục pool DeepSeek</button>
    </Banner>
  );
}
