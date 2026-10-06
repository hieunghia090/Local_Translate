import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';

type ToastLink = { to: string; label: string };
type Show = (message: string, link?: ToastLink) => void;
const ToastContext = createContext<Show>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ message: string; link?: ToastLink } | null>(null);
  const timer = useRef<number>();
  const show = useCallback<Show>((message, link) => {
    setToast({ message, link });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setToast(null), link ? 6000 : 2500);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {toast && (
        <div className="toast" role="status">
          <span>{toast.message}</span>
          {toast.link && <Link to={toast.link.to} onClick={() => setToast(null)}>{toast.link.label}</Link>}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
