export interface SegOption<T extends string> { value: T; label: string; }

export default function Segmented<T extends string>(props: {
  options: SegOption<T>[]; value: T; onChange: (v: T) => void; full?: boolean; ariaLabel: string;
}) {
  return (
    <div className={`seg${props.full ? ' full' : ''}`} role="group" aria-label={props.ariaLabel}>
      {props.options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === props.value} onClick={() => props.onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
