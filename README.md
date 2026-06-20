## 保發中心（TII）人身保險條款批次爬蟲開發紀錄

本專案在存取財團法人保險事業發展中心（保發中心）商品查詢系統時，因目標網站架構較為老舊，且具備非標準網頁語法，開發管線共歷經四個關鍵日期的診斷、重構與修正：

### 📅 2026-06-04 | 初始爬蟲管線與內文檢索測試

* **模組化版本 (`20260604/`)**
* **概述：** 專題團隊初期建立的第一代基礎管線，嘗試對保發中心進行初步的自動化探索，拆分為爬取模組 `module1_tii_scraper.py` 與後續的文本診斷框架 `module2_gvdr_processor.py`。
* **問題：** 此階段代碼架構對保發中心老舊的表單防禦與動態渲染機制處理能力不足，且直接嘗試針對全文檢索頁面（`QueryFullText.aspx`）進行內文爆破，導致後續執行引發嚴重的超時崩潰。



### 📅 2026-06-05 | 基礎架構定位與 DOM 狀態偵測

* **早期版本 (`20260605/tii_pdf_downloader.py`)**
* **現象：** 於 `WebDriverWait` 尋找選單元件時卡住 30 秒，最後拋出 `TimeoutException` 崩潰。
* **診斷：** 執行測試指令 `python step1_diagnose_frames.py`。結果顯示網頁實際並無任何 `frame` 或 `iframe` 結構（框架數為 0）。崩潰主因為目標網址錯誤，誤將全文檢索頁（`QueryFullText.aspx`）當作商品查詢頁。
* **修正：** 將目標網址修正為包含險種分類的商品查詢頁 `Query.aspx`，排除框架切換的邏輯錯誤。


* **第二版本 (`20260605/tii_health_scraper.py` / v1 - v2)**
* **現象：** 選完下拉選項後，在尚未輸入驗證碼的情況下，程式便直接判定「結果頁已載入」，解析出 0 筆資料並結束。
* **診斷：** 執行 `python read_debug.py` 分析快照。發現網站查詢按鈕是透過 JavaScript `document.form1.submit()` 以 **POST 方式** 提交。送出後**網址列（URL）完全不會改變**（依然停在 `Query.aspx`），而是直接刷新當前頁面的 DOM。舊版爬蟲因比對 URL 包含特定關鍵字，導致在初始頁面即錯誤觸發。
* **修正：** * 定位到未停售的 Checkbox 屬性為 `id="endDate2"`，由程式自動點擊。
* 放棄等待 URL 變化的傳統邏輯，改用 **DOM 元件消失偵測法**：持續監控直到驗證碼輸入框（`name="bmpC"`）從畫面消失，且頁面出現「商品名稱」等結果表格特徵，才判定真正進入結果頁。





### 📅 2026-06-19 | 異常 Alert 容錯與路徑對接

* **第三版本 (`20260619/tii_health_scraper_v3.py`)**
* **現象：** 手動填寫驗證碼若不慎填錯，網站會跳出瀏覽器原生彈出視窗 `Alert: 識別碼錯誤！`，爬蟲因未處理 Alert 直接中斷崩潰。
* **修正：** 在監控迴圈中引入 `try-except` 區塊，捕捉 `UnexpectedAlertPresentException`。當偵測到錯誤 Alert 時，自動執行 `driver.switch_to.alert.accept()` 將彈窗關閉，並引導使用者於 180 秒內重新輸入。


* **輔助測試 (`20260619/step4_save_result_list.py`)**
* **現象：** 成功進入結果列表後，程式識別出數百筆商品，但下載輸出全為 `{'product': '...', 'reason': 'no PDF found'}`。
* **診斷：** 執行 `python step4_save_result_list.py` 儲存真實的列表頁 HTML 以分析超連結格式。發現商品詳細頁超連結為相對路徑（如 `DetailList.aspx?productId=...`），舊版程式使用字串相加，生成了 `...tii.org.twDetailList.aspx`（漏掉了斜線 `/`），引發 404 錯誤。
* **修正：** 程式全面改用標準 `urllib.parse.urljoin()` 進行路徑對接。



