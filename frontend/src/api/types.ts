export type ChapterStatus = 'todo' | 'queued' | 'translating' | 'translated' | 'needs_review' | 'reviewed' | 'error';
export type Genre = 'xianxia' | 'urban' | 'modern_war' | 'xuanhuan' | 'romance' | 'other';
export type BookState = 'not_started' | 'in_progress' | 'completed';
export type Stats = Record<ChapterStatus, number> & { total: number };
export type SplitRule = 'auto' | 'blank_lines' | 'regex';
export type Encoding = 'auto' | 'utf-8' | 'gbk' | 'big5';

export interface Honorific { kinship: boolean; pronoun: boolean; modern_stable: boolean; }

export interface RunConfig {
  engine: 'ct2' | 'deepseek';
  model_id: string;
  beam: number;
  batch: { auto: boolean; size: number };
  chunk_mode: 'sentence' | 'paragraph';
  han_normalize: 'auto' | 't2s' | 'none';
  honorific: Honorific;
  deepseek: DeepSeekOptions;
  review: ReviewOptions;
}

export interface BookItem {
  id: string; slug: string; title_zh: string; title_vi: string | null; author: string | null; genre: Genre;
  cover_url: string | null; stats: Stats; progress_pct: number; state: BookState;
  last_opened_at: string | null; created_at: string;
}

export interface LibraryStats { books: number; chapters_total: number; chapters_done: number; chapters_left: number; }

export interface BookDetail extends BookItem {
  note: string | null; run_config: RunConfig; foundation_prompt: string | null;
  total_chars: number; source_dir: string; updated_at: string;
}

export interface ImportChapter {
  key: string; no: number; title_zh: string; title_vi: string; title_vi_edited: boolean;
  chars: number; selected: boolean; warnings: string[];
}
export interface FileError { file: string; code: string; message: string; }
export interface ImportView {
  import_id: string; status: 'parsing' | 'ready' | 'failed'; mode: 'single' | 'multi';
  split_rule: SplitRule; split_regex: string | null; encoding: Encoding; suggested_title_zh: string | null;
  total_chars: number; chapters: ImportChapter[]; file_errors: FileError[]; error: string | null;
}

export interface LastRun { beam: number | null; chunk_mode: string | null; at: string; }
export interface ChapterCompare { mt: number; ai: number; both: number; differ: number; }
export interface ChapterRow {
  id: string; book_id: string; no: number; title_zh: string; title_vi: string | null; status: ChapterStatus;
  char_count: number; model_id: string | null; last_run: LastRun | null; has_manual_edits: boolean;
  error: string | null; translated_at: string | null; reviewed_at: string | null; updated_at: string;
  compare?: ChapterCompare;
}
export interface ChapterPage { items: ChapterRow[]; next_cursor: string | null; total: number; }

export interface HonorificEdit { from: string; to: string; rule: string; offset: number; src_token: string; }
export type Route = 'ancient' | 'modern' | 'mixed' | 'unknown';

export interface Segment {
  idx: number; is_meta: boolean; src: string; dst: string | null; dst_machine: string | null;
  edited: boolean; flags: string[]; glossary_spans?: GlossarySpan[]; honorific_edits?: HonorificEdit[];
  /** Bản HachimiMT / DeepSeek mới nhất (spec 06 mục 4a). */
  dst_mt?: string | null; dst_ai?: string | null;
  /** Server tính theo BR-6.11; null khi thiếu một bản hoặc là dòng meta. */
  mt_ai_differ?: boolean | null;
}
export interface ChapterDetail {
  chapter: ChapterRow & {
    run_config_override: Partial<RunConfig> | null; run_config: RunConfig;
    register_route?: Route | null; register_score?: number | null; register_override?: Route | null;
  };
  segments: Segment[]; prev_no: number | null; next_no: number | null;
  job: { id: string; status: string; progress: number } | null; source_missing: boolean;
  glossary_terms?: { id: string; src_zh: string; dst_vi: string; count: number }[];
}
export interface SegmentPatchResult { segment: Segment; chapter: { id: string; status: ChapterStatus }; }

