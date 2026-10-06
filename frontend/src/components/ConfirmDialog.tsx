import { useEffect, type ReactNode } from 'react';

export interface DialogAction { label: string; onClick: () => void; primary?: boolean; danger?: boolean; disabled?: boolean; }

export default function ConfirmDialog(props: { title: string; children?: ReactNode; actions: DialogAction[]; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && props.onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [props]);
  return (
    <div className="overlay" onClick={props.onClose}>
      <div className="dlg" role="dialog" aria-modal="true" aria-label={props.title} onClick={(e) => e.stopPropagation()}>
        <h2>{props.title}</h2>
        {props.children}
        <div className="row">
          <span className="grow" />
          <button type="button" className="btn" onClick={props.onClose}>Huỷ</button>
          {props.actions.map((a) => (
            <button key={a.label} type="button" disabled={a.disabled}
              className={`btn${a.primary ? ' primary' : ''}${a.danger ? ' danger' : ''}`} onClick={a.onClick}>
              {a.label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
