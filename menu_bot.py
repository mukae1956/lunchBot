import os
import datetime
import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

def get_lunch_for_today():
    res = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")
    today = WEEKDAYS[datetime.datetime.now().weekday()]

    for row in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if cells and cells[0] == today:
            lunch_text = cells[2] if len(cells) > 2 else ""
            items = [x.strip() for x in lunch_text.split(",") if x.strip()]
            return today, items
    return today, []

def send(today, items):
    if items:
        body = f"🍱 오늘({today}) 점심 메뉴\n" + "\n".join(f"• {m}" for m in items)
    else:
        body = f"오늘({today})은 식단 정보가 없어요 😢"
    requests.post(WEBHOOK_URL, json={"content": body})  # 슬랙이면 "text"
    print(body)

if __name__ == "__main__":
    send(*get_lunch_for_today())
