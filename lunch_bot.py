import os
import requests
from bs4 import BeautifulSoup

WEBHOOK_URL = os.environ["WEBHOOK_URL"]

def get_menu():
    # ↓↓↓ 이 부분이 스크래핑할 사이트에 맞게 바뀌는 핵심 영역
    url = "https://스크래핑할사이트주소"
    res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    res.raise_for_status()
    soup = BeautifulSoup(res.text, "html.parser")

    # 예시: 메뉴가 들어있는 요소를 선택자로 추출
    items = [el.get_text(strip=True) for el in soup.select(".menu-item")]
    return items

def send_to_webhook(items):
    if items:
        text = "🍱 오늘의 점심 메뉴\n" + "\n".join(f"• {m}" for m in items)
    else:
        text = "오늘 메뉴 정보를 못 찾았어요 😢"

    requests.post(
        WEBHOOK_URL,
        json={"content": text},   # 슬랙이면 "text" 로 변경
    )

if __name__ == "__main__":
    menu = get_menu()
    send_to_webhook(menu)
    print("전송 완료:", menu)
