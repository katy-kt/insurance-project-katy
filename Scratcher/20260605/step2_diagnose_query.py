"""
Step 2：保發中心 Query.aspx 元件診斷腳本
=========================================
針對正確的查詢頁面，把所有 select / input / button 的 ID 印出來。
跑完把輸出貼給 Claude。
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
    service = Service()

options = Options()
options.add_argument("--window-size=1400,900")
options.add_argument("--no-sandbox")
options.add_experimental_option("excludeSwitches", ["enable-automation"])

driver = webdriver.Chrome(service=service, options=options)
driver.get("https://insprod.tii.org.tw/Query.aspx")
time.sleep(5)

print(f"\n{'='*60}")
print(f"Title : {driver.title}")
print(f"URL   : {driver.current_url}")
print(f"{'='*60}\n")

# 所有 <select>
selects = driver.find_elements(By.TAG_NAME, "select")
print(f"【SELECT 元件數：{len(selects)}】")
for s in selects:
    from selenium.webdriver.support.ui import Select
    sel = Select(s)
    opts = [o.text.strip() for o in sel.options]
    print(f"  id={s.get_attribute('id')!r}  name={s.get_attribute('name')!r}")
    print(f"    選項: {opts}")
print()

# 所有 <input>
inputs = driver.find_elements(By.TAG_NAME, "input")
print(f"【INPUT 元件數：{len(inputs)}】")
for i in inputs:
    print(f"  id={i.get_attribute('id')!r}  name={i.get_attribute('name')!r}"
          f"  type={i.get_attribute('type')!r}  value={i.get_attribute('value')!r}")
print()

# 所有 <button>
buttons = driver.find_elements(By.TAG_NAME, "button")
print(f"【BUTTON 元件數：{len(buttons)}】")
for b in buttons:
    print(f"  id={b.get_attribute('id')!r}  text={b.text!r}")

print(f"\n{'='*60}")
print("原始碼前 1000 字：")
print(driver.page_source[:1000])
print(f"{'='*60}")
print("診斷完成！瀏覽器保持開啟 30 秒。")
time.sleep(30)
driver.quit()
