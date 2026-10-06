import type { ReactNode } from 'react';

export default function Banner({ kind = 'err', children }: { kind?: 'err' | 'warn' | 'info'; children: ReactNode }) {
  return <div className={`banner ${kind}`} role={kind === 'err' ? 'alert' : 'status'}>{children}</div>;
}
