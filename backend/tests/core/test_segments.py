from app.core.segments import plan_segments

CHAPTER = (
    "第16章 你过河 我拆桥\n"
    "=========================\n"
    "Nguồn: https://www.69shuba.com/txt/35466/24881048\n"
    "\n"
    "　　在赵楷准备离开东京汴梁的时候。\n"
)


def test_roundtrip_is_lossless():
    plans = plan_segments(CHAPTER)
    assert "\n".join(p.src for p in plans) == CHAPTER


def test_meta_and_text_lines():
    plans = plan_segments(CHAPTER)
    assert [p.is_meta for p in plans] == [False, True, True, True, False, True]
    assert plans[0].parts == ("第16章 你过河 我拆桥",)
    assert plans[4].parts == ("在赵楷准备离开东京汴梁的时候。",)  # bỏ thụt lề khi gửi model
    assert all(p.parts == () for p in plans if p.is_meta)


def test_indices_are_sequential():
    assert [p.idx for p in plan_segments(CHAPTER)] == [0, 1, 2, 3, 4, 5]


def test_long_line_gets_multiple_parts():
    plans = plan_segments("他说。" * 200, max_chars=60)
    assert len(plans) == 1 and len(plans[0].parts) > 1
    assert "".join(plans[0].parts) == "他说。" * 200


def test_no_carriage_returns():
    assert all("\r" not in p.src for p in plan_segments("第一行\n第二行"))
