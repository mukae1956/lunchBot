import os
import io
import time
import json
import base64
import datetime
import subprocess
import requests
from PIL import Image
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
HISTORY_PATH = "data/history.json"
IMAGE_DIR = "data/images"
MAX_HISTORY = 30            # 최대 보관 일수

client = OpenAI()


# ─────────────────────────────────────────
# 0. 점심시간 (홀수 달 11:50 / 짝수 달 12:10)
# ─────────────────────────────────────────

def lunch_time_korean() -> str:
    return "11시 50분" if datetime.date.today().month % 2 == 1 else "12시 10분"


def get_lunch_time_msg() -> str:
    return f"데이터분석과 점심시간은 {lunch_time_korean()}입니다!"


# ─────────────────────────────────────────
# 1. 히스토리 읽기 / 저장
# ─────────────────────────────────────────

def load_history() -> list:
    if not os.path.exists(HISTORY_PATH):
        return []
    with open(HISTORY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_history(history: list):
    os.makedirs("data", exist_ok=True)
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def upsert_history(history: list, entry: dict) -> list:
    """오늘 날짜 항목이 있으면 덮어쓰고, 없으면 추가. MAX_HISTORY 초과분 제거."""
    history = [h for h in history if h["date"] != entry["date"]]
    history.append(entry)
    history.sort(key=lambda x: x["date"])
    return history[-MAX_HISTORY:]


def get_recent(history: list, n: int = 5) -> list:
    """오늘 제외 최근 n일 데이터"""
    today = datetime.date.today().isoformat()
    return [h for h in history if h["date"] != today][-n:]


def get_recent_terms(history: list, n: int = 15) -> list:
    """최근 history에서 이미 다룬 IT 용어 목록 (중복 방지용)."""
    terms = []
    for h in history[-n:]:
        term = h.get("it_term", {}).get("term")
        if term:
            terms.append(term)
    return terms


# ─────────────────────────────────────────
# 2. 크롤링
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
# 3. GPT 분석
# ─────────────────────────────────────────

def analyze_nutrition(items: list) -> dict:
    prompt = f"""다음은 구내식당 점심 메뉴야: {", ".join(items)}

각 음식의 1인분 기준 칼로리(kcal)와 탄수화물/단백질/지방(g)을 추정하고
전체 합계를 내줘. 아래 JSON 형식으로만 응답해:
{{
  "dishes": [{{"name": "음식명", "kcal": 0, "carb": 0, "protein": 0, "fat": 0}}],
  "total": {{"kcal": 0, "carb": 0, "protein": 0, "fat": 0}}
}}"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def recommend_dinner(today_items: list, recent: list) -> str:
    recent_summary = ""
    if recent:
        lines = []
        for h in recent:
            t = h["nutrition"]["total"]
            lines.append(
                f"- {h['date']} ({h['day']}): {', '.join(h['items'])} "
                f"/ {t['kcal']}kcal 탄{t['carb']}g 단{t['protein']}g 지{t['fat']}g"
            )
        recent_summary = "최근 점심 기록:\n" + "\n".join(lines)

    prompt = f"""너는 영양 균형을 고려한 저녁 메뉴 추천 전문가야.

오늘 점심: {", ".join(today_items)}
{recent_summary}

위 정보를 바탕으로:
1. 오늘 점심에서 부족한 영양소를 파악해
2. 최근 5일 식단 패턴을 고려해
3. 저녁 메뉴 3가지를 추천하고 각각 한 줄로 이유를 설명해

답변은 간결하게 한국어로, 이모지 포함해서 친근하게 써줘."""

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()


def get_it_term(recent_terms: list) -> dict:
    """오늘의 IT 용어 하나 생성. 최근 다룬 용어는 제외해서 중복 방지."""
    avoid = ", ".join(recent_terms) if recent_terms else "없음"
    prompt = f"""오늘의 IT 용어를 딱 하나만 골라줘.
데이터분석/개발 전공 학생이 알아두면 좋은 실무 용어로.
최근에 이미 다룬 아래 용어들은 제외해줘: {avoid}

아래 JSON 형식으로만 응답해:
{{
  "term": "용어명 (영문이면 한글 병기)",
  "definition": "1~2문장으로 쉽고 친근하게 설명",
  "example": "실무에서 어떻게 쓰이는지 한 줄 예시"
}}"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


# ─────────────────────────────────────────
# 3-1. gpt-image-1 급식판 이미지 생성 → 압축 → repo 호스팅 URL
# ─────────────────────────────────────────

def generate_meal_jpeg(items: list) -> bytes | None:
    """급식판 사진을 gpt-image-1로 생성 → 축소/JPEG(<=95KB) 압축한 바이트 반환."""
    menu = ", ".join(items)
    prompt = (
        "A Korean cafeteria stainless-steel meal tray (급식판) photographed from directly above, "
        "divided into compartments, each compartment neatly filled with the day's dishes. "
        f"The dishes are: {menu}. "
        "Rice in the largest compartment, soup or stew in the round compartment, "
        "side dishes (반찬) in the smaller compartments, with chopsticks and a spoon on the side. "
        "Realistic appetizing food photography, bright even lighting, clean wooden table background."
    )
    try:
        # gpt-image-1은 항상 base64(b64_json)로 반환. quality: low/medium/high/auto.
        resp = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            quality="medium",
            n=1,
        )
        raw = base64.b64decode(resp.data[0].b64_json)

        # 카드 렌더링 안정성을 위해 작게 압축 (95KB 이하 목표)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((640, 640))
        quality = 85
        while True:
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=quality, optimize=True)
            data = buf.getvalue()
            if len(data) <= 95_000 or quality <= 40:
                return data
            quality -= 10
    except Exception as e:
        print(f"이미지 생성 실패: {e}")
        return None


