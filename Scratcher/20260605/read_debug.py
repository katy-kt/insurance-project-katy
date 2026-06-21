from bs4 import BeautifulSoup

with open("debug_result_page.html", encoding="utf-8") as f:
    html = f.read()

soup = BeautifulSoup(html, "html.parser")

print("=== TITLE ===")
print(soup.title.string if soup.title else "")

print("\n=== ALL INPUTS ===")
for i in soup.find_all("input"):
    print(" ", i.get("id"), i.get("name"), i.get("type"), repr(i.get("value", "")))

print("\n=== CHECKBOXES ===")
for i in soup.find_all("input", type="checkbox"):
    print(" ", i.get("id"), i.get("name"), i.get("value"), i.get("checked"))

print("\n=== FIRST 20 LINKS ===")
for a in soup.find_all("a")[:20]:
    print(" ", repr(a.get("href")), repr(a.get("onclick", "")[:80]), repr(a.get_text(strip=True)[:40]))

print("\n=== PAGE TEXT (first 3000) ===")
print(soup.get_text()[:3000])
