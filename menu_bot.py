import os
import io
import time
import json
import datetime
import subprocess
import requests
from html import escape
from PIL import Image
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

WEBHOOK_URL = os.environ["WEBHOOK_URL"]
URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
HISTORY_PATH = "data/history.json"
PAGE_DIR = "lunch"          # GitHub Pages로 서빙되는 페이지 폴더
MAX_HISTORY = 30            # 최대 보관 일수

client = OpenAI()


# ─────────────────────────────────────────
# 0. 점심시간 (홀수 달 11:50 / 짝수 달 12:10)
# ─────────────────────────────────────────

def lunch_time_hhmm() -> str:
    return "11:50" if datetime.date.today().month % 2 == 1 else "12:10"


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


# ─────────────────────────────────────────
# 3-1. DALL·E 급식판 이미지 → base64 (페이지에 직접 삽입)
# ─────────────────────────────────────────

def generate_meal_image_b64(items: list) -> str | None:
    """오늘 메뉴를 급식판에 담은 사진을 gpt-image-1로 생성 → 축소/JPEG 압축 → base64 반환."""
    import base64
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
        # gpt-image-1은 항상 base64(b64_json)로 반환하며 response_format 파라미터는 없음.
        # quality 값은 low / medium / high / auto.
        resp = client.images.generate(
            model="gpt-image-1",
            prompt=prompt,
            size="1024x1024",
            quality="medium",
            n=1,
        )
        raw = base64.b64decode(resp.data[0].b64_json)

        # 축소 + JPEG 압축 (페이지 용량 절감)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((900, 900))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=82, optimize=True)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as e:
        print(f"이미지 생성 실패: {e}")
        return None


# ─────────────────────────────────────────
# 4. 웹페이지 빌드 (식단 + 영양 + 저녁추천 + 사진)
# ─────────────────────────────────────────

PAGE_SHELL = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__DATE__ 데이터분석과 점심</title>
<link rel="preconnect" href="https://cdn.jsdelivr.net">
<link href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css" rel="stylesheet">
<style>
:root{
  --bg:#e9edf0; --surface:#ffffff; --ink:#1d2529; --muted:#69757e;
  --accent:#e8552d; --green:#2f8f5b; --line:#dde3e8;
}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;}
body{background:var(--bg);color:var(--ink);
  font-family:'Pretendard',system-ui,-apple-system,sans-serif;
  line-height:1.55;-webkit-font-smoothing:antialiased;}
.wrap{max-width:560px;margin:0 auto;padding:20px 16px 56px;}
.eyebrow{font-size:12px;letter-spacing:.16em;color:var(--accent);font-weight:800;}
.title{font-size:26px;font-weight:800;margin:3px 0 0;letter-spacing:-.01em;}
.date{color:var(--muted);font-size:15px;margin-top:3px;}
.timecard{margin-top:16px;background:var(--accent);color:#fff;border-radius:16px;
  padding:15px 18px;display:flex;align-items:baseline;gap:12px;}
.timecard .label{font-size:14px;opacity:.92;}
.timecard .time{font-size:30px;font-weight:800;font-variant-numeric:tabular-nums;margin-left:auto;}
.hero{margin-top:18px;border-radius:18px;overflow:hidden;background:#cfd6db;
  box-shadow:0 10px 30px rgba(20,30,40,.12);}
.hero img{display:block;width:100%;height:auto;}
.hero .noimg{padding:52px 16px;text-align:center;color:var(--muted);font-size:14px;}
.section{margin-top:30px;}
.section h2{font-size:15px;font-weight:800;margin:0 0 12px;display:flex;align-items:center;gap:9px;}
.section h2::before{content:"";width:15px;height:3px;background:var(--accent);border-radius:2px;}
.dish{display:flex;justify-content:space-between;gap:12px;padding:12px 0;border-bottom:1px solid var(--line);}
.dish:last-child{border-bottom:none;}
.dish .name{font-weight:700;font-size:16px;}
.dish .macros{color:var(--muted);font-size:13px;margin-top:3px;font-variant-numeric:tabular-nums;}
.dish .kcal{font-family:ui-monospace,Menlo,monospace;font-weight:700;font-size:15px;white-space:nowrap;}
.label-box{border:2px solid var(--ink);border-radius:12px;padding:14px 16px;background:var(--surface);}
.label-box .lbtitle{font-size:12px;letter-spacing:.12em;font-weight:800;
  border-bottom:6px solid var(--ink);padding-bottom:7px;margin-bottom:10px;}
