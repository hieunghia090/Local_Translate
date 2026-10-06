import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '../../api/client';
import type { AcceptResult, SuggestionList } from '../../api/types';
import SuggestionTable, { defaultPicked, PAGE_SIZE, type SuggestionEdit } from '../../components/SuggestionTable';
import { LOW_CONFIDENCE } from '../../lib/format';
import { useToast } from '../../components/Toast';
import ExtractDialog from './ExtractDialog';

const ACTIVE = ['queued', 'running', 'paused'];
const POLLING = ['queued', 'running']; // job tạm dừng không đổi cho tới khi người dùng tiếp tục: không cần hỏi lại mỗi giây

/** Spec 04 mục 3: "Trích thêm từ các chương mới bằng DeepSeek" và bảng đề xuất chờ duyệt (BR-4.7, BR-4.8). */
export default function SuggestionsPanel({ bookId }: { bookId: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [open, setOpen] = useState(true);
  const [dialog, setDialog] = useState(false);
  const [picked, setPicked] = useState<Set<string> | null>(null); // null = theo mặc định (tin cậy ≥ 80)
  const [edits, setEdits] = useState<Record<string, SuggestionEdit>>({});
  const [page, setPage] = useState(0);
  const q = useQuery({
    queryKey: ['glossary-suggestions', bookId, page],
    queryFn: () => api.get<SuggestionList>(`/books/${bookId}/glossary/suggestions?status=pending&limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (POLLING.includes(query.state.data?.last_job?.status ?? '') ? 1000 : false),
  });
  const items = q.data?.items ?? [];
  const total = q.data?.total ?? items.length;
  const chosen = picked ?? defaultPicked(items);
  const confidence = useRef(new Map<string, number>()); // tin cậy của mọi mục đã tải, để biết bản sửa nào thuộc mục chọn sẵn
  for (const s of items) confidence.current.set(s.id, s.confidence);
  const job = q.data?.last_job ?? null;
  const prev = useRef<string | null>(null);
  useEffect(() => { // BR-4.10: job vừa chuyển sang lỗi thì báo kèm link Console logs
    const status = job?.status ?? null;
    if (prev.current && prev.current !== 'failed' && status === 'failed') {
      toast(`Trích xuất bằng AI lỗi: ${job?.error ?? ''}`, { to: `/books/${bookId}/logs`, label: 'Xem Console logs' });
    }
    prev.current = status;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.status]);
  const refresh = () => {
    setPage(0);
    confidence.current.clear();
    setPicked(null);
    setEdits({});
    void qc.invalidateQueries({ queryKey: ['glossary-suggestions', bookId] });
    void qc.invalidateQueries({ queryKey: ['glossary', bookId] });
  };
  const accept = useMutation({
    mutationFn: () => {
      if (picked !== null) { // chọn tay: gửi đúng các id đã chọn (tối đa 2000)
        return api.post<AcceptResult>(`/books/${bookId}/glossary/suggestions/accept`, {
          items: [...picked].map((id) => ({ id, ...edits[id] })),
        });
      }
      // chọn mặc định (tin cậy ≥ 80) trên mọi trang: bản sửa đi theo items, phần còn lại do server lọc
      const edited = Object.keys(edits).filter((id) => (confidence.current.get(id) ?? 0) >= LOW_CONFIDENCE);
      return api.post<AcceptResult>(`/books/${bookId}/glossary/suggestions/accept`, {
        items: edited.map((id) => ({ id, ...edits[id] })), filter: { min_confidence: LOW_CONFIDENCE, exclude_ids: edited },
      });
    },
    onSuccess: (r) => { toast(`Đã thêm ${r.added} thuật ngữ${r.existing ? `, ${r.existing} mục đã có trong glossary` : ''}`); refresh(); },
    onError: (e) => toast(errorMessage(e)),
  });
  const reject = useMutation({
    mutationFn: (body: { ids: string[] } | { filter: Record<string, never> }) =>
      api.post<{ rejected: number }>(`/books/${bookId}/glossary/suggestions/reject`, body),
    onSuccess: refresh,
    onError: (e) => toast(errorMessage(e)),
  });
  return (
    <div className="panel" style={{ gap: 8 }}>
      <div className="row">
        <span className="hint grow">Trích thêm từ các chương mới bằng DeepSeek.</span>
        <button className="btn sm" onClick={() => setDialog(true)}>✦ Trích xuất bằng AI</button>
      </div>
      {job && ACTIVE.includes(job.status) && (
        <div className="hint">{job.status === 'paused' ? `Trích xuất đang tạm dừng (${job.progress}%). Tiếp tục hàng đợi để chạy tiếp.` : `Đang trích xuất… ${job.progress}%`}</div>
      )}
      {(items.length > 0 || total > 0) && (
        <>
          <div className="row">
            <button className="btn sm" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? '▼' : '▶'} Đề xuất từ AI · {items[0].model ?? 'DeepSeek'} ({total})
            </button>
            <span className="grow" />
            <button className="btn sm primary"
              disabled={(picked !== null ? !picked.size || picked.size > 2000 : total === 0) || accept.isPending}
              title={picked !== null && picked.size > 2000 ? 'Chọn tay tối đa 2.000 mục mỗi lần' : undefined} onClick={() => accept.mutate()}>Chấp nhận các mục đã chọn</button>
            <button className="btn sm" disabled={reject.isPending} onClick={() => reject.mutate({ filter: {} })}>Bỏ hết</button>
          </div>
          {open && (
            <SuggestionTable rows={items} picked={chosen} onPick={setPicked} edits={edits}
              onEdit={(id, e) => setEdits({ ...edits, [id]: { ...edits[id], ...e } })} onReject={(id) => reject.mutate({ ids: [id] })}
              pager={{ page, pageSize: PAGE_SIZE, total, onPage: setPage }} />
          )}
        </>
      )}
      {dialog && <ExtractDialog bookId={bookId} onClose={() => setDialog(false)} />}
    </div>
  );
}