### 📅 2026-06-20 | 繞過憑證封鎖與非標準 HTML 破解

* **網路優化版本 (`20260620/v4.1/tii_health_scraper_v4.1.py`)**
* **現象：** 修正路徑後，後端 `requests.Session()` 在請求詳細頁時，依舊全數回傳錯誤。
* **診斷：** 查看 `error_log.txt`，發現在 Windows 本地端環境下，Python 內建的 CA 憑證鏈無法透過保發中心的 SSL 驗證，直接觸發了 `SSLError`。
* **修正：** 在 `requests` 請求中強制加入 `verify=False` 參數以跳過 SSL 驗證，並使用 `urllib3.disable_warnings()` 壓制不安全請求的警告。


* **最終穩定版本 (`20260620/v4.2/tii_health_scraper_v4.2.py`)**
* **現象：** 打通 HTTP 請求後，程式仍然沒有存下 PDF，反而在本地端產生了數百個 `debug_detail_*.html`。
* **診斷：** 對產生的診斷 HTML 進行文本分析，發現兩個網頁底層特殊防禦：
1. **非 PDF 直連：** 網站並非點擊 `.pdf` 檔案直連，而是透過後台處理程序 `Open2.ashx?id=GUID` 動態串流輸出。
2. **無引號標籤：** 詳細頁源碼中的 HTML 語法極不標準，`href` 屬性完全沒有使用引號包裹：`<a href=Open2.ashx?id=6323f3ea...>XXXX-A.pdf</a>`。這導致 BeautifulSoup 的 DOM 解析器在提取 `href` 屬性時全數判定為 `None`。


* **修正：** 徹底棄用 BeautifulSoup 的節點尋找邏輯，改用純文字正則表達式（Regex）直接對詳細頁的原始碼文本進行字串匹配：`re.findall(r'href=([^\s>]+Open2\.ashx\?id=[^\s>]+)', page_text)`。同時比對標籤文字，若包含 `-A.pdf` 則判定為目標「保單條款」，成功完成專題海量批次下載管線。



---

## 📂 版本演進摘要 (Version Summary)

* **`20260604` 版本：** 初代自動化探索，建構基礎爬取與 GVDR 前處理框架，但因目標鎖定全文檢索頁面而產生超時潰散。
* **`20260605` 版本 (`tii_health_scraper.py`)：** 導向至正確的商品查詢頁，實作自動勾選未停售（`endDate2`），並將結果頁判定從網址監控改為「DOM 元件消失偵測」。
* **`20260619` 版本 (`tii_health_scraper_v3.py`)：** 引入 Alert 捕捉與自動關閉機制，修復驗證碼填錯時程式直接中斷崩潰的錯誤。
* **`20260620/v4.1` 版本 (`tii_health_scraper_v4.1.py`)：** 使用 `urljoin` 修正相對路徑缺少斜線的 Bug，並於 Requests Session 中強制關閉憑證驗證（`verify=False`），突破 Windows 環境下的 SSL 驗證阻擋。
* **`20260620/v4.2` 版本 (`tii_health_scraper_v4.2.py`)：** 最終穩定版。改用文字層級的 Regex 盲測抓取無引號的 `Open2.ashx` 動態下載連結，精準過濾 `-A.pdf` 條款，成功完成醫療險批次下載管線，但因為他將全部資料(包含保險商品內容說明、 保單條款、 要保書(僅供參考)、費率(本費率表為一般要保人投保時適用之標準費率，如欲瞭解個人所適用之保險費，請至"發行公司"項目連結該公司網站)、 附加保險(依各公司現行核保規定辦理)、理賠文件、 總經理或其授權之部門主管及保險商品簽署人員名冊等)，故重寫一版本v4.3。
* **`20260620/v4.3` 版本 (`tii_health_scraper_v4.3.py`)：** 爬蟲分類最終版本，僅抓取保險條款及保險商品內容說明兩項
