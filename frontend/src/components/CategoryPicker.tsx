import type { GlossaryCategory } from '../api/types';
import { CATEGORY_LABEL } from '../lib/format';

export const DEFAULT_CATEGORIES: GlossaryCategory[] = ['character', 'organization', 'realm', 'location'];
const OPTIONS: GlossaryCategory[] = [...DEFAULT_CATEGORIES, 'item', 'rank'];

/** Spec 04 mục 6: loại cần trích (4 loại đầu bật sẵn). */
export default function CategoryPicker({ value, onChange }: { value: GlossaryCategory[]; onChange: (v: GlossaryCategory[]) => void }) {
  return (
    <div className="field">
      <span className="lbl">Loại cần trích</span>
      <div className="row" style={{ flexWrap: 'wrap' }}>
        {OPTIONS.map((c) => (
          <label key={c} className="check">
            <input type="checkbox" checked={value.includes(c)}
              onChange={(e) => onChange(e.target.checked ? [...value, c] : value.filter((x) => x !== c))} /> {CATEGORY_LABEL[c]}
          </label>
        ))}
      </div>
    </div>
  );
}
