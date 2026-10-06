import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ToastProvider } from './Toast';
import Topbar from './Topbar';

afterEach(() => vi.restoreAllMocks());

function renderBar() {
  render(<QueryClientProvider client={new QueryClient()}><MemoryRouter><ToastProvider><Topbar /></ToastProvider></MemoryRouter></QueryClientProvider>);
}

it('nút Sao lưu gọi POST /backups và báo đường dẫn file', async () => {
  const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify({ file: 'a.dump', path: '/d/backups/a.dump', size_bytes: 10, method: 'docker' }), { status: 201 }));
  renderBar();
  fireEvent.click(screen.getByRole('button', { name: '⤓ Sao lưu' }));
  expect(await screen.findByText('Đã sao lưu database: /d/backups/a.dump')).toBeInTheDocument();
  expect(fetchSpy).toHaveBeenCalledWith('/api/v1/backups', expect.objectContaining({ method: 'POST' }));
});

it('không sao lưu được thì báo lỗi của server', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    error: { code: 'BACKUP_UNAVAILABLE', message: 'Không tìm thấy pg_dump trên máy', details: {} },
  }), { status: 503 }));
  renderBar();
  fireEvent.click(screen.getByRole('button', { name: '⤓ Sao lưu' }));
  expect(await screen.findByText('Không tìm thấy pg_dump trên máy')).toBeInTheDocument();
});
