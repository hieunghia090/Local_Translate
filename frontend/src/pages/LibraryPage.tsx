import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { api, errorMessage, qs } from '../api/client';
import { useEvents } from '../api/events';
import type { BookItem, LibraryStats } from '../api/types';
import Banner from '../components/Banner';
import Breadcrumb from '../components/Breadcrumb';
import ProgressBar from '../components/ProgressBar';
import Segmented from '../components/Segmented';
import StatTiles from '../components/StatTiles';
import { fmtNum, genreLabel, relativeTime, STATE_LABEL } from '../lib/format';
import { useDebouncedValue, usePersistentState } from '../lib/hooks';

type Filter = 'all' | 'in_progress' | 'completed';
type Sort = 'recent' | 'name' | 'progress';

function BookCard({ book, onOpen }: { book: BookItem; onOpen: () => void }) {
  const done = book.stats.translated + book.stats.needs_review + book.stats.reviewed;
  const state = STATE_LABEL[book.state];
  return (
    <button type="button" className="book" onClick={onOpen}>
      <div className="cover" aria-hidden>{book.title_zh.slice(0, 1)}</div>
      <div className="info">
        <div className="t">{book.title_vi || '(chưa có tên Việt)'}</div>
        <div className="z">{book.title_zh}{book.author ? ` · ${book.author}` : ''}</div>
        <span className={`chip ${state.cls}`} style={{ justifySelf: 'start' }}>{state.label}</span>
        <ProgressBar stats={book.stats} />
        <div className="nums"><span>{`${fmtNum(done)}/${fmtNum(book.stats.total)} chương · ${book.progress_pct}%`}</span></div>
        <div className="hint">{genreLabel(book.genre)} · mở {relativeTime(book.last_opened_at)}</div>
      </div>
    </button>
  );
}

export default function LibraryPage() {
  useEvents();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const dq = useDebouncedValue(q.trim(), 200);
  const [filter, setFilter] = usePersistentState<Filter>('lt.lib.filter', 'all');
  const [sort, setSort] = usePersistentState<Sort>('lt.lib.sort', 'recent');

  const stats = useQuery({ queryKey: ['library-stats'], queryFn: () => api.get<LibraryStats>('/library/stats') });
  const books = useQuery({
    queryKey: ['books', { dq, filter, sort }],
    queryFn: () => api.get<{ items: BookItem[] }>(`/books${qs({ q: dq || undefined, filter, sort })}`),
    placeholderData: keepPreviousData,
  });
  const items = books.data?.items ?? [];
  const filtered = Boolean(dq) || filter !== 'all';
  const s = stats.data;

  return (
    <section style={{ display: 'grid', gap: 16 }}>
      <Breadcrumb items={[{ label: 'Thư viện' }]} />
      <div className="head">
        <h1>Truyện của tôi</h1>
        <Link className="btn primary" to="/books/new">＋ Tạo truyện</Link>
      </div>
      <StatTiles items={[
        { label: 'Truyện', value: s ? fmtNum(s.books) : '–' },
        { label: 'Tổng chương', value: s ? fmtNum(s.chapters_total) : '–' },
        { label: 'Chương đã dịch', value: s ? fmtNum(s.chapters_done) : '–' },
        { label: 'Chương chưa dịch', value: s ? fmtNum(s.chapters_left) : '–' },
      ]} />
      <div className="lib-tools">
        <input type="search" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Tìm truyện"
          placeholder="Tìm theo tên Việt, tên gốc, tác giả…" />
        <Segmented ariaLabel="Lọc truyện" value={filter} onChange={setFilter} options={[
          { value: 'all', label: 'Tất cả' }, { value: 'in_progress', label: 'Đang dịch' }, { value: 'completed', label: 'Hoàn tất' },
        ]} />
        <span className="grow" />
        <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sắp xếp" style={{ width: 'auto' }}>
          <option value="recent">Mở gần đây</option>
          <option value="name">Tên A–Z</option>
          <option value="progress">Tiến độ</option>
        </select>
      </div>

      {books.isError && (
        <Banner>
          <span className="grow">Không tải được thư viện. Kiểm tra app backend còn chạy không. ({errorMessage(books.error)})</span>
          <button className="btn sm" onClick={() => void books.refetch()}>Thử lại</button>
        </Banner>
      )}

      {books.isPending ? (
        <div className="books">{Array.from({ length: 6 }, (_, i) => <div key={i} className="skeleton" />)}</div>
      ) : items.length === 0 && !filtered ? (
        <div className="panel empty">
          <p>Chưa có truyện nào. Tạo truyện đầu tiên từ file .txt/.md hoặc thư mục chương.</p>
          <Link className="btn primary" to="/books/new">＋ Tạo truyện</Link>
        </div>
      ) : items.length === 0 ? (
        <div className="panel empty">
          <p>Không có truyện khớp bộ lọc.</p>
          <button className="btn" onClick={() => { setQ(''); setFilter('all'); }}>Xoá bộ lọc</button>
        </div>
      ) : (
        <div className="books">
          {items.map((b) => <BookCard key={b.id} book={b} onOpen={() => navigate(`/books/${b.id}`)} />)}
          <Link to="/books/new" className="book new"><b>＋</b><span>Tạo truyện mới</span></Link>
        </div>
      )}
    </section>
  );
}
