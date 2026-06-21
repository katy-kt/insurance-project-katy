"""
Step 1：保發中心框架結構診斷腳本
==============================
先跑這支，它會把頁面所有 frame/iframe 的名稱、ID、src 印出來，
並嗅探每個 frame 內的關鍵元件 ID，讓我們知道要切到哪一層。

執行方式：
    python step1_diagnose_frames.py

跑完後把終端機輸出貼給 Claude，就可以進到 step2 正式爬蟲。
"""

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

try:
    from webdriver_manager.chrome import ChromeDriverManager
    service = Service(ChromeDriverManager().install())
except ImportError:
    service = Service()   # 已在 PATH 的 chromedriver

# ── 啟動瀏覽器（保持可見，方便觀察）─────────────────────────
options = Options()
options.add_argument("--window-size=1400,900")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_argument(
    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

driver = webdriver.Chrome(service=service, options=options)
TARGET_URL = "https://insprod.tii.org.tw/QueryFullText.aspx"

print(f"\n{'='*60}")
print(f"正在開啟：{TARGET_URL}")
print(f"{'='*60}\n")

driver.get(TARGET_URL)
time.sleep(5)   # 等頁面完整載入

# ── 1. 頂層頁面基本資訊 ─────────────────────────────────────
print(f"[頂層] Title  : {driver.title}")
print(f"[頂層] URL    : {driver.current_url}")
print(f"[頂層] 原始碼前 500 字：")
print(driver.page_source[:500])
print()

# ── 2. 列出所有頂層 <frame> 與 <iframe> ─────────────────────
driver.switch_to.default_content()
frames = driver.find_elements(By.TAG_NAME, "frame")
iframes = driver.find_elements(By.TAG_NAME, "iframe")

print(f"{'='*60}")
print(f"頂層找到 <frame> 數量：{len(frames)}")
print(f"頂層找到 <iframe> 數量：{len(iframes)}")
print(f"{'='*60}\n")

all_frame_elements = frames + iframes
for i, f in enumerate(all_frame_elements):
    name = f.get_attribute("name") or "(無 name)"
    fid  = f.get_attribute("id")   or "(無 id)"
    src  = f.get_attribute("src")  or "(無 src)"
    print(f"  Frame [{i}]  name={name!r}  id={fid!r}  src={src!r}")

print()

# ── 3. 逐一切入每個 frame，嗅探關鍵元件 ────────────────────
KEYWORDS = [
    "ddlInsuranceType", "insuranceType",
    "ddl_InsuType", "btnSearch", "searchButton",
    "ctl00_ContentPlaceHolder1",
    "QueryFullText", "商品名稱", "查詢",
]

def sniff_frame(label: str):
    """在目前的 frame 內搜尋關鍵字，印出摘要"""
    src = driver.page_source
    found = [kw for kw in KEYWORDS if kw in src]
    all_selects = driver.find_elements(By.TAG_NAME, "select")
    all_inputs  = driver.find_elements(By.TAG_NAME, "input")
    all_buttons = driver.find_elements(By.TAG_NAME, "button")

    print(f"  ▶ {label}")
    print(f"    Title  : {driver.title!r}")
    print(f"    URL    : {driver.current_url!r}")
    print(f"    原始碼找到的關鍵字: {found}")
    print(f"    <select> 元件數: {len(all_selects)}")
    for s in all_selects:
        print(f"      select id={s.get_attribute('id')!r}  name={s.get_attribute('name')!r}")
    print(f"    <input>  元件數（type=text/submit/button）: {len(all_inputs)}")
    for inp in all_inputs[:10]:   # 只印前 10 個避免太長
        print(f"      input  id={inp.get_attribute('id')!r}  name={inp.get_attribute('name')!r}  type={inp.get_attribute('type')!r}  value={inp.get_attribute('value')!r}")
    print(f"    <button> 元件數: {len(all_buttons)}")
    for btn in all_buttons:
        print(f"      button id={btn.get_attribute('id')!r}  text={btn.text!r}")
    print()


# 頂層
sniff_frame("頂層 (default_content)")

# 逐 frame 切入
for i, f in enumerate(all_frame_elements):
    driver.switch_to.default_content()
    name = f.get_attribute("name") or f.get_attribute("id") or str(i)
    try:
        driver.switch_to.frame(i)
        sniff_frame(f"Frame [{i}]  name={name!r}")

        # 也嘗試切入子 frame
        sub_frames = driver.find_elements(By.TAG_NAME, "frame") + \
                     driver.find_elements(By.TAG_NAME, "iframe")
        for j, sf in enumerate(sub_frames):
            sname = sf.get_attribute("name") or sf.get_attribute("id") or str(j)
            try:
                driver.switch_to.frame(j)
                sniff_frame(f"  SubFrame [{i}][{j}]  name={sname!r}")
                driver.switch_to.parent_frame()
            except Exception as e:
                print(f"  SubFrame [{i}][{j}] 切入失敗: {e}\n")

    except Exception as e:
        print(f"Frame [{i}] 切入失敗: {e}\n")

print(f"{'='*60}")
print("診斷完成！請把以上輸出完整複製給 Claude。")
print("瀏覽器將保持開啟 30 秒供你觀察，之後自動關閉。")
print(f"{'='*60}\n")
time.sleep(30)
driver.quit()
