import unicodedata

# (mt, ai, khác nhau?) — dùng chung cho test Python (core) và test SQL (web/test_compare_api.py)
CASES = [
    ("Hắn đi.", "Hắn đi.", False),
    ("Hắn  đi.\t", " Hắn đi.", False),
    (unicodedata.normalize("NFD", "Lâm Phàm đi."), "Lâm Phàm đi.", False),
    ("Hắn đi.", "Hắn đi.", False),
    ("Hắn　đi.", "Hắn đi.", False),
    ("Hắn\nđi.", "Hắn đi.", False),
    ("Hắn đi.", "Hắn đi rồi.", True),
    ("Hắn đi.", "hắn đi.", True),
    ("Hắn đi.", "Hắn đi.", True),  # em space không thuộc WS_CHARS
]
