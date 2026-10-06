from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class BatchResult:
    outputs: list[str]
    tokens_in: int
    tokens_out: int
    truncated: list[bool]


class Translator(Protocol):
    model_id: str

    def translate(self, texts: Sequence[str], *, beam: int, batch_size: int) -> BatchResult: ...


@dataclass
class FakeTranslator:
    prefix: str = "VI"
    model_id: str = "fake"
    calls: list[list[str]] = field(default_factory=list)

    def translate(self, texts: Sequence[str], *, beam: int, batch_size: int) -> BatchResult:
        texts = list(texts)
        if texts:  # không ghi lần gọi rỗng, để test kiểm tra được "không gọi translator"
            self.calls.append(texts)
        outputs = [f"{self.prefix}<{t}>" for t in texts]
        return BatchResult(outputs, sum(map(len, texts)), sum(map(len, outputs)), [False] * len(texts))
