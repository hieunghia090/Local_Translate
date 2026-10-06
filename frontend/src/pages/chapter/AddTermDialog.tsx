import { useMutation, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { GlossaryCategory } from '../../api/types';
import ConfirmDialog from '../../components/ConfirmDialog';
import { CATEGORY_LABEL } from '../../lib/format';

export default function AddTermDialog(props: { bookId: string; src: string; onClose: () => void; onSaved: () => void }) {
  const suggestion = useQuery({ queryKey: ['snippet', props.src], queryFn: () => api.post<{ vi: string }>('/translate/snippet', { text: props.src }) });
  const [dst, setDst] = useState<string | null>(null);
  const [category, setCategory] = useState<GlossaryCategory>('character');
  const [error, setError] = useState<string | null>(null);
  const value = dst ?? suggestion.data?.vi ?? '';
  const save = useMutation({
    mutationFn: () => api.post(`/books/${props.bookId}/glossary`, { src_zh: props.src, dst_vi: value, category }),
    onSuccess: props.onSaved,
    onError: (e) => setError(errorMessage(e)),
  });
  return (
    <ConfirmDialog title="Thêm vào glossary" onClose={props.onClose}
      actions={[{ label: 'Lưu thuật ngữ', primary: true, disabled: !value.trim() || save.isPending, onClick: () => save.mutate() }]}>
      <div className="field"><label htmlFor="atSrc">Nguồn Trung</label><input id="atSrc" type="text" className="zh" value={props.src} readOnly /></div>
      <div className="field"><label htmlFor="atDst">Đích Việt</label>
        <input id="atDst" type="text" value={value} placeholder={suggestion.isPending ? 'Đang gợi ý…' : ''} onChange={(e) => setDst(e.target.value)} autoFocus /></div>
      <div className="field"><label htmlFor="atCat">Loại</label>
        <select id="atCat" value={category} onChange={(e) => setCategory(e.target.value as GlossaryCategory)}>
          {(Object.keys(CATEGORY_LABEL) as GlossaryCategory[]).map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c]}</option>)}
        </select></div>
      {error && <span className="err-text">{error}</span>}
    </ConfirmDialog>
  );
}
