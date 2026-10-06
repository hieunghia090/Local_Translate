from dataclasses import dataclass, field

from app.core.glossary import GlossaryIndex, Term
from app.core.pipeline import render, translate_segments
from app.core.placeholders import PSEUDO_NAMES
from app.core.segments import plan_segments
from app.core.translator import BatchResult, FakeTranslator

ZHAO = Term("t1", "赵楷", "Triệu Khải", ("Triệu Giai",))
INDEX = GlossaryIndex([ZHAO])


def test_glossary_term_restored_and_hits_recorded():
    # AC-4.1
    res = translate_segments(plan_segments("赵楷说话。\n他走了。"), FakeTranslator(), glossary=INDEX)
    first, second = res.segments
    assert first.dst == "VI<Triệu Khải说话。>"
    assert first.glossary_hits == ["t1"] and first.flags == []
    assert second.glossary_hits == []


def test_model_sees_pseudo_names_not_chinese_term():
    fake = FakeTranslator()
    translate_segments(plan_segments("赵楷说话。"), fake, glossary=INDEX)
    assert fake.calls == [[f"{PSEUDO_NAMES[0]}说话。"]]


@dataclass
class DropsPlaceholders(FakeTranslator):
    """Lần gọi đầu làm mất tên giả; lần gọi lại (không placeholder) dịch tên thành alias."""
    rounds: list = field(default_factory=list)

    def translate(self, texts, *, beam, batch_size):
        texts = list(texts)
        self.rounds.append(texts)
        outs = []
        for t in texts:
            for name in PSEUDO_NAMES:
                t = t.replace(name, "")
            outs.append(t.replace("赵楷", "Triệu Giai"))
        return BatchResult(outs, 1, 1, [False] * len(texts))


def test_lost_placeholder_retranslated_and_repaired_by_alias():
    # BR-4.3
    tr = DropsPlaceholders()
    res = translate_segments(plan_segments("赵楷说话。"), tr, glossary=INDEX)
    seg = res.segments[0]
    assert tr.rounds[1] == ["赵楷说话。"]  # dịch lại câu gốc, không placeholder
    assert seg.dst == "Triệu Khải说话。"
    assert seg.flags == ["retried"]


def test_still_lost_is_flagged():
    # AC-4.3
    class Hopeless(DropsPlaceholders):
        def translate(self, texts, *, beam, batch_size):
            r = super().translate(texts, beam=beam, batch_size=batch_size)
            return BatchResult([o.replace("Triệu Giai", "hắn") for o in r.outputs], 1, 1, r.truncated)

    seg = translate_segments(plan_segments("赵楷说话。"), Hopeless(), glossary=INDEX).segments[0]
    assert set(seg.flags) == {"retried", "placeholder_lost"}


def test_only_failed_parts_are_retranslated():
    tr = DropsPlaceholders()
    translate_segments(plan_segments("赵楷说话。\n天气很好。"), tr, glossary=INDEX)
    assert tr.rounds[1] == ["赵楷说话。"]


def test_without_glossary_behaviour_unchanged():
    text = "第1章 甲\n=====\n他走了。"
    assert render(translate_segments(plan_segments(text), FakeTranslator())) == render(
        translate_segments(plan_segments(text), FakeTranslator(), glossary=GlossaryIndex([]))
    )