export type JobStatus = 'queued' | 'running' | 'paused' | 'done' | 'failed' | 'cancelled';
export interface JobView {
  id: string; book_id: string; chapter_id: string | null; chapter_no: number | null; chapter_title: string | null;
  kind: string; engine: 'ct2' | 'deepseek'; status: JobStatus; progress: number; position: number;
  tokens_in: number; tokens_out: number; error: string | null; created_at: string;
  started_at: string | null; finished_at: string | null; duration_ms: number | null;
}
export interface QueueView {
  paused: Record<'ct2' | 'deepseek', boolean>; paused_reason?: Record<'ct2' | 'deepseek', string | null>; active: JobView[]; recent: JobView[];
  running_elsewhere: { book_id: string; title: string } | null; eta_seconds: number | null;
}

export type LogLevel = 'info' | 'warn' | 'error';
export type LogSource = 'translate' | 'glossary' | 'review' | 'system';
export interface LogEntry {
  id: string; ts: string; book_id: string | null; chapter_id: string | null; chapter_no: number | null; job_id: string | null;
  level: LogLevel; source: LogSource; provider: string | null; model: string | null; message: string;
  tokens_in: number | null; tokens_out: number | null; tokens_in_cached: number | null; latency_ms: number | null;
  tokens_in_est?: number | null; tokens_out_est?: number | null; cost_usd?: number | null;
  params: Record<string, unknown>; detail?: Record<string, unknown>;
}
export interface LogPage { items: LogEntry[]; next_cursor: string | null; }
export interface LogSummary { total: number; errors: number; }

export interface Revision {
  id: string; kind: 'machine' | 'manual'; model_id: string | null; beam: number | null; chunk_mode: string | null;
  segments_changed: number; note: string | null; created_at: string; updated_at: string; restorable: boolean;
}
export interface BulkResult { affected: number; skipped: number; job_ids: string[]; }
export interface RestoreResult { restored: number; skipped: number; revision_id: string | null; }

export type GlossaryCategory = 'character' | 'location' | 'organization' | 'term' | 'rank' | 'realm' | 'item' | 'abbreviation';
export interface GlossaryTerm {
  id: string; src_zh: string; dst_vi: string; category: GlossaryCategory; name_lang: 'zh' | 'ja' | 'foreign' | null;
  notes: string | null; aliases: string[]; enabled: boolean; predictable: boolean; always_send: boolean;
  prompt_note: boolean; miss_count: number; book_id?: string; occurrence_count: number; origin: 'manual' | 'import' | 'ai' | 'copied';
  created_at: string; updated_at: string;
}
export interface GlossarySpan { term_id: string; src: [number, number]; dst: [number, number] | null; }
export interface ImportResult { added: number; updated: number; kept: number; conflicts: { src_zh: string; current: string; incoming: string }[]; }

export type DeepSeekModelId = 'deepseek-v4-pro' | 'deepseek-flash';
export interface DeepSeekOptions {
  model_id?: DeepSeekModelId; temperature?: number; glossary_max_terms?: number; thinking?: boolean;
  chain_context?: boolean; auto_extract_glossary?: boolean; concurrency?: number;
}
export interface ReviewOptions { auto_after_ct2?: boolean; model_id?: DeepSeekModelId; auto_apply?: 'none' | 'high_confidence'; }
export interface Coefficients { han_per_token: number; out_ratio: number; samples: number; calibrated: boolean; }
export interface CostEstimate {
  action: 'translate' | 'review'; model_id: string; chapters: number; skipped: number; tokens_in: number;
  tokens_in_cached: number; tokens_out: number; cost_usd: number; prices_are_samples: boolean; coefficients: Coefficients;
}
export interface Foundation { foundation_prompt: string; is_default: boolean; honorific_block: string; }
export interface GlossaryStats { matched: number; sent: number; truncated: number; skipped_predictable: number; tokens_est: number; }
export interface FoundationPreview { chapter_no: number; model_id: string; system: string; user: string; glossary: GlossaryStats; tokens_in_est: number; }
export interface AiModel {
  id: string; provider: string; label: string; context_window: number; max_output_tokens: number;
  price_in_per_mtok: number; price_in_cached_per_mtok: number; price_out_per_mtok: number;
  prices_are_samples: boolean; enabled: boolean;
}
export interface UsageRow { model: string; source: LogSource; requests: number; tokens_in: number; tokens_in_cached: number; tokens_out: number; cost_usd: number; }
export interface UsageTotal { requests: number; tokens_in: number; tokens_in_cached: number; tokens_out: number; cost_usd: number; }
export interface Accuracy { model: string; requests: number; err_in_pct: number | null; err_out_pct: number | null; coefficients: Coefficients; }
export interface UsageView { month: string; items: UsageRow[]; total: UsageTotal; accuracy: Accuracy[]; }
export interface DeepSeekStatus { key_present: boolean; key_masked: string | null; paused: boolean; paused_reason: string | null; message: string | null; }
export interface ConnectionTest { ok: boolean; model: string; latency_ms?: number; reasoning_tokens?: number; status?: number | null; message?: string; }
export type FixType = 'name_mismatch' | 'missing_content' | 'mistranslation' | 'honorific' | 'grammar';
export interface ReviewFix {
  id: string; chapter_id: string; segment_idx: number; type: FixType; before: string; after: string; reason: string | null;
  confidence: number; status: 'pending' | 'applied' | 'rejected'; model_id: string | null; created_at: string; decided_at: string | null;
}
export interface ChapterNote {
  id: string; chapter_id: string; type: 'correction' | 'context' | 'general'; content: string; resolved: boolean;
  created_at: string; updated_at: string;
}

