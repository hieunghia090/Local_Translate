import type { ChapterStatus } from '../api/types';
import { STATUS } from '../lib/format';

export default function StatusChip({ status }: { status: ChapterStatus }) {
  const s = STATUS[status];
  return <span className={`chip ${s.cls}`}>{s.label}</span>;
}
