import os
from datetime import datetime, timedelta, timezone


# ==========================================
# 1. API 키 및 외부 연동 설정
# ==========================================
LAW_API_KEY = os.environ.get("LAW_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WEBHOOK_URL = "https://hook.eu1.make.com/okarw4rcy9yusgxj44ogornxbdj8r51u"

# [V27 신규] 구글 시트 직접 제어용 환경 변수
GCP_SERVICE_ACCOUNT_JSON = os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
GOOGLE_SHEET_URL = os.environ.get("GOOGLE_SHEET_URL")
WORKNET_API_KEY = os.environ.get("WORKNET_API_KEY")

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
# 💡 TARGET_DATE = yesterday.strftime("%Y%m%d") # 💡 오전 5시에 돌면 어제 법령 전체를 다 가져옵니다!
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
    "Track1_취급유형", # 🌟 신설: A~E 유형
    "Track1_위험도",   # 🌟 신설: C, H, M, L, N
    "Track2_효용코드", # 🌟 신설: Ⅰ-1 ~ Ⅳ-0
    "상세 분석결과", 
    "근거 조문", 
    "AI 신뢰도", 
    "검토 필요", 
    "검토 사유", 
    "조문별 다이렉트 링크"
    "워크넷_실시간_구인건수" # 🌟 [신설] 맨 마지막 17번째 컬럼(Q열)으로 추가!
]

# ==========================================
# 3. 공단 전용 491개 자격 종목 사전
# ==========================================
QUALIFICATION_CSV_PATH = "26년 국가기술자격 종목.csv"
