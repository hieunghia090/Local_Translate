export interface Tile { label: string; value: number | string; onClick?: () => void; }

export default function StatTiles({ items }: { items: Tile[] }) {
  return (
    <div className="stats">
      {items.map((t) =>
        t.onClick ? (
          <button key={t.label} type="button" className="stat" onClick={t.onClick}><b>{t.value}</b><span>{t.label}</span></button>
        ) : (
          <div key={t.label} className="stat"><b>{t.value}</b><span>{t.label}</span></div>
        ),
      )}
    </div>
  );
}
