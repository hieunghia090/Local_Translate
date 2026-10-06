import { useMutation, useQuery } from '@tanstack/react-query';
import { api, errorMessage } from '../../api/client';
import type { ConnectionTest, DeepSeekStatus } from '../../api/types';
import { useToast } from '../../components/Toast';

/** 00 mục 9: chỉ hiện "Đã có key · ••••abcd" hoặc "Chưa có key"; không cho nhập key. */
export default function DeepSeekKeyLine() {
  const toast = useToast();
  const status = useQuery({ queryKey: ['deepseek-status'], queryFn: () => api.get<DeepSeekStatus>('/deepseek/status') });
  const test = useMutation({
    mutationFn: () => api.post<ConnectionTest>('/deepseek/test'),
    onSuccess: (r) => toast(r.ok ? `Kết nối DeepSeek được (${r.model}, ${r.latency_ms} ms)` : `DeepSeek lỗi: ${r.message ?? r.status}`),
    onError: (e) => toast(errorMessage(e)),
  });
  const s = status.data;
  return (
    <div className="row">
      <span className="hint grow">
        {s ? (s.key_present ? `Đã có key · ${s.key_masked}` : 'Chưa có key: thêm DEEPSEEK_API_KEY vào file .env rồi khởi động lại app.') : ''}
      </span>
      <button className="btn sm" disabled={!s?.key_present || test.isPending} onClick={() => test.mutate()}>Kiểm tra kết nối</button>
    </div>
  );
}
