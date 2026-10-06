#!/usr/bin/env python3
"""
Translate Chinese novels/texts in Source directory to Vietnamese using HachimiMT-60-zh-vi.
Optimized for Apple Silicon / CPU inference with CTranslate2 INT8 (or PyTorch MPS).
"""

import os
import sys
import re
import time
import argparse
from pathlib import Path
from typing import List, Tuple
from tqdm import tqdm
from huggingface_hub import snapshot_download
from transformers import AutoTokenizer

MODEL_ID = "ngocdang83/HachimiMT-60-zh-vi"

def has_chinese(text: str) -> bool:
    """Check if string contains Chinese characters."""
    return bool(re.search(r'[\u4e00-\u9fff]', text))

def is_delimiter_or_meta(text: str) -> bool:
    """Check if line is markdown separator, URL, or metadata delimiter."""
    t = text.strip()
    if not t:
        return True
    if t.startswith("http://") or t.startswith("https://") or t.startswith("www."):
        return True
    if re.fullmatch(r'[=\-_*#~`|/\\:\. ]+', t):
        return True
    return False

def split_long_chinese_line(text: str, max_chars: int = 250) -> List[str]:
    """Split very long Chinese paragraphs by sentence punctuation to prevent MT drift/truncation."""
    if len(text) <= max_chars:
        return [text]
    
    # Split by Chinese sentence delimiters: 。！？；
    parts = re.split(r'([。！？；]+)', text)
    sentences = []
    current = ""
    
    for i in range(0, len(parts), 2):
        chunk = parts[i]
        punct = parts[i+1] if i + 1 < len(parts) else ""
        piece = chunk + punct
        if not piece:
            continue
        if len(current) + len(piece) <= max_chars:
            current += piece
        else:
            if current:
                sentences.append(current)
            current = piece
    if current:
        sentences.append(current)
    return sentences if sentences else [text]

class NovelTranslator:
    def __init__(self, device: str = "cpu", threads: int = 4, beam_size: int = 4):
        print(f"Loading tokenizer and model {MODEL_ID}...")
        self.model_dir = snapshot_download(MODEL_ID)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        self.device = device
        self.beam_size = beam_size

        if device == "cpu":
            import ctranslate2
            ct2_path = Path(self.model_dir) / "ct2-int8_float32"
            if not ct2_path.exists():
                raise FileNotFoundError(f"CTranslate2 export not found at {ct2_path}")
            print(f"Initializing CTranslate2 (CPU INT8) with {threads} threads...")
            self.engine = "ct2"
            self.translator = ctranslate2.Translator(
                str(ct2_path),
                device="cpu",
                compute_type="int8_float32",
                intra_threads=threads
            )
        else:
            import torch
            from transformers import MarianMTModel
            target_device = "mps" if (device == "mps" and torch.backends.mps.is_available()) else "cpu"
            print(f"Initializing PyTorch MarianMT on {target_device}...")
            self.engine = "pytorch"
            self.model = MarianMTModel.from_pretrained(self.model_dir).to(target_device).eval()
            self.torch_device = target_device

    def translate_texts(self, texts: List[str], batch_size: int = 32) -> List[str]:
        if not texts:
            return []

        results = []
        if self.engine == "ct2":
            # Tokenize all texts
            batch_tokens = [
                self.tokenizer.convert_ids_to_tokens(
                    self.tokenizer.encode(t, truncation=True, max_length=300)
                )
                for t in texts
            ]
            # Translate in batches
            translated_batches = self.translator.translate_batch(
                batch_tokens,
                batch_type="examples",
                max_batch_size=batch_size,
                beam_size=self.beam_size,
                repetition_penalty=1.2,
                no_repeat_ngram_size=2
            )
            for res in translated_batches:
                hyp = res.hypotheses[0]
                vi = self.tokenizer.decode(
                    self.tokenizer.convert_tokens_to_ids(hyp),
                    skip_special_tokens=True
                )
                results.append(vi)
        else:
            import torch
            for i in range(0, len(texts), batch_size):
                sub_batch = texts[i:i+batch_size]
                inp = self.tokenizer(
                    sub_batch,
                    return_tensors="pt",
                    padding=True,
                    truncation=True,
                    max_length=300
                ).to(self.torch_device)
                with torch.inference_mode():
                    out = self.model.generate(
                        **inp,
                        max_new_tokens=350,
                        num_beams=self.beam_size,
                        early_stopping=True,
                        no_repeat_ngram_size=2,
                        repetition_penalty=1.2
                    )
                for o in out:
                    vi = self.tokenizer.decode(o, skip_special_tokens=True)
                    results.append(vi)

        return results

    def translate_file(self, input_path: str, output_path: str, batch_size: int = 32):
        with open(input_path, "r", encoding="utf-8", errors="replace") as f:
            raw_lines = f.readlines()

        final_lines = []
        # Mapping: list of (line_idx, sub_index, segment_text)
        segments_to_translate: List[Tuple[int, int, str]] = []
        
        # Prepare line slots
        for line_idx, line in enumerate(raw_lines):
            clean = line.strip()
            if not clean or is_delimiter_or_meta(clean) or not has_chinese(clean):
                final_lines.append(line)
            else:
                # Long line splitting to maintain high quality
                sub_chunks = split_long_chinese_line(clean, max_chars=250)
                # Slot with empty placeholders
                final_lines.append(["" for _ in sub_chunks])
                for sub_idx, sub_text in enumerate(sub_chunks):
                    segments_to_translate.append((line_idx, sub_idx, sub_text))

        if segments_to_translate:
            texts_only = [item[2] for item in segments_to_translate]
            translated_segments = self.translate_texts(texts_only, batch_size=batch_size)

            for (l_idx, s_idx, _), trans in zip(segments_to_translate, translated_segments):
                final_lines[l_idx][s_idx] = trans

        # Flatten slots back to lines
        output_content = []
        for item in final_lines:
            if isinstance(item, list):
                # Join sentences back with space
                joined = " ".join(part.strip() for part in item if part.strip())
                output_content.append(joined + "\n")
            else:
                output_content.append(item)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.writelines(output_content)

