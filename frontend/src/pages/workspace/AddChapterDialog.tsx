import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import ConfirmDialog from '../../components/ConfirmDialog';
import Segmented from '../../components/Segmented';
import { useToast } from '../../components/Toast';

export default function AddChapterDialog({ bookId, total, onClose }: { bookId: string; total: number; onClose: () => void }) {
  const [how, setHow] = useState<'paste' | 'files'>('paste');
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [after, setAfter] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const afterNo = after.trim() === '' ? null : Number(after);

  const add = useMutation({
    mutationFn: () => {
      if (how === 'paste') return api.post<{ chapters: unknown[] }>(`/books/${bookId}/chapters`, { title_zh: title || null, content, after_no: afterNo });
      const form = new FormData();
      files.forEach((f) => form.append('files[]', f, f.name));
      if (afterNo !== null) form.set('after_no', String(afterNo));
      return api.post<{ chapters: unknown[] }>(`/books/${bookId}/chapters`, form);
    },
    onSuccess: (r) => {
      toast(`Đã thêm ${r.chapters.length} chương`);
      void qc.invalidateQueries({ queryKey: ['chapters'] });
      void qc.invalidateQueries({ queryKey: ['book'] });
      onClose();
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const ready = how === 'paste' ? content.trim().length > 0 : files.length > 0;

  return (
    <ConfirmDialog title="Thêm chương" onClose={onClose}
      actions={[{ label: 'Thêm', primary: true, disabled: !ready || add.isPending, onClick: () => add.mutate() }]}>
      <Segmented ariaLabel="Cách thêm" full value={how} onChange={setHow}
        options={[{ value: 'paste', label: 'Dán văn bản' }, { value: 'files', label: 'Upload file' }]} />
      {how === 'paste' ? (
        <>
          <div className="field"><label htmlFor="acTitle">Tiêu đề (gốc)</label><input id="acTitle" type="text" className="zh" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="field"><label htmlFor="acBody">Nội dung</label><textarea id="acBody" rows={8} className="zh" value={content} onChange={(e) => setContent(e.target.value)} /></div>
        </>
      ) : (
        <input type="file" accept=".txt,.md" multiple aria-label="Chọn file chương" onChange={(e) => setFiles([...(e.target.files ?? [])])} />
      )}
      <div className="field">
        <label htmlFor="acAfter">Vị trí</label>
        <input id="acAfter" type="number" min={0} max={total} placeholder={`Cuối truyện (sau chương ${total})`} value={after} onChange={(e) => setAfter(e.target.value)} />
        <span className="hint">Để trống: thêm vào cuối. Nhập số N: chèn sau chương N, các chương sau được đánh số lại.</span>
      </div>
      {error && <span className="err-text">{error}</span>}
    </ConfirmDialog>
  );
}
