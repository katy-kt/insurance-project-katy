"""
Step 4：抓取並儲存「結果列表頁」的真實 HTML
==========================================
跑這支，自動選好選項、等你填驗證碼按查詢，
一旦偵測到查詢表單消失，立刻把當下頁面存成 result_list_real.html
並印出所有 <a> 連結，讓我們看清楚商品連結的真實格式。
"""

import time
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import NoSuchElementException, UnexpectedAlertPresentException

try:
    from webdriver_manager.chrome import ChromeDriverManager
    service = Service(ChromeDriverManager().install())
except ImportError:
    service = Service()

options = Options()
options.add_argument("--window-size=1400,900")
options.add_argument("--no-sandbox")
options.add_experimental_option("excludeSwitches", ["enable-automation"])

driver = webdriver.Chrome(service=service, options=options)
driver.get("https://insprod.tii.org.tw/Query.aspx")
time.sleep(3)

# 選好選項
Select(driver.find_element(By.NAME, "categoryId")).select_by_visible_text("人身保險")
time.sleep(1.5)
sel = Select(driver.find_element(By.NAME, "CompanyID"))
target = next((o for o in sel.options if "壽險全部" in o.text), None)
if target:
    sel.select_by_visible_text(target.text)
time.sleep(1)
Select(driver.find_element(By.NAME, "f_CategoryId1")).select_by_visible_text("健康保險")
time.sleep(0.5)
cb = driver.find_element(By.ID, "endDate2")
if not cb.is_selected():
    cb.click()

print("\n選項已設定好。請手動填驗證碼，然後按下「開始查詢」按鈕。")
print("程式會等到查詢表單消失（代表已進入結果頁），最多等 180 秒。\n")

deadline = time.time() + 180
while time.time() < deadline:
    try:
        alert = driver.switch_to.alert
        print(f"[ALERT] {alert.text!r}，已關閉，請重新填驗證碼")
        alert.accept()
        time.sleep(1)
        continue
    except Exception:
        pass

    try:
        form_present = len(driver.find_elements(By.NAME, "bmpC")) > 0
    except UnexpectedAlertPresentException:
        try:
            driver.switch_to.alert.accept()
        except Exception:
            pass
        continue

    if not form_present:
        print("偵測到查詢表單已消失，進入結果頁！")
        break
    time.sleep(1)
else:
    print("等待超時。")
    driver.quit()
    raise SystemExit

time.sleep(2)

# ── 存檔 ──
with open("result_list_real.html", "w", encoding="utf-8") as f:
    f.write(driver.page_source)
print("\n已儲存完整頁面到 result_list_real.html")

# ── 印出所有連結 ──
soup = BeautifulSoup(driver.page_source, "html.parser")
all_links = soup.find_all("a")
print(f"\n=== 共找到 {len(all_links)} 個 <a> 連結，列出前 60 個 ===\n")
for i, a in enumerate(all_links[:60]):
    href = a.get("href", "")
    onclick = a.get("onclick", "")
    text = a.get_text(strip=True)
    print(f"[{i}] href={href!r}  onclick={onclick[:70]!r}  text={text[:40]!r}")

# ── 找看起來像商品名稱的連結（中文字多的）──
print("\n=== 文字含中文且長度 > 5 的連結（可能是商品名稱）===\n")
for i, a in enumerate(all_links):
    text = a.get_text(strip=True)
    if len(text) > 5 and any('\u4e00' <= c <= '\u9fff' for c in text):
        href = a.get("href", "")
        onclick = a.get("onclick", "")
        print(f"[{i}] href={href!r}  onclick={onclick[:70]!r}  text={text!r}")

print("\n瀏覽器保持開啟 30 秒。")
time.sleep(30)
driver.quit()