def persist_meal_image(jpeg_bytes: bytes | None, date_str: str) -> str | None:
    """
    JPEG 바이트를 repo에 저장하고 raw.githubusercontent URL(영구)을 반환한다.
    로컬에서는 공개 호스팅이 불가하므로 사진을 생략(None)한다.
    ※ raw URL이 Teams 카드에서 보이려면 repo가 반드시 public 이어야 함.
    """
    if not jpeg_bytes:
        return None
    os.makedirs(IMAGE_DIR, exist_ok=True)
    path = f"{IMAGE_DIR}/{date_str}.jpg"
    with open(path, "wb") as f:
        f.write(jpeg_bytes)

    if not os.environ.get("GITHUB_ACTIONS"):
        print("[로컬] 공개 호스팅 불가 — 카드에서 사진 생략")
        return None

    repo_full = os.environ["GITHUB_REPOSITORY"]    # owner/repo
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    return f"https://raw.githubusercontent.com/{repo_full}/{branch}/{path}"


def wait_until_public(url: str | None, tries: int = 8, delay: float = 3.0) -> str | None:
    """push 직후 raw URL이 잠깐 404일 수 있으니 200 될 때까지 대기(최선)."""
    if not url:
        return None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                print(f"이미지 URL 공개 확인됨 (시도 {i + 1})")
                return url
            print(f"이미지 URL 아직 {r.status_code}, 재시도...")
        except Exception as e:
            print(f"이미지 URL 확인 오류: {e}")
        time.sleep(delay)
    print("이미지 URL 공개 확인 실패(그래도 전송 시도)")
    return url


# ─────────────────────────────────────────
# 4. 카드 빌더 (전부 카드에 담기)
# ─────────────────────────────────────────

def build_card(today: str, items: list, nutrition: dict, recent: list,
               dinner_rec: str, meal_image_url: str | None = None,
               it_term: dict | None = None) -> list:
    body = []

    # ── 점심시간 안내 (맨 위)
    body.append({
        "type": "TextBlock",
        "text": get_lunch_time_msg(),
        "weight": "Bolder",
        "size": "Medium",
        "color": "Accent",
        "wrap": True
    })

    # ── 헤더
    body.append({
        "type": "TextBlock",
        "text": f"🍱 오늘({today}) 점심",
        "weight": "Bolder",
        "size": "Large"
    })

    # ── 급식판 사진 (한 눈에)
    if meal_image_url:
        body.append({
            "type": "Image",
            "url": meal_image_url,
            "size": "Stretch",
            "altText": f"{today} 급식 이미지",
            "spacing": "Medium",
            "msTeams": {"allowExpand": True}
        })

    # ── 메뉴별 영양
    for d in nutrition["dishes"]:
        body.append({
            "type": "TextBlock",
            "wrap": True,
            "text": (
                f"**{d['name']}** — {d['kcal']}kcal "
                f"(탄 {d['carb']}g / 단 {d['protein']}g / 지 {d['fat']}g)"
            )
        })

    t = nutrition["total"]
    body.append({"type": "TextBlock", "text": "— 오늘 합계 —", "weight": "Bolder", "spacing": "Medium"})
    body.append({"type": "FactSet", "facts": [
        {"title": "칼로리", "value": f"{t['kcal']} kcal"},
        {"title": "탄수화물", "value": f"{t['carb']} g"},
        {"title": "단백질", "value": f"{t['protein']} g"},
        {"title": "지방", "value": f"{t['fat']} g"},
    ]})

    # ── 최근 누적
    if recent:
        acc = {"kcal": 0, "carb": 0, "protein": 0, "fat": 0}
        for h in recent:
            for k in acc:
                acc[k] += h["nutrition"]["total"][k]
        for k in acc:
            acc[k] += t[k]  # 오늘 포함

        days_count = len(recent) + 1
        body.append({
            "type": "TextBlock",
            "text": f"— 최근 {days_count}일 누적 —",
            "weight": "Bolder",
            "spacing": "Medium"
        })
        body.append({"type": "FactSet", "facts": [
            {"title": "칼로리", "value": f"{acc['kcal']} kcal"},
            {"title": "탄수화물", "value": f"{acc['carb']} g"},
            {"title": "단백질", "value": f"{acc['protein']} g"},
            {"title": "지방", "value": f"{acc['fat']} g"},
        ]})

    # ── 저녁 추천
    body.append({
        "type": "TextBlock",
        "text": "🌙 오늘 저녁 추천",
        "weight": "Bolder",
        "spacing": "Medium"
    })
    body.append({
        "type": "TextBlock",
        "text": dinner_rec,
        "wrap": True
    })

    # ── 오늘의 IT 용어
    if it_term:
        body.append({
            "type": "TextBlock",
            "text": "💡 오늘의 IT 용어",
            "weight": "Bolder",
            "spacing": "Medium"
        })
        body.append({
            "type": "TextBlock",
            "text": f"**{it_term['term']}**\n\n{it_term['definition']}",
            "wrap": True
        })
        if it_term.get("example"):
            body.append({
                "type": "TextBlock",
                "text": f"📌 {it_term['example']}",
                "wrap": True,
                "isSubtle": True,
                "size": "Small"
            })

    # ── 푸터
    body.append({
        "type": "TextBlock",
        "text": "※ 영양정보·식단 사진은 AI 추정/생성입니다",
        "size": "Small",
        "isSubtle": True,
        "spacing": "Medium"
    })

    return body


