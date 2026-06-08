import os
import json
import datetime
import requests
from bs4 import BeautifulSoup
import anthropic

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용


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
    prompt = f"""다음은 오늘 구내식당 점심 메뉴야: {", ".join(items)}

각 음식의 탄수화물/단백질/지방(g)을 1인분 기준으로 추정하고,
점심 전체 합계를 낸 뒤, 이 점심의 영양 균형을 고려한 저녁 메뉴를 추천해줘.

아래 JSON 형식으로만 응답해. 마크다운이나 다른 설명 없이 JSON만:
{{
  "dishes": [{{"name": "음식명", "carb": 0, "protein": 0, "fat": 0}}],
  "lunch_total": {{"carb": 0, "protein": 0, "fat": 0}},
  "dinner": {{
    "menu": ["추천 음식 2~3개"],
    "reason": "왜 이 저녁을 추천하는지 한 문장",
    "total": {{"carb": 0, "protein": 0, "fat": 0}}
  }}
}}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def build_message(today, items, data):
    lt = data["lunch_total"]
    d = data["dinner"]
    dt = d["total"]

    lines = [f"🍱 **오늘({today}) 점심**"]
    for dish in data["dishes"]:
        lines.append(f"• {dish['name']} (탄 {dish['carb']}/단 {dish['protein']}/지 {dish['fat']}g)")
    lines.append(f"→ 점심 합계: 탄 {lt['carb']} / 단 {lt['protein']} / 지 {lt['fat']}g")
    lines.append("")
    lines.append(f"🌙 **저녁 추천**: {', '.join(d['menu'])}")
    lines.append(f"💡 {d['reason']}")
    lines.append(f"→ 저녁 예상: 탄 {dt['carb']} / 단 {dt['protein']} / 지 {dt['fat']}g")
    lines.append("")
    lines.append("※ 탄단지는 AI 추정치입니다")
    return "\n".join(lines)


if __name__ == "__main__":
    today, items = get_lunch_for_today()
    if not items:
        requests.post(WEBHOOK_URL, json={"content": f"오늘({today})은 식단 정보가 없어요 😢"})
    else:
        data = analyze(items)
        requests.post(WEBHOOK_URL, json={"content": build_message(today, items, data)})
        print("전송 완료")
