"""
Step 3：測試「開始查詢」按鈕是否真的有反應
==========================================
跑這支，手動填好驗證碼後按查詢，
觀察終端機是否印出 URL 變化 / alert / 任何訊號。
讓我們確認問題是「按鈕沒反應」還是「跳轉判斷錯誤」。
"""

import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

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
print("程式會持續監控 60 秒，印出任何 URL 變化或 alert。\n")

last_url = driver.current_url
print(f"[起始 URL] {last_url}")

start = time.time()
while time.time() - start < 60:
    # 檢查 alert
    try:
        alert = driver.switch_to.alert
        print(f"[ALERT 偵測到] {alert.text!r}")
        alert.accept()
        print("[ALERT 已關閉]")
    except Exception:
        pass

    # 檢查 URL 變化
    try:
        cur = driver.current_url
        if cur != last_url:
            print(f"[URL 變化] {last_url} → {cur}")
            last_url = cur
    except Exception as e:
        print(f"[讀取 URL 發生例外] {e}")

    # 檢查頁面是否有「識別碼錯誤」或「查無資料」等文字
    try:
        src = driver.page_source
        for kw in ["識別碼錯誤", "查無資料", "ResultQueryAll", "商品名稱"]:
            if kw in src:
                print(f"[頁面內容含關鍵字] {kw!r}")
    except Exception:
        pass

    time.sleep(1)

print(f"\n[結束時 URL] {driver.current_url}")
print("60 秒監控結束，瀏覽器保持開啟，請手動觀察。按 Ctrl+C 結束程式。")

# 保持瀏覽器開啟讓你繼續觀察
try:
    while True:
        time.sleep(5)
except KeyboardInterrupt:
    driver.quit()
