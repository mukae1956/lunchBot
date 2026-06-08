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
        body_text = f"🍱 오늘({today}) 점심 메뉴\n\n" + "\n".join(f"- {m}" for m in items)
    else:
        body_text = f"오늘({today})은 식단 정보가 없어요 😢"

    payload = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4",
                    "body": [
                        {"type": "TextBlock", "text": body_text, "wrap": True}
                    ]
                }
            }
        ]
    }
    requests.post(WEBHOOK_URL, json=payload)
    print(body_text)

if __name__ == "__main__":
    send(*get_lunch_for_today())
