import os
import json
import time
import requests
import xmltodict
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime
from prompt_template import SYSTEM_PROMPT

# --- 1. 환경 변수 및 설정 ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LAW_API_KEY = os.getenv("LAW_API_KEY")
WORKNET_API_KEY = os.getenv("WORKNET_API_KEY")
GCP_JSON_STR = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

genai.configure(api_key=GEMINI_API_KEY)

# --- 2. 구글 시트 연동 (로컬 CSV 대체) ---
def get_gspread_client():
    creds_dict = json.loads(GCP_JSON_STR)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def update_google_sheet(sheet_client, data_list):
    doc = sheet_client.by_url(SHEET_URL)
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 총괄현황표 로깅
    try:
        summary_sheet = doc.worksheet("총괄현황표")
        summary_sheet.append_row([today_str, len(data_list), "모니터링 성공", "AI+워크넷 매쉬업 완료"])
    except Exception as e:
        print(f"총괄현황표 시트 오류 (시트가 존재하는지 확인): {e}")

    # 2) Master DB 데이터 업데이트
    try:
        master_sheet = doc.worksheet("국가기술자격 관련법령")
        for item in data_list:
            row = [
                today_str,
                item.get("unique_key", ""),
                item.get("law_name", ""),
                item.get("provision", ""),
                ", ".join(item.get("related_qualifications", [])),
                item.get("preference_type", ""),
                item.get("sapa_target", ""),
                item.get("impact_level", ""),
                item.get("insight", ""),
                item.get("worknet_job_count", "0") # 워크넷 일자리 수요
            ]
            master_sheet.append_row(row)
    except Exception as e:
        print(f"Master DB 시트 오류: {e}")

# --- 3. 법제처 API 수집 ---
def fetch_today_laws():
    """당일 제/개정된 법령 XML을 호출합니다."""
    # TODO: 법제처 API 스펙에 맞춘 상세 쿼리 조건 추가 가능
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target=law&type=XML"
    try:
        res = requests.get(url)
        # 로직 생략: 실제 환경에서는 XML에서 개정 내용 추출
        # 여기서는 테스트용 Mockup 데이터를 전달합니다.
        raw_text = xmltodict.parse(res.text)
        return str(raw_text)[:3000] # 토큰 제한 방지용 슬라이싱
    except Exception as e:
        print(f"법제처 API 호출 실패: {e}")
        return ""

# --- 4. 워크넷(고용24) API 매쉬업 (일자리 수요 확인) ---
def fetch_worknet_jobs(keyword):
    """자격증명을 키워드로 현재 등록된 고용24 채용공고 수를 파악합니다."""
    url = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
    params = {
        "authKey": WORKNET_API_KEY,
        "callTp": "L",
        "returnType": "XML",
        "startPage": "1",
        "display": "10",
        "keyword": keyword
    }
    try:
        res = requests.get(url, params=params)
        xml_data = xmltodict.parse(res.text)
        total_jobs = xml_data.get('wantedRoot', {}).get('total', 0)
        return int(total_jobs)
    except Exception as e:
        print(f"Worknet API 에러: {e}")
        return 0

# --- 5. Gemini AI 분석 (JSON 모드) ---
def analyze_with_gemini(law_data_text):
    if not law_data_text:
        return []
    
    # AI 챌린지에 적합한 고성능 Pro 모델 사용
    model = genai.GenerativeModel('gemini-2.5-pro')
    prompt = SYSTEM_PROMPT + f"\n\n[금일 수집된 법령 데이터]\n{law_data_text}"
    
    # 응답을 반드시 JSON 형식으로 반환하도록 설정 (응답 빈값 및 파싱 에러 완벽 해결)
    response = model.generate_content(
        prompt,
        generation_config={"response_mime_type": "application/json"}
    )
    
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        print("JSON 파싱 에러 발생.")
        return []

# --- 6. 메인 파이프라인 ---
def main():
    print("1. [법제처] 금일 제/개정 법령 API 수집 중...")
    law_text = fetch_today_laws()
    
    print("2. [Gemini] 법령 분석 및 자격증 파급력(Ticketing Intensity) 도출 중...")
    analyzed_data = analyze_with_gemini(law_text)
    
    if not analyzed_data:
        print("분석된 유의미한 국가기술자격 연관 법령이 없습니다.")
        return

    print("3. [고용24] 워크넷 API 3자 매쉬업 (실시간 구인 수요 확인)...")
    for item in analyzed_data:
        qualifications = item.get("related_qualifications", [])
        job_count = 0
        if qualifications:
            # 추출된 자격증 중 첫 번째(대표) 자격증으로 워크넷 검색
            job_count = fetch_worknet_jobs(qualifications[0])
        item["worknet_job_count"] = f"{job_count}건"
        print(f" - 추출 자격증: {qualifications[0]} -> 실시간 채용 공고: {job_count}건")
        
    print("4. [Google Sheets] 마스터 DB 업데이트 중...")
    client = get_gspread_client()
    update_google_sheet(client, analyzed_data)
    
    print("✅ AI 챌린지 법령 모니터링 자동화 프로세스 완료!")

if __name__ == "__main__":
    main()