.facts{display:grid;grid-template-columns:1fr 1fr;gap:2px 18px;}
.fact{display:flex;justify-content:space-between;border-bottom:1px solid var(--line);padding:7px 0;font-size:14px;}
.fact .v{font-family:ui-monospace,Menlo,monospace;font-weight:700;}
.recent{background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:14px 16px;}
.recent .rtitle{font-size:13px;color:var(--muted);font-weight:700;margin-bottom:8px;}
.dinner{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:16px;white-space:pre-wrap;font-size:15px;}
.foot{margin-top:34px;color:var(--muted);font-size:12px;text-align:center;line-height:1.7;}
@media (prefers-reduced-motion:no-preference){.hero img{transition:transform .3s ease;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="eyebrow">한국폴리텍 · 데이터분석과</div>
  <h1 class="title">오늘의 점심</h1>
  <div class="date">__DATE__ (__DAY__)</div>

  <div class="timecard"><span class="label">점심시간</span><span class="time">__TIME__</span></div>

  __HERO__

  <div class="section">
    <h2>오늘 메뉴</h2>
    __MENU__
  </div>

  <div class="section">
    <h2>오늘 영양 합계</h2>
    __TODAY_TOTAL__
  </div>

  __RECENT__

  <div class="section">
    <h2>저녁 추천</h2>
    <div class="dinner">__DINNER__</div>
  </div>

  <div class="foot">영양정보와 급식판 사진은 AI 추정·생성 결과입니다.<br>__GENERATED__ 자동 생성</div>
</div>
</body>
</html>"""


def _fact(title: str, value: str) -> str:
    return f'<div class="fact"><span>{title}</span><span class="v">{value}</span></div>'


def build_html_page(today: str, date_str: str, items: list, nutrition: dict,
                    recent: list, dinner_rec: str, image_b64: str | None) -> str:
    # 사진
    if image_b64:
        hero = f'<div class="hero"><img src="data:image/jpeg;base64,{image_b64}" alt="오늘의 급식판 사진"></div>'
    else:
        hero = '<div class="hero"><div class="noimg">급식판 사진을 불러오지 못했어요</div></div>'

    # 메뉴별
    rows = []
    for d in nutrition["dishes"]:
        rows.append(
            '<div class="dish">'
            f'<div><div class="name">{escape(str(d["name"]))}</div>'
            f'<div class="macros">탄 {d["carb"]}g · 단 {d["protein"]}g · 지 {d["fat"]}g</div></div>'
            f'<div class="kcal">{d["kcal"]} kcal</div>'
            '</div>'
        )
    menu_html = "".join(rows)

    # 오늘 합계 (영양성분표 스타일)
    t = nutrition["total"]
    today_total = (
        '<div class="label-box"><div class="lbtitle">TODAY · 1인분 합계</div><div class="facts">'
        + _fact("칼로리", f"{t['kcal']} kcal")
        + _fact("탄수화물", f"{t['carb']} g")
        + _fact("단백질", f"{t['protein']} g")
        + _fact("지방", f"{t['fat']} g")
        + '</div></div>'
    )

    # 최근 누적
    recent_html = ""
    if recent:
        acc = {"kcal": 0, "carb": 0, "protein": 0, "fat": 0}
        for h in recent:
            for k in acc:
                acc[k] += h["nutrition"]["total"][k]
        for k in acc:
            acc[k] += t[k]
        days = len(recent) + 1
        recent_html = (
            '<div class="section"><h2>최근 누적</h2>'
            f'<div class="recent"><div class="rtitle">최근 {days}일 합계 (오늘 포함)</div>'
            '<div class="facts">'
            + _fact("칼로리", f"{acc['kcal']} kcal")
            + _fact("탄수화물", f"{acc['carb']} g")
            + _fact("단백질", f"{acc['protein']} g")
            + _fact("지방", f"{acc['fat']} g")
            + '</div></div></div>'
        )

    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    html = PAGE_SHELL
    html = html.replace("__DATE__", escape(date_str))
    html = html.replace("__DAY__", escape(today))
    html = html.replace("__TIME__", lunch_time_hhmm())
    html = html.replace("__HERO__", hero)
    html = html.replace("__MENU__", menu_html)
    html = html.replace("__TODAY_TOTAL__", today_total)
    html = html.replace("__RECENT__", recent_html)
    html = html.replace("__DINNER__", escape(dinner_rec))
    html = html.replace("__GENERATED__", generated)
    return html


def publish_page(html: str, date_str: str) -> str | None:
    """페이지를 lunch/{date}.html 로 저장. Actions면 GitHub Pages 공개 URL 반환."""
    os.makedirs(PAGE_DIR, exist_ok=True)
    path = f"{PAGE_DIR}/{date_str}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    # 항상 최신본을 가리키는 고정 링크도 갱신
    with open(f"{PAGE_DIR}/index.html", "w", encoding="utf-8") as f:
        f.write(html)

    if not os.environ.get("GITHUB_ACTIONS"):
        return "file://" + os.path.abspath(path)  # 로컬: 미리보기용 경로

    repo_full = os.environ["GITHUB_REPOSITORY"]   # owner/repo
    owner, repo = repo_full.split("/", 1)
    return f"https://{owner.lower()}.github.io/{repo}/{PAGE_DIR}/{date_str}.html"


def wait_until_public(url: str | None, tries: int = 15, delay: float = 5.0) -> str | None:
    """GitHub Pages 배포는 비동기라 잠깐 404가 날 수 있다. 200 될 때까지 대기(최선)."""
    if not url or url.startswith("file://"):
        return url
    for i in range(tries):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                print(f"페이지 공개 확인됨 (시도 {i + 1})")
                return url
            print(f"페이지 아직 {r.status_code}, 재시도...")
        except Exception as e:
            print(f"페이지 확인 오류: {e}")
        time.sleep(delay)
    print("페이지 공개 확인 실패(그래도 링크 전송 — 곧 배포되면 열림)")
    return url


# ─────────────────────────────────────────
# 5. 카드 빌더 (점심시간 + 링크만)
# ─────────────────────────────────────────

def build_link_card(page_url: str) -> tuple[list, list]:
    body = [
        {
            "type": "TextBlock",
            "text": get_lunch_time_msg(),
            "weight": "Bolder",
            "size": "Large",
            "color": "Accent",
            "wrap": True,
        },
        {
            "type": "TextBlock",
            "text": "식단 및 저녁 추천을 확인하고 싶으면 아래 버튼(또는 링크)을 눌러 주세요!",
            "wrap": True,
            "spacing": "Small",
        },
        {
            "type": "TextBlock",
            "text": f"[🍱 오늘 식단 보러가기]({page_url})",
            "wrap": True,
            "spacing": "Small",
        },
    ]
    actions = [
        {"type": "Action.OpenUrl", "title": "🍱 오늘 식단 보기", "url": page_url}
    ]
    return body, actions


# ─────────────────────────────────────────
# 6. 전송
# ─────────────────────────────────────────

def send_card(body: list, actions: list | None = None):
    content = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",   # 모바일 호환 위해 1.2 유지
        "body": body,
    }
    if actions:
        content["actions"] = actions
    payload = {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": content,
        }]
    }
    requests.post(WEBHOOK_URL, json=payload)


def send_text(text: str):
    send_card([{"type": "TextBlock", "text": text, "wrap": True}])


# ─────────────────────────────────────────
# 7. GitHub 자동 커밋
# ─────────────────────────────────────────

def git_commit_history():
    """GitHub Actions 환경에서만 동작. 로컬에서는 스킵."""
    if not os.environ.get("GITHUB_ACTIONS"):
        print("[로컬] git 커밋 스킵")
        return
    today = datetime.date.today().isoformat()
    subprocess.run(["git", "config", "user.email", "bot@github-actions"], check=True)
    subprocess.run(["git", "config", "user.name", "Lunch Menu Bot"], check=True)
    subprocess.run(["git", "add", "-A"], check=True)  # history.json + lunch/*.html
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
    if result.returncode != 0:
        subprocess.run(["git", "commit", "-m", f"chore: update lunch page {today}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("히스토리/페이지 커밋 완료")
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

            nutrition = analyze_nutrition(items)
            dinner_rec = recommend_dinner(items, recent)
            image_b64 = generate_meal_image_b64(items)

            # 페이지 생성 → 저장
            html = build_html_page(today_label, today_str, items, nutrition,
                                   recent, dinner_rec, image_b64)
            page_url = publish_page(html, today_str)

            # 히스토리 업데이트
            entry = {
                "date": today_str,
                "day": today_label,
                "items": items,
                "nutrition": nutrition,
                "page_url": page_url,
            }
            history = upsert_history(history, entry)
            save_history(history)
            git_commit_history()  # 페이지 push가 링크 전송보다 먼저 끝나야 함

            # Pages 배포 대기(최선) 후 링크 카드 전송
            page_url = wait_until_public(page_url)
            body, actions = build_link_card(page_url)
            send_card(body, actions)
            print("전송 완료:", page_url)

        except Exception as e:
            menu = "\n".join(f"- {m}" for m in items)
            send_text(
                f"{get_lunch_time_msg()}\n\n🍱 오늘({today_label}) 점심\n{menu}\n\n(분석 실패: {e})"
            )
            print("분석 실패, 메뉴만 전송:", e)
