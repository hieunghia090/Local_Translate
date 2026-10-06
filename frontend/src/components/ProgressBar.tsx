import type { Stats } from '../api/types';

const pct = (n: number, total: number) => (total ? `${Math.round((n * 1000) / total) / 10}%` : '0%');

export default function ProgressBar({ stats, height = 6 }: { stats: Stats; height?: number }) {
  const done = stats.translated + stats.reviewed;
  return (
    <div className="bar" style={{ height }} role="img"
      aria-label={`Đã dịch ${done + stats.needs_review}/${stats.total} chương`}>
      <i className="b-ok" style={{ width: pct(done, stats.total) }} />
      <i className="b-rev" style={{ width: pct(stats.needs_review, stats.total) }} />
      <i className="b-run" style={{ width: pct(stats.translating, stats.total) }} />
    </div>
  );
}
