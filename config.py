import os
from datetime import datetime, timedelta, timezone


# ==========================================
# 1. API 키 및 외부 연동 설정
# ==========================================
LAW_API_KEY = os.environ.get("LAW_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# 🛠️ [D-3 패치] 웹훅 주소 하드코딩 제거 → GitHub Secrets(환경변수)에서 읽도록 변경
# ⚠️ 기존 주소는 저장소 이력에 노출되었으므로 Make.com에서 반드시 '재발급' 후 Secrets에 등록하세요.
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
WORKNET_API_KEY = os.environ.get("WORKNET_API_KEY", "")  # 🛠️ [중복 제거] 아래 중복 선언 삭제

# [V27 신규] 구글 시트 직접 제어용 환경 변수
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")

# Phase 1 신규: SQLite 지식베이스 경로
DB_PATH = os.environ.get("DB_PATH", "hrdk_law.db")

# ==========================================
# 2. 날짜 및 공통 변수 설정
# ==========================================
KST = timezone(timedelta(hours=9))
today = datetime.now(KST)

# 🌟 [D-1 로직 적용] 오늘(today)에서 1일을 뺀 어제 날짜를 계산합니다.
yesterday = today - timedelta(days=1)
TARGET_DATE = yesterday.strftime("%Y%m%d")

# 💡 만약 과거 데이터를 돌리고 싶다면 이 변수를 수동으로 바꿔서 쓰면 됩니다.
# 💡 TARGET_DATE = "20260429"
# 💡 TARGET_DATE = yesterday.strftime("%Y%m%d")
# 💡 TARGET_DATE = today.strftime("%Y%m%d")

# ==========================================
# [개편된 COLUMNS] 투트랙 입체 분석용 엑셀 헤더
# ==========================================
COLUMNS = [
    "MST_ID",          # 🌟 신설: 마스터 고유 ID (예: HRDK-L-0001)
    "시행일자",
    "소관부처",        # 🌟 신설: 여기에 소관부처를 추가합니다!
    "법령명",
    "연관성_판별",
    "관련 종목",
    "조문 요약",
    "우대분류",        # 🌟 신설: 직능연 우대분류 (의무고용/직무권한부여/인사우대/시험면제/기타) — 메인 분류
    "Track1_취급유형", # 🌟 신설: A~E 유형
    "Track1_위험도",   # 🌟 신설: C, H, M, L, N
    "Track2_효용코드", # 🌟 신설: Ⅰ-1 ~ Ⅳ-0
    "중처법대상",      # 🌟 신설: 중대재해처벌법 대상 여부 (대상/비대상)
    "상세 분석 결과",
    "근거조문",
    "AI신뢰도",
    "검토필요",
    "검토사유",
    "조문별 다이렉트 링크",  # 🛠️ [D-1 패치] 쉼표 누락 수정!
    "워크넷 실시간 구인건수" # 🌟 [신설] 맨 마지막 17번째 컬럼(Q열)으로 추가!
]

