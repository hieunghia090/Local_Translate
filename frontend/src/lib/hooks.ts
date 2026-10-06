import { useEffect, useRef, useState } from 'react';

export function useDebouncedValue<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function usePersistentState<T>(key: string, initial: T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      return raw === null ? initial : (JSON.parse(raw) as T);
    } catch {
      return initial;
    }
  });
  const set = (v: T) => {
    setValue(v);
    try {
      localStorage.setItem(key, JSON.stringify(v));
    } catch {
      /* không nhớ được, vẫn dùng trong phiên */
    }
  };
  return [value, set];
}

export type SaveState = 'idle' | 'saving' | 'saved' | 'error';

/**
 * Gọi `save(value, prev)` sau `ms` kể từ lần đổi cuối (`prev` = giá trị đã lưu lần trước).
 * Không lưu khi giá trị bằng bản đã lưu (nên không cần bỏ qua lần render đầu, an toàn với StrictMode).
 * Unmount khi còn lần lưu đang chờ thì lưu ngay. `rebase(v)` đặt lại mốc "đã lưu" (vd. nạp từ server).
 */
export function useAutosave<T>(value: T, save: (v: T, prev: T) => Promise<unknown>, ms = 500, enabled = true) {
  const [state, setState] = useState<SaveState>('idle');
  const serialized = JSON.stringify(value);
  const lastSaved = useRef({ value, serialized });
  const latest = useRef({ value, serialized, enabled });
  latest.current = { value, serialized, enabled };
  const saveRef = useRef(save);
  saveRef.current = save;
  const timer = useRef<number>();
  const scheduled = useRef(false);

  const run = () => {
    window.clearTimeout(timer.current);
    if (!scheduled.current) return;
    scheduled.current = false;
    const cur = latest.current;
    if (!cur.enabled || cur.serialized === lastSaved.current.serialized) return;
    const prev = lastSaved.current;
    lastSaved.current = { value: cur.value, serialized: cur.serialized };
    setState('saving');
    saveRef.current(cur.value, prev.value).then(
      () => setState('saved'),
      () => {
        if (lastSaved.current.serialized === cur.serialized) lastSaved.current = prev; // lần sau gửi lại
        setState('error');
      },
    );
  };

  useEffect(() => {
    window.clearTimeout(timer.current);
    if (!enabled || serialized === lastSaved.current.serialized) {
      scheduled.current = false;
      return;
    }
    scheduled.current = true;
    timer.current = window.setTimeout(run, ms);
    return () => window.clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized, ms, enabled]);

  useEffect(() => () => run(), []); // unmount: gửi bản còn chờ (fire-and-forget)
  // eslint-disable-line react-hooks/exhaustive-deps

  const rebase = (v: T) => {
    window.clearTimeout(timer.current);
    scheduled.current = false;
    lastSaved.current = { value: v, serialized: JSON.stringify(v) };
  };
  const dirty = () => latest.current.serialized !== lastSaved.current.serialized;
  return { state, rebase, dirty };
}