def collect_files(source_dir: str) -> List[str]:
    """Recursively collect text files sorted by name."""
    all_files = []
    for root, _, files in os.walk(source_dir):
        for f in files:
            if f.endswith(".txt") and not f.startswith("."):
                all_files.append(os.path.join(root, f))
    all_files.sort()
    return all_files

def main():
    parser = argparse.ArgumentParser(description="Translate novel chapters using HachimiMT-60-zh-vi")
    parser.add_argument("--source_dir", "-s", type=str, default="Source", help="Directory containing source novel files")
    parser.add_argument("--output_dir", "-o", type=str, default="Output", help="Directory to save translated files")
    parser.add_argument("--device", type=str, default="cpu", choices=["cpu", "mps"], help="Compute device ('cpu' with CTranslate2 INT8 or 'mps' with PyTorch)")
    parser.add_argument("--batch_size", "-b", type=int, default=32, help="Batch size for translation")
    parser.add_argument("--threads", "-t", type=int, default=4, help="Number of CPU threads for CTranslate2")
    parser.add_argument("--beam_size", type=int, default=4, help="Beam size for translation (default: 4)")
    parser.add_argument("--start", type=int, default=1, help="1-based start index of chapters to translate")
    parser.add_argument("--end", type=int, default=None, help="1-based end index of chapters to translate")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit total number of chapters to translate")
    parser.add_argument("--force", action="store_true", help="Overwrite already translated output files")

    args = parser.parse_args()

    source_path = Path(args.source_dir)
    if not source_path.exists():
        print(f"Error: Source directory '{args.source_dir}' does not exist.")
        sys.exit(1)

    all_files = collect_files(str(source_path))
    if not all_files:
        print(f"No .txt files found in '{args.source_dir}'.")
        sys.exit(0)

    print(f"Found {len(all_files)} total files in '{args.source_dir}'.")

    # Apply range / limit filtering
    start_idx = max(0, args.start - 1)
    end_idx = args.end if args.end is not None else len(all_files)
    target_files = all_files[start_idx:end_idx]

    if args.limit:
        target_files = target_files[:args.limit]

    print(f"Queued {len(target_files)} files for translation (indices {start_idx+1} to {start_idx+len(target_files)}).")

    translator = NovelTranslator(
        device=args.device,
        threads=args.threads,
        beam_size=args.beam_size
    )

    output_dir = Path(args.output_dir)
    t_start = time.time()
    translated_count = 0
    skipped_count = 0

    pbar = tqdm(target_files, desc="Translating chapters", unit="ch")
    for file_path in pbar:
        # Compute relative path to maintain folder structure
        rel_path = os.path.relpath(file_path, args.source_dir)
        out_file = output_dir / rel_path

        if out_file.exists() and out_file.stat().st_size > 0 and not args.force:
            skipped_count += 1
            pbar.set_postfix(skipped=skipped_count, done=translated_count)
            continue

        pbar.set_description(f"Translating: {Path(file_path).name[:30]}")
        translator.translate_file(file_path, str(out_file), batch_size=args.batch_size)
        translated_count += 1
        pbar.set_postfix(skipped=skipped_count, done=translated_count)

    elapsed = time.time() - t_start
    print("\n" + "=" * 50)
    print("Translation completed!")
    print(f"Total time: {elapsed:.1f}s ({elapsed/60:.2f} min)")
    print(f"Chapters translated: {translated_count}")
    print(f"Chapters skipped (already existed): {skipped_count}")
    print(f"Saved to: {output_dir.resolve()}")
    print("=" * 50)

if __name__ == "__main__":
    main()
