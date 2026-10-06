# Local Translate

Web app chạy trên máy của bạn để dịch truyện mạng tiếng Trung sang tiếng Việt.

- Dịch bằng model **HachimiMT-60** ([ngocdang83/HachimiMT-60-zh-vi](https://huggingface.co/ngocdang83/HachimiMT-60-zh-vi)) chạy trên CPU qua CTranslate2, không cần GPU hay mạng sau lần tải model đầu tiên.
- Quản lý nhiều truyện, hàng đợi dịch, sửa từng câu, glossary tên riêng, chuẩn hoá xưng hô.
- Xuất bản dịch ra `.txt`, `.zip`, `.epub` hoặc song ngữ.
- Tuỳ chọn: dịch và soát bằng DeepSeek nếu bạn có API key, kèm ước tính và theo dõi chi phí.
- Tuỳ chọn: AI trích glossary từ chương và bảng âm Hán Việt (xem mục G9 bên dưới).

Dữ liệu nằm trên máy bạn: PostgreSQL chạy trong Docker, file nằm trong thư mục `~/LocalTranslate`.

## Yêu cầu

| Thành phần | Phiên bản |
|---|---|
| Máy | macOS (khuyến nghị Apple Silicon) hoặc Linux. Khoảng 2 GB trống cho model. |
| Python | 3.11 trở lên |
| Node.js | 20 trở lên (chỉ cần để build giao diện) |
| Docker | Docker Desktop hoặc Docker Engine có `docker compose` |

## Cài đặt lần đầu

```bash
cd Local_Translate
cp .env.example .env                      # mở .env và đặt mật khẩu Postgres
python3 -m venv .venv
.venv/bin/pip install -e "./backend[dev]"
make fe-install                           # thư viện giao diện
make db                                   # chạy Postgres 16 bằng docker compose
make migrate                              # tạo bảng
```

- Nếu cổng 5432 đã bị dùng, đặt `POSTGRES_PORT=5433` trong `.env`, rồi sửa cổng trong `DATABASE_URL` cho khớp.
- Model HachimiMT tự tải từ Hugging Face ở lần dịch đầu tiên, nên lần đó cần mạng. Sau đó có thể chạy offline với `HF_HUB_OFFLINE=1`.

### Biến môi trường (`.env`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `DATABASE_URL` | `postgresql://local_translate:change-me@127.0.0.1:5432/local_translate` | Database chính |
| `POSTGRES_PASSWORD`, `POSTGRES_PORT` | `change-me`, `5432` | Dùng cho container Postgres của docker compose |
| `TEST_DATABASE_URL` | cùng server, DB `local_translate_test` | Database cho `make test-db` / `make e2e` |
| `DATA_DIR` | `~/LocalTranslate` | Thư mục bản gốc, file xuất, sao lưu |
| `APP_HOST`, `APP_PORT` | `127.0.0.1`, `8000` | Địa chỉ app |
| `DEEPSEEK_API_KEY` | trống | Bật tính năng DeepSeek (tuỳ chọn) |
| `DEEPSEEK_BASE_URL`, `DEEPSEEK_DEFAULT_MODEL` | `https://api.deepseek.com`, `deepseek-v4-pro` | Endpoint và model DeepSeek mặc định |
| `HANVIET_AUTOFILL` | `true` | Bổ sung âm Hán Việt nền bằng DeepSeek |
| `CT2_INTRA_THREADS` | `8` | Số luồng CPU cho HachimiMT (1–32). Đo trên M5: 8 nhanh nhất |
| `CT2_INTER_THREADS` | `1` | Số bản dịch song song của CTranslate2 (1–8) |

Đổi `.env` xong phải khởi động lại app.

## Khởi động

```bash
make start
```

Lệnh này chạy Postgres, build giao diện (nếu có thay đổi), API và worker dịch. Mở **http://127.0.0.1:8000**.

Bấm `Ctrl+C` để dừng app. Postgres vẫn chạy nền. Muốn tắt hẳn thì chạy `docker compose stop db`.

## Sử dụng

### Thư viện

- Màn đầu tiên liệt kê các truyện, kèm thanh tiến độ và số chương đã dịch.
- Tìm theo tên Việt (gõ không dấu cũng được), tên gốc hoặc tác giả.
- Lọc: Tất cả / Đang dịch / Hoàn tất. Sắp xếp: Mở gần đây, Tên A–Z, Tiến độ.
- Nút **＋ Tạo truyện** mở màn nhập truyện mới.

### Tạo truyện

Bấm **＋ Tạo truyện** để mở màn "Workspace truyện mới", gồm ba phần:

1. **Thông tin truyện:** tên gốc (bắt buộc), tên tiếng Việt, tác giả, thể loại. Thể loại quyết định cấu hình xưng hô mặc định. Nếu trùng một truyện đã có, app hỏi trước khi cho tạo.
2. **Nhập nội dung gốc:** chọn một trong ba cách:
   - **Một file cả bộ:** app tự tách chương. Quy tắc tách: tự động, theo dòng trống, hoặc theo biểu thức chính quy.
   - **Nhiều file, mỗi file 1 chương:** chọn cả thư mục, các file được sắp theo số trong tên.
   - **Trống:** tạo workspace rỗng, thêm chương sau.

   Bảng mã mặc định là tự nhận (UTF-8 / GBK / Big5), đổi tay nếu nhận sai. Danh sách chương hiện ra để xem trước:
   - Chương quá ngắn và lời tác giả (`感言`, `请假`…) được bỏ chọn sẵn;
   - tiêu đề tiếng Việt sửa được trực tiếp.
3. **Cấu hình dịch mặc định:** model (HachimiMT-60 chạy local, hoặc DeepSeek API), beam, cách chia chunk.

Bấm **Tạo workspace**. Bản gốc mỗi chương được lưu thành `books/<slug>/source/NNNN.txt`.

### Workspace truyện

Đầu trang có thanh tiến độ và các ô thống kê. Bấm vào một ô để lọc chương theo trạng thái đó. Bên dưới là các tab:

- **Chương**
  - Lọc theo trạng thái (Chưa dịch, Đã dịch, Cần soát, Lỗi).
  - Tìm theo số chương hoặc tên chương.
  - Chọn nhiều chương rồi bấm ▶ Dịch, ↻ Dịch lại hoặc ✓ Đánh dấu đã soát.
  - Với DeepSeek: **▶ Dịch bằng DeepSeek** và **✦ Soát bằng DeepSeek** (xem mục DeepSeek bên dưới).
  - Nút **▶ Dịch tất cả chương chưa dịch** đưa mọi chương chưa dịch vào hàng đợi. Trên 100 chương thì app hỏi xác nhận trước.
  - Menu "…" của mỗi chương có: dịch lại, sửa tiêu đề, thay bản gốc, xoá chương.
  - **＋ Thêm chương:** dán văn bản hoặc chọn file, thêm vào cuối hoặc sau một chương.
- **Glossary**
  - Thêm, sửa hoặc tắt thuật ngữ (tên người, địa danh, cảnh giới…). Tìm, lọc theo loại, sắp xếp.
  - Nhập hoặc xuất `.tsv` / `.json`; khi trùng thuật ngữ, app hỏi lại trước khi ghi đè.
  - Sao glossary từ truyện khác (bỏ qua term đã có).
  - Trích glossary bằng AI và bảng âm Hán Việt: xem mục G9 bên dưới.
  - Khi dịch, thuật ngữ được thay bằng tên giả để model giữ nguyên, rồi khôi phục. Câu bị mất thuật ngữ được dịch lại; nếu vẫn hỏng thì chương chuyển sang "Cần soát".
- **Hàng đợi dịch**
  - Xem chương đang dịch và chương đang chờ.
  - Tạm dừng hoặc tiếp tục (worker CPU và pool DeepSeek riêng), huỷ, đổi thứ tự bằng ↑ ↓.
  - Tắt app giữa chừng thì lần sau dịch tiếp từ câu đang dở.
- **Console logs**
  - Nhật ký từng chương: thời gian, token, lỗi.
  - Lọc theo mức hoặc nguồn, tìm theo chữ, xuất `.jsonl`.
- **Cấu hình**
  - HachimiMT-60: beam, batch, chia chunk (theo câu / theo đoạn), chuẩn hoá chữ Hán phồn → giản.
  - Model dịch mặc định (HachimiMT-60 hoặc DeepSeek API), model DeepSeek, số chương song song.
  - Soát bằng DeepSeek: model soát và cách áp đề xuất (Không / Chỉ fix tin cậy cao).
  - Chuẩn hoá xưng hô. Thông tin truyện.
  - Prompt nền (DeepSeek) và Chi phí DeepSeek, xem mục DeepSeek bên dưới.
  - Tự lưu sau nửa giây. Cấu hình mới chỉ áp cho lần dịch sau.
  - **Vùng nguy hiểm:** gõ lại đúng tên truyện thì mới xoá được workspace. Thư mục truyện được chuyển vào thùng rác, không xoá hẳn.
- **Xuất bản dịch:** xem mục [Xuất bản dịch](#xuất-bản-dịch).

### Màn Dịch chương

Bấm vào tên một chương để mở màn này. Bản gốc và bản dịch nằm song song theo từng câu.

- **Sửa tay:**
  - Bấm vào câu dịch để sửa. App tự lưu khi rời ô hoặc dừng gõ.
  - Câu đã sửa có dấu ✎, kèm lựa chọn "Khôi phục bản máy".
  - Sửa câu trong chương đã soát thì chương quay về "Cần soát".
- **Dòng meta:** dòng như `=====` hay `Nguồn: …` hiện mờ và không sửa được.
- **Chỉ hiện câu có cờ:** chỉ giữ lại câu có vấn đề, như mất thuật ngữ hoặc phải dịch lại.
- **Glossary:**
  - Chỗ khớp glossary được tô sáng.
  - Bôi đen chữ Hán ở cột gốc rồi bấm **＋ Thêm vào glossary**. Lưu xong, app hỏi có dịch lại chương không.
- **Văn phong** (cột phụ): cổ trang / hiện đại / hỗn hợp, app tự nhận khi dịch. Có thể ép tay.
- **Cấu hình chương này:** bỏ chọn "Dùng cấu hình của truyện" để đặt model hoặc beam riêng cho chương.
- **↻ Dịch lại chương ▾:** chọn dịch lại bằng HachimiMT-60 hoặc DeepSeek. Có ô "Ghi chú cho lần dịch lại" (vd. "Tên 高俅 phải là Cao Cầu") để đưa vào lần dịch DeepSeek sau.
- **Lịch sử:** các lần dịch máy và sửa tay. "Khôi phục" tạo một bản mới, không xoá gì.

**Phím tắt**

| Phím | Tác dụng |
|---|---|
| `Alt+↑` / `Alt+↓` | Sang câu dịch trước / sau (chưa chọn ô nào thì vào câu cuối / câu đầu) |
| `Alt+←` / `Alt+→` | Sang chương trước / sau. Ở chương đầu hoặc cuối thì không làm gì. |
| `Ctrl+Enter` (macOS: cả `⌘+Enter`) | Đánh dấu đã soát |

### Chuẩn hoá xưng hô

HachimiMT hay dịch xưng hô theo kiểu hiện đại (`anh`, `chị`, `anh ta`). App sửa lại theo văn phong của chương bằng ba lớp, bật hoặc tắt ở tab Cấu hình:

- **Thân tộc:** `师兄` → sư huynh, `姐姐` → tỷ tỷ…
- **Đại từ cổ trang:** `他` → hắn, `她` → nàng, `你` → ngươi (chỉ trong lời thoại).
- **Ổn định ngôi hiện đại.**

App chỉ sửa khi chắc chắn. Chỗ đã sửa có gạch chân chấm; bấm vào để "Bỏ thay đổi này". Đổi cấu hình thì bấm **Áp lại** để chạy lại luật trên bản máy gốc, không cần dịch lại. Câu đã sửa tay không bị đụng.

### Dịch và soát bằng DeepSeek (tuỳ chọn)

Tính năng này gửi nội dung chương tới DeepSeek API và tính phí theo token. Mặc định app chỉ dùng HachimiMT chạy local.

1. Đặt `DEEPSEEK_API_KEY` trong `.env` rồi khởi động lại app. Giao diện không cho nhập key: tab Cấu hình chỉ hiện `••••` kèm 4 ký tự cuối và nút **Kiểm tra kết nối**.
2. Chọn model dịch mặc định là **DeepSeek API** ở màn Tạo truyện hoặc tab Cấu hình. Muốn thử trước thì chọn riêng: nút **▶ Dịch bằng DeepSeek** ở tab Chương, hoặc menu **↻ Dịch lại chương ▾** ở màn Dịch chương.

Sau đó:

- **Dịch:** chương được DeepSeek dịch theo prompt nền, kèm glossary (chỉ term có trong chương) và ghi chú sửa lỗi của bạn.
- **✦ Soát bằng DeepSeek:** chỉ dùng cho chương dịch bằng HachimiMT. DeepSeek đề xuất sửa từng câu; ở màn Dịch chương bấm **Áp** hoặc **Bỏ** từng đề xuất (hoặc Áp tất cả / Bỏ tất cả). Câu bạn đã sửa tay không bị đề xuất sửa. Còn đề xuất chưa duyệt thì chưa đánh dấu đã soát được.
- **Prompt nền (DeepSeek):** xem và sửa ở tab Cấu hình, có nút Khôi phục mẫu và xem trước prompt đầy đủ cho một chương. Prompt nền giống nhau ở mọi chương để DeepSeek dùng lại cache.
- **Chi phí:** từ 10 chương trở lên, app hiện ước tính token và tiền trước khi tạo job. Mục **Chi phí DeepSeek** ở tab Cấu hình cho thấy ước tính so với thực tế và bảng giá. Giá trong bảng là **giá mẫu** cho tới khi bạn sửa theo bảng giá DeepSeek hiện hành.
- **Tự dừng khi có sự cố:** key sai hoặc hết số dư, token ra bất thường, hoặc nhiều chương liên tiếp lỗi. App hiện banner đỏ; xử lý xong thì bấm **▶ Tiếp tục pool DeepSeek**.
- Console logs ghi số token vào và ra của từng lần dịch.

### Xuất bản dịch

Ở tab **Xuất bản dịch** của workspace:

- **Phạm vi:**
  - Chương đã dịch (gồm cả "Cần soát" và "Đã soát");
  - Chỉ chương đã soát;
  - Khoảng chương (từ # đến #).
- **Định dạng:**
  - `.txt` gộp;
  - `.txt` mỗi chương, đóng trong `.zip`;
  - `.epub` có mục lục theo tên chương tiếng Việt;
  - Song ngữ: câu gốc rồi câu dịch.
- **Tuỳ chọn:**
  - Chèn tiêu đề chương tiếng Việt (bật sẵn);
  - Giữ dòng meta như `Nguồn: …` (tắt sẵn).

Trước khi xuất, app báo "Sẽ xuất N chương, bỏ qua M". Chương chưa dịch xong hoặc nằm ngoài phạm vi bị bỏ qua.

Xuất xong, trình duyệt tự tải file về. Một bản cũng được giữ trong `~/LocalTranslate/books/<slug>/exports/`, tên dạng `<slug>_<phạm vi>_<năm tháng ngày-giờ phút>.<đuôi>`, ví dụ `dai-tong_da-soat_20261004-0905.txt`.

### Sao lưu và khôi phục

Bấm **⤓ Sao lưu** ở góc trên, hoặc chạy `make backup`. Database được dump ra `~/LocalTranslate/backups/local_translate_<thời điểm>.dump`.

App dùng `pg_dump` trên máy. Không có, hoặc sai phiên bản, thì app chạy `pg_dump` trong container Postgres của docker compose. Nếu cả hai đều không dùng được, app báo cách cài: `brew install libpq`, hoặc `make db`.

Khôi phục (ghi đè database hiện tại, nên tắt app trước):

```bash
docker compose exec -T db pg_restore --clean --if-exists --no-owner \
  -U local_translate -d local_translate < ~/LocalTranslate/backups/local_translate_20261004-090507.dump
```

Bản gốc các chương nằm trong `~/LocalTranslate/books/`. Hãy chép thư mục này cùng với file `.dump` khi sao lưu sang máy khác.

## Dữ liệu nằm ở đâu

```
~/LocalTranslate/                 # đổi bằng DATA_DIR trong .env
├── books/<slug>/
│   ├── source/0001.txt           # bản gốc UTF-8, mỗi chương 1 file
│   └── exports/                  # file đã xuất
└── backups/                      # file .dump từ nút Sao lưu
```

Bản dịch, glossary, lịch sử sửa và log nằm trong PostgreSQL (volume `pgdata` của docker compose).

## Xử lý sự cố

| Hiện tượng | Cách xử lý |
|---|---|
| `make db` báo cổng 5432 đã dùng | Đặt `POSTGRES_PORT=5433` trong `.env`, rồi sửa cổng trong `DATABASE_URL` cho khớp |
| Giao diện báo "Không kết nối được tới backend" | Kiểm tra cửa sổ `make start` còn chạy không, rồi chạy lại |
| Chương nằm mãi ở "Đang chờ" | Worker chưa chạy hoặc đang tạm dừng: xem tab Hàng đợi dịch, bấm ▶ Tiếp tục |
| Lần dịch đầu rất lâu | Đang tải model (~1 GB) từ Hugging Face |
| HachimiMT dịch chậm hoặc máy quá nóng | Chỉnh `CT2_INTRA_THREADS` trong `.env` theo số nhân CPU; đo bằng `bench_ct2` (mục Dành cho người phát triển) |
| Banner đỏ "DeepSeek …" và chương DeepSeek không chạy | Kiểm tra `DEEPSEEK_API_KEY` và số dư (nút Kiểm tra kết nối ở tab Cấu hình), rồi bấm ▶ Tiếp tục pool DeepSeek |
| Sao lưu báo không tìm thấy `pg_dump` | `brew install libpq` (macOS), hoặc chạy Postgres bằng `make db` |

## Dành cho người phát triển

```bash
make dev        # API tự nạp lại + worker + Vite ở http://127.0.0.1:5173
make test       # test backend nhanh (không cần DB, không nạp model)
make test-db    # test cần Postgres (DB local_translate_test)
make test-slow  # test nạp HachimiMT thật (NFR-1)
make test-live  # test gọi DeepSeek thật 1 chương, tốn tiền; chỉ chạy khi LT_LIVE=1 (make đã đặt sẵn)
make fe-test    # vitest
make e2e        # Playwright, server riêng ở cổng 8765, seed 50 truyện để đo NFR-2
make seed-perf  # tạo 50 truyện / 2.000 chương để thử hiệu năng; từ chối nếu DATABASE_URL không phải DB test (muốn ghi vào DB thật: `make seed-perf ARGS=--yes-main-db`)
```

- Đặc tả: `specs/00-overview.md` → `specs/08-ai-translation.md` (đã làm). `specs/09-staged-name-scan.md` (G11, quét tên theo giai đoạn) đang là bản nháp, chưa có trong app; kế hoạch ở `docs/superpowers/plans/2026-10-05-g11-staged-name-scan.md`.
- Kế hoạch từng giai đoạn: `docs/superpowers/plans/`.
- API nằm dưới `/api/v1`. Xem danh sách đầy đủ ở mục API của từng spec, hoặc tại http://127.0.0.1:8000/docs khi app đang chạy.
- `make test` và `make test-db` dùng DeepSeek giả, không bao giờ gọi API thật.
- Không chạy `make test-db` cùng lúc với `make e2e`, vì cả hai dùng chung DB test.

### Công cụ dòng lệnh

Dịch một file chương mà không cần app:

```bash
cd backend && ../.venv/bin/python -m app.cli translate-file in.txt out.txt --beam 2
```

Công cụ phát triển khác (chạy trong `backend/`):

```bash
../.venv/bin/python -m app.devtools.bench_ct2       # đo tốc độ HachimiMT theo số luồng / batch, dùng câu trong Output/compare/source
../.venv/bin/python -m app.devtools.seed_compare    # dữ liệu cho e2e So sánh Hachimi/AI; từ chối DB chính trừ khi có --yes-main-db
```

Script cũ `translate_novel.py` (dịch cả thư mục `Source/` ra `Output/`) vẫn còn trong repo. Chạy `python3 translate_novel.py --help` để xem tham số.

## G9: AI trích glossary và bảng âm Hán Việt

- `make hanviet` dựng `data/hanviet_seed.tsv`: lấy 8.105 chữ thông dụng (trường `kTGH` của Unihan) cùng chữ phồn thể, hỏi DeepSeek (`deepseek-flash`) theo lô 400 chữ, đối chiếu `kVietnamese` của Unihan (âm có ở cả hai nguồn là `confirmed`; âm chỉ có ở Unihan bị bỏ vì có thể là âm Nôm). `Unihan.zip` tải một lần vào `~/LocalTranslate/cache/`. File seed đã commit; API nạp vào DB khi khởi động, theo dấu sha256 của file lưu trong `app_meta` (seed đổi thì nạp lại).
- Chữ chưa có âm được bổ sung nền sau khi tạo truyện, thêm chương hoặc thêm term, và chỉ chạy khi đã nạp seed (`HANVIET_AUTOFILL=false` để tắt). Mỗi lượt hỏi tối đa 300 chữ, phần còn lại để lượt sau. Term đã duyệt dạy thêm âm cho chữ chưa có âm nào (`source = learned`).
- Tab Glossary: badge **HV** = tự đoán được (cách dịch trùng âm Hán Việt), không gửi cho DeepSeek trừ khi bật **Luôn gửi**. Term bị DeepSeek dịch sai từ 3 lần tự bật Luôn gửi. **Gửi ghi chú** gửi cột Ghi chú kèm term.
- "✦ Trích xuất bằng AI" (tab Glossary) chạy thành job trong pool DeepSeek; đề xuất chờ duyệt, mục dưới 80% không chọn sẵn. Danh sách đề xuất phân trang ở server; "Bỏ hết" và chấp nhận mặc định áp qua bộ lọc phía server cho mọi mục đang chờ, không chỉ trang đang xem. Truyện dịch bằng DeepSeek tự trích sau mỗi chương (tắt bằng `auto_extract_glossary`).
- Tạo truyện: khối "Glossary ban đầu" hiện chi phí ước tính, có "Chạy thử" (đồng bộ, tối đa 120 giây; trả 409 `ENGINE_PAUSED` khi engine DeepSeek đang tạm dừng); mục đã chọn thành thuật ngữ khi bấm Tạo, và một job trích cho N chương đầu được xếp hàng.
- "Kiểm tra glossary" liệt kê đích Việt còn chữ Hán và âm Hán Việt nghi ngờ (sửa ở đây ghi `manual`).

## So sánh bản Hachimi và bản AI

- Mỗi câu giữ bản HachimiMT mới nhất (`dst_mt`) và bản DeepSeek mới nhất (`dst_ai`). Dịch lại bằng engine này không xoá bản của engine kia (trừ câu có bản gốc đổi). Bản chính (`dst`) vẫn theo lần dịch mới nhất, trừ câu sửa tay.
- Trang Dịch chương có 4 chế độ: **Bản chính** (sửa như cũ), **Hachimi**, **AI** (chỉ đọc), **So sánh** (tô chữ khác nhau, "Chỉ câu khác nhau", nhảy câu bằng nút hoặc `Alt+Shift+↑/↓`, "Dùng bản này" = sửa tay câu đó). Chế độ được nhớ trong trình duyệt.
- Workspace: badge `≠ n` ở cột Model cho chương có cả hai bản.
- Migration `0011` điền sẵn hai cột cho dữ liệu cũ: bản của engine hiện tại lấy từ `dst_machine`, bản của engine kia lấy từ lần dịch gần nhất trong lịch sử (nếu câu gốc không đổi).
