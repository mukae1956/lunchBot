import os
import json
import datetime
import requests
from bs4 import BeautifulSoup
import anthropic

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 자동 사용


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


def analyze(items):
    prompt = f"""다음은 구내식당 점심 메뉴야: {", ".join(items)}

각 음식의 1인분 기준 칼로리(kcal)와 탄수화물/단백질/지방(g)을 추정하고
전체 합계를 내줘. 아래 JSON 형식으로만 응답해 (마크다운·설명 없이 JSON만):
{{
  "dishes": [{{"name": "음식명", "kcal": 0, "carb": 0, "protein": 0, "fat": 0}}],
  "total": {{"kcal": 0, "carb": 0, "protein": 0, "fat": 0}}
}}"""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def build_card(today, items, data):
    body = [{"type": "TextBlock", "text": f"🍱 오늘({today}) 점심", "weight": "Bolder", "size": "Large"}]

    for d in data["dishes"]:
        body.append({
            "type": "TextBlock", "wrap": True,
            "text": f"**{d['name']}** — {d['kcal']}kcal (탄 {d['carb']}/단 {d['protein']}/지 {d['fat']}g)"
        })

    t = data["total"]
    body.append({"type": "TextBlock", "text": "— 합계 —", "weight": "Bolder", "spacing": "Medium"})
    body.append({"type": "FactSet", "facts": [
        {"title": "칼로리", "value": f"{t['kcal']} kcal"},
        {"title": "탄수화물", "value": f"{t['carb']} g"},
        {"title": "단백질", "value": f"{t['protein']} g"},
        {"title": "지방", "value": f"{t['fat']} g"},
    ]})
    body.append({"type": "TextBlock", "text": "※ 영양정보는 AI 추정치입니다", "size": "Small", "isSubtle": True})
    return body


def send_card(body):
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.4",
                "body": body,
            }
        }]
    }
    requests.post(WEBHOOK_URL, json=payload)


def send_text(text):  # 메뉴 없거나 분석 실패 시 fallback
    send_card([{"type": "TextBlock", "text": text, "wrap": True}])


if __name__ == "__main__":
    today, items = get_lunch_for_today()
    if not items:
        send_text(f"오늘({today})은 식단 정보가 없어요 😢")
    else:
        try:
            data = analyze(items)
            send_card(build_card(today, items, data))
            print("전송 완료")
        except Exception as e:
            # 분석 실패해도 메뉴는 보냄
            menu = "\n".join(f"- {m}" for m in items)
            send_text(f"🍱 오늘({today}) 점심\n{menu}\n\n(영양정보 분석 실패: {e})")
            print("분석 실패, 메뉴만 전송:", e)
