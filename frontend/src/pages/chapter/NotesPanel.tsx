import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { ChapterNote } from '../../api/types';
import { useToast } from '../../components/Toast';

/** Ghi chú `correction` cho lần dịch lại bằng DeepSeek (spec 06 mục 4, 08 AC-8.10). */
export default function NotesPanel({ chapterId }: { chapterId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState('');
  const notes = useQuery({ queryKey: ['notes', chapterId], queryFn: () => api.get<{ items: ChapterNote[] }>(`/chapters/${chapterId}/notes`) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ['notes', chapterId] });
  const onError = (e: unknown) => toast(errorMessage(e));
  const add = useMutation({
    mutationFn: (content: string) => api.post<ChapterNote>(`/chapters/${chapterId}/notes`, { type: 'correction', content }),
    onSuccess: () => { setText(''); refresh(); }, onError,
  });
  const toggle = useMutation({ mutationFn: (n: ChapterNote) => api.patch<ChapterNote>(`/notes/${n.id}`, { resolved: !n.resolved }), onSuccess: refresh, onError });
  const remove = useMutation({ mutationFn: (id: string) => api.del(`/notes/${id}`), onSuccess: refresh, onError });
  const items = notes.data?.items ?? [];
  return (
    <div className="panel">
      <h2>Ghi chú cho lần dịch lại</h2>
      <div className="side-list">
        {items.map((n) => (
          <div key={n.id} className="it">
            <span style={{ textDecoration: n.resolved ? 'line-through' : undefined }}>{n.content}</span>
            <span className="hint">{n.resolved ? 'đã xử lý' : 'sẽ gửi cho DeepSeek ở lần dịch lại'}</span>
            <div className="row">
              <button className="btn sm" onClick={() => toggle.mutate(n)}>{n.resolved ? 'Mở lại' : 'Đã xử lý'}</button>
              <button className="btn sm" aria-label={`Xoá ghi chú: ${n.content}`} onClick={() => remove.mutate(n.id)}>Xoá</button>
            </div>
          </div>
        ))}
        {items.length === 0 && <span className="hint">Chưa có ghi chú.</span>}
      </div>
      <textarea aria-label="Ghi chú sửa lỗi mới" rows={2} value={text} placeholder="Vd. Tên 高俅 phải là Cao Cầu, không phải Cao Cừu"
        onChange={(e) => setText(e.target.value)} />
      <button className="btn sm" style={{ justifySelf: 'start' }} disabled={!text.trim() || add.isPending}
        onClick={() => add.mutate(text.trim())}>＋ Thêm ghi chú</button>
      <span className="hint">Ghi chú chưa xử lý được đưa vào prompt lần dịch lại bằng DeepSeek, dịch xong thì tự chuyển sang đã xử lý.</span>
    </div>
  );
}