export type ExportScope = 'translated' | 'reviewed' | 'range';
export type ExportFormat = 'txt' | 'zip' | 'epub' | 'bilingual';
export interface ExportPreview { chapters: number; skipped: number; }
export interface ExportJob {
  id: string; book_id: string; status: 'running' | 'done' | 'failed'; scope: ExportScope; format: ExportFormat;
  from_no: number | null; to_no: number | null; include_titles: boolean; keep_meta: boolean;
  chapters: number; skipped: number; file_name: string | null; size_bytes: number | null; error: string | null;
  created_at: string; finished_at: string | null; download_url: string | null;
}
export interface BackupResult { file: string; path: string; size_bytes: number; method: 'local' | 'docker'; }
export type NameLang = 'zh' | 'ja' | 'foreign';
export interface GlossaryListView { items: GlossaryTerm[]; summary?: { total: number; will_send: number } }
export interface GlossarySuggestion {
  id: string; src_zh: string; dst_vi: string; category: GlossaryCategory; name_lang: NameLang | null; notes: string | null;
  context: string | null; confidence: number; occurrence_count: number; provider: string; model: string | null;
  status: 'pending' | 'accepted' | 'rejected'; selected: boolean; created_at: string;
}
export interface ExtractJobView {
  id: string; status: JobStatus; progress: number; error: string | null; auto: boolean; chapters: number;
  created_at: string; finished_at: string | null;
}
export interface SuggestionList { items: GlossarySuggestion[]; total: number; limit: number; offset: number; last_job: ExtractJobView | null }
export interface AcceptResult { added: number; existing: number; skipped: number; term_ids: string[] }
export type ExtractScope = { mode: 'unscanned' } | { mode: 'range'; from: number; to: number } | { mode: 'first_n'; n: number };
export interface ExtractBody { provider: 'deepseek'; model: DeepSeekModelId; scope: ExtractScope; categories: GlossaryCategory[] }
export interface ExtractEstimate {
  model_id: string; chapters: number; batches: number; tokens_in: number; tokens_in_cached: number; tokens_out: number;
  cost_usd: number; prices_are_samples: boolean;
}
export interface PreviewEstimateBody { provider: 'deepseek'; model: DeepSeekModelId; chapters: number; categories: GlossaryCategory[] }
export interface SuspiciousReading { char: string; readings: string[]; proposed: string; terms: { id: string; src_zh: string; dst_vi: string }[] }
export interface GlossaryHealth { han_in_dst: GlossaryTerm[]; suspicious_readings: SuspiciousReading[] }
export interface PreviewItem {
  id: string; src_zh: string; dst_vi: string; category: GlossaryCategory; name_lang: NameLang | null; notes: string | null;
  context: string | null; confidence: number; occurrence_count: number; selected: boolean;
}
export interface PreviewResult {
  items: PreviewItem[]; model: string; chapters: number; batches: number; dropped: Record<string, number>;
  tokens_in: number; tokens_out: number; cost_usd: number;
}
export interface SendStats {
  chapters: number; avg_tokens_est: number | null; avg_sent: number | null; avg_skipped_predictable: number | null;
  avg_truncated: number | null; miss_pct: number | null; autofixed_pct: number | null;
}
