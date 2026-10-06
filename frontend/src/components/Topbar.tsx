import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { api, errorMessage } from '../api/client';
import type { BackupResult } from '../api/types';
import { applyTheme, storedTheme, type Theme } from '../lib/theme';
import { useToast } from './Toast';

const NEXT: Record<Theme, Theme> = { auto: 'dark', dark: 'light', light: 'auto' };
const LABEL: Record<Theme, string> = { auto: 'Giao diện: tự động', dark: 'Giao diện: tối', light: 'Giao diện: sáng' };

export default function Topbar() {
  const [theme, setTheme] = useState<Theme>(storedTheme());
  const toast = useToast();
  const backup = useMutation({
    mutationFn: () => api.post<BackupResult>('/backups'),
    onSuccess: (r) => toast(`Đã sao lưu database: ${r.path}`),
    onError: (e) => toast(errorMessage(e)),
  });
  const toggle = () => {
    const next = NEXT[theme];
    applyTheme(next);
    setTheme(next);
  };
  return (
    <header className="topbar">
      <Link to="/" className="brand">Local Translate</Link>
      <div className="row" style={{ gap: 8 }}>
        <button className="btn sm" disabled={backup.isPending} onClick={() => backup.mutate()}
          title="Sao lưu database bằng pg_dump vào thư mục backups">
          {backup.isPending ? 'Đang sao lưu…' : '⤓ Sao lưu'}
        </button>
        <button className="btn sm" onClick={toggle} aria-label={LABEL[theme]} title={LABEL[theme]}>
          {theme === 'dark' ? '☾' : theme === 'light' ? '☀' : '◐'}
        </button>
      </div>
    </header>
  );
}
