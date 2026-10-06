from collections.abc import Sequence
from pathlib import Path

from app.core.translator import BatchResult

HACHIMI_REPO = "ngocdang83/HachimiMT-60-zh-vi"
CT2_SUBDIR = "ct2-int8_float32"
MAX_SRC_TOKENS = 300


class CT2Translator:
    model_id = "HachimiMT-60"

    def __init__(self, threads: int = 4, model_dir: Path | None = None, inter_threads: int = 1):
        import ctranslate2
        from huggingface_hub import snapshot_download
        from transformers import AutoTokenizer

        root = Path(model_dir) if model_dir else Path(snapshot_download(HACHIMI_REPO))
        ct2_path = root / CT2_SUBDIR
        if not ct2_path.exists():
            raise FileNotFoundError(f"Không thấy bản CTranslate2 tại {ct2_path}")
        self._tok = AutoTokenizer.from_pretrained(str(root))
        self._tr = ctranslate2.Translator(
            str(ct2_path), device="cpu", compute_type="int8_float32", intra_threads=threads,
            inter_threads=inter_threads,
        )

    def _encode(self, text: str) -> tuple[list[int], bool]:
        """Mã hoá một lần; nếu dài quá thì cắt giống tokenizer (giữ token kết thúc)."""
        ids = self._tok.encode(text)
        if len(ids) <= MAX_SRC_TOKENS:
            return ids, False
        return ids[: MAX_SRC_TOKENS - 1] + ids[-1:], True

    def translate(self, texts: Sequence[str], *, beam: int, batch_size: int) -> BatchResult:
        texts = list(texts)
        if not texts:
            return BatchResult([], 0, 0, [])
        encoded = [self._encode(t) for t in texts]
        ids = [x for x, _ in encoded]
        truncated = [t for _, t in encoded]
        src_tokens = [self._tok.convert_ids_to_tokens(x) for x in ids]
        results = self._tr.translate_batch(
            src_tokens,
            batch_type="examples",
            max_batch_size=batch_size,
            beam_size=beam,
            repetition_penalty=1.2,
            no_repeat_ngram_size=2,
        )
        outputs: list[str] = []
        tokens_out = 0
        for res in results:
            hyp = res.hypotheses[0]
            tokens_out += len(hyp)
            outputs.append(self._tok.decode(self._tok.convert_tokens_to_ids(hyp), skip_special_tokens=True))
        return BatchResult(outputs, sum(map(len, src_tokens)), tokens_out, truncated)