# ─────────────────────────────────────────
# 5. 전송
# ─────────────────────────────────────────

def send_card(body: list):
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {
                "type": "AdaptiveCard",
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "version": "1.2",   # 모바일 호환 위해 1.2
                "body": body,
            }
        }]
    }
    requests.post(WEBHOOK_URL, json=payload)


def send_text(text: str):
    send_card([{"type": "TextBlock", "text": text, "wrap": True}])


# ─────────────────────────────────────────
# 6. GitHub 자동 커밋
# ─────────────────────────────────────────

def git_commit_history():
    """GitHub Actions 환경에서만 동작. 로컬에서는 스킵."""
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[로컬] git 커밋 스킵")
        return
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "config", "user.email", "bot@github-actions"], check=True)
    subprocess.run(["git", "config", "user.name", "Lunch Menu Bot"], check=True)
    subprocess.run(["git", "add", "data/"], check=True)  # history.json + images
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if result.returncode != 0:
        subprocess.run(["git", "commit", "-m", f"chore: update lunch data {today}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("히스토리/이미지 커밋 완료")
    else:
        print("변경사항 없음, 커밋 스킵")


# ─────────────────────────────────────────
# 메인
# ─────────────────────────────────────────

if __name__ == "__main__":
    today_str = datetime.date.today().isoformat()
    today_label, items = get_lunch_for_today()

    if not items:
        send_text(f"{get_lunch_time_msg()}\n\n오늘({today_label})은 식단 정보가 없어요 😢")
        print("메뉴 없음")
    else:
        try:
            history = load_history()
            recent = get_recent(history, n=5)
            recent_terms = get_recent_terms(history)

            nutrition = analyze_nutrition(items)
            dinner_rec = recommend_dinner(items, recent)
            it_term = get_it_term(recent_terms)

            # 급식판 이미지 생성 → repo 저장 → 공개 URL
            jpeg = generate_meal_jpeg(items)
            meal_image_url = persist_meal_image(jpeg, today_str)

            # 히스토리 업데이트
            entry = {
                "date": today_str,
                "day": today_label,
                "items": items,
                "nutrition": nutrition,
                "image": meal_image_url,
                "it_term": it_term,
            }
            history = upsert_history(history, entry)
            save_history(history)
            git_commit_history()  # 이미지 push가 카드 전송보다 먼저 끝나야 raw URL이 뜸

            # push 직후 CDN 전파 대기 후 카드 전송
            meal_image_url = wait_until_public(meal_image_url)
            card_body = build_card(today_label, items, nutrition, recent,
                                   dinner_rec, meal_image_url, it_term)
            send_card(card_body)
            print("전송 완료")

        except Exception as e:
            menu = "\n".join(f"- {m}" for m in items)
            send_text(
                f"{get_lunch_time_msg()}\n\n🍱 오늘({today_label}) 점심\n{menu}\n\n(분석 실패: {e})"
            )
            print("분석 실패, 메뉴만 전송:", e)
