import os
import json
import csv
import time
import requests
import xmltodict
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import google.generativeai as genai
from datetime import datetime, timedelta
from prompt_template import SYSTEM_PROMPT

# ==========================================
# 1. 환경 변수 및 API 키 설정 (GitHub Secrets 연동)
# ==========================================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LAW_API_KEY = os.getenv("LAW_API_KEY")
WORKNET_API_KEY = os.getenv("WORKNET_API_KEY")
GCP_JSON_STR = os.getenv("GCP_SERVICE_ACCOUNT_JSON")
SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

genai.configure(api_key=GEMINI_API_KEY)

# ==========================================
# 2. [오피셜 데이터 로드] 직능연 분석 기준 (하이브리드 전략 & 조문 분리)
# ==========================================
def load_reference_csv(file_path):
    """
    엑셀이 어떤 인코딩으로 저장하든 자동으로 맞춰서 읽어오는 강력한 함수
    """
    # 파이썬이 시도해 볼 인코딩 후보들 (UTF-8 먼저, 안되면 한국어 윈도우용 cp949)
    encodings_to_try = ['utf-8', 'utf-8-sig', 'cp949', 'euc-kr']
    
    for encoding in encodings_to_try:
        try:
            with open(file_path, mode='r', encoding=encoding) as f:
                reader = csv.DictReader(f)
                data = list(reader)
                # 성공하면 로그에 어떤 인코딩으로 성공했는지 출력해줍니다.
                print(f"✅ 성공: '{file_path}' 파일을 {encoding} 방식으로 완벽하게 읽었습니다!")
                return data
        except UnicodeDecodeError:
            # 해당 인코딩으로 실패하면 조용히 다음 후보로 넘어갑니다.
            continue
            
    # 모든 방법을 다 썼는데도 안 될 경우의 안전장치
    raise Exception(f"❌ 치명적 에러: '{file_path}' 파일을 읽을 수 없습니다.")

    
    """
    과거 직능연 기준 데이터를 사전에 로드합니다. 
    하나의 셀에 '제27조, 제28조'처럼 뭉쳐있는 조문을 분리하여 각각의 Key로 만듭니다.
    """
    
    ref_dict = {}
    try:
        with open(file_name, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                law_name = row.get('우대법령', '').strip()
                # 🌟 조문내역에 쉼표가 있을 경우 쪼개서 리스트로 만듦
                raw_provisions = row.get('조문내역', '').split(',')
                
                for p in raw_provisions:
                    provision = p.strip()
                    if law_name and provision:
                        # 띄어쓰기 차이로 인한 미스매치를 막기 위해 공백 완전 제거
                        u_key = f"{law_name}_{provision}".replace(" ", "")
                        ref_dict[u_key] = {
                            "preference_type": row.get('우대분류', '').strip(),
                            "sapa_target": row.get('중처법대상', '').strip()
                        }
        print(f"📖 [참고용 CSV 로드 완료] 분리된 조문 기준 총 {len(ref_dict)}개의 오피셜 키 확보")
    except FileNotFoundError:
        print(f"⚠️ [경고] 오피셜 기준 CSV 파일을 찾을 수 없습니다: {file_name}")
    return ref_dict

# ==========================================
# 3. [오피셜 데이터 로드] 국가기술자격 491개 종목 리스트 (RAG 주입용)
# ==========================================
def load_qualification_list(file_name="26년 국가기술자격 종목.csv"):
    """
    AI가 존재하지 않는 자격증을 지어내지(환각) 못하도록, 
    정확한 오피셜 종목 리스트를 프롬프트에 주입하기 위해 문자열로 읽어옵니다.
    """
    qual_list = []
    try:
        with open(file_name, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0].strip() != "종목명": # 빈 줄이나 헤더 제외
                    qual_list.append(row[0].strip())
                    
        print(f"📋 [종목 리스트 로드 완료] 총 {len(qual_list)}개의 오피셜 종목명 확보")
        return ", ".join(qual_list)
    except FileNotFoundError:
        print(f"⚠️ [경고] 종목 리스트 CSV 파일을 찾을 수 없습니다: {file_name}")
        return ""

# ==========================================
# 4. MST_ID (고유 마스터 ID) 연도별 스마트 자동 발급기 (6자리 확장)
# ==========================================
def generate_next_mst_id(last_id: str) -> str:
    """연도별 초기화 유지 + 6자리 번호(000001~999999) 발급기"""
    
    # 1. 현재 연도 접두사 생성 (예: HRDK-L-26-)
    current_year = datetime.now().strftime("%y") 
    prefix = f"HRDK-L-{current_year}-"
    
    # 2. 리셋 판단: ID가 비어있거나, 접두사가 올해와 다르면(연도가 바뀌면) 리셋
    if not last_id or last_id.strip() == "" or not str(last_id).startswith(prefix):
        return f"{prefix}000001"
    
    # 3. 올해 ID라면 마지막 번호만 가져와서 +1
    try:
        # HRDK-L-26-000005 형태에서 마지막 6자리(000005)를 분리
        parts = last_id.split("-")
        num_str = parts[-1] 
        next_num = int(num_str) + 1
        
        # 4. 6자리로 맞춰서 반환 (zfill(6) 사용)
        return f"{prefix}{str(next_num).zfill(6)}"
    except (ValueError, IndexError):
        # 예외 발생 시 안전하게 000001부터 재시작
        return f"{prefix}000001"

# ==========================================
# 5. 구글 시트 증분 결합 (Upsert) - MST_ID 유지 로직
# ==========================================
def get_gspread_client():
    creds_dict = json.loads(GCP_JSON_STR)
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

def update_google_sheet(sheet_client, data_list, total_reviewed_count):
    doc = sheet_client.open_by_url(SHEET_URL) 
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1) 총괄현황표 로깅
    try:
        summary_sheet = doc.worksheet("총괄현황표")
        summary_row = [today_str, total_reviewed_count, len(data_list), "정상 완료", "하이브리드(AI+CSV) 및 스마트 MST_ID 매쉬업 완료"]
        summary_sheet.append_row(summary_row)
        print(f"📊 [총괄현황표] 로깅 완료")
    except Exception as e:
        print(f"🚨 총괄현황표 시트 오류: {e}")

    # 2) Master DB 업데이트 (MST_ID 기반)
    try:
        master_sheet = doc.worksheet("국가기술자격 관련법령")
        existing_mst_ids = master_sheet.col_values(1)   # A열: 법령_ID
        existing_unique_keys = master_sheet.col_values(3) # C열: 고유키

        valid_ids = [uid for uid in existing_mst_ids if str(uid).startswith("HRDK-L-")]
        last_mst_id = valid_ids[-1] if valid_ids else ""

        for item in data_list:
            u_key = item.get("unique_key", "")
            
            row_data = [
                "", # A열: (아래 로직에서 MST_ID 할당)
                today_str,           
                u_key,               
                item.get("law_name", ""),
                item.get("provision", ""),
                ", ".join(item.get("related_qualifications", [])),
                item.get("preference_type", ""),
                item.get("sapa_target", ""),
                item.get("impact_level", ""),
                item.get("insight", ""),
                item.get("worknet_job_count", "0")
            ]

            if u_key and u_key in existing_unique_keys:
                # [Update] 기존 법령이면 기존 MST_ID 유지
                row_idx = existing_unique_keys.index(u_key) + 1 
                current_mst_id = existing_mst_ids[row_idx - 1] 
                row_data[0] = current_mst_id 
                
                cell_range = f"A{row_idx}:K{row_idx}"
                master_sheet.update(values=[row_data], range_name=cell_range)
                print(f"🔄 [Update] 기존 법령 갱신 완료 (ID: {current_mst_id})")
            else:
                # [Insert] 신규 법령이면 새로운 MST_ID 발급 (연도별 리셋 적용)
                new_mst_id = generate_next_mst_id(last_mst_id)
                row_data[0] = new_mst_id 
                
                master_sheet.append_row(row_data)
                print(f"🆕 [Insert] 신규 법령 추가 완료 (ID: {new_mst_id})")
                
                last_mst_id = new_mst_id
                existing_unique_keys.append(u_key)
                existing_mst_ids.append(new_mst_id)
                
    except Exception as e:
        print(f"🚨 Master DB 시트 오류: {e}")

# ==========================================
# 6. 법제처 API 수집 (30일 백캐스팅)
# ==========================================
def fetch_recent_laws():
    import re 
    today_dt = datetime.now()
    past_dt = today_dt - timedelta(days=30)
    end_date = today_dt.strftime("%Y%m%d")
    start_date = past_dt.strftime("%Y%m%d")
    
    url = f"https://www.law.go.kr/DRF/lawSearch.do?OC={LAW_API_KEY}&target=law&type=XML&lsTrm={start_date}~{end_date}"
    headers = {"User-Agent": "Mozilla/5.0"}
    
    try:
        res = requests.get(url, headers=headers, timeout=15)
        res.raise_for_status() 
        raw_xml_text = res.text
        
        match = re.search(r'<totalCnt>(\d+)</totalCnt>', raw_xml_text, re.IGNORECASE)
        total_count = int(match.group(1)) if match else raw_xml_text.count("</law>") + raw_xml_text.count("</LAW>")
        raw_dict = xmltodict.parse(raw_xml_text)
        return str(raw_dict)[:3000], total_count # 토큰 최적화를 위해 일부만 슬라이싱
    except Exception as e:
        print(f"🚨 법제처 API 수집 실패: {e}")
        return "", 0

# ==========================================
# 7. 워크넷(고용24) API
# ==========================================
def fetch_worknet_jobs(keyword):
    url = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
    params = {"authKey": WORKNET_API_KEY, "callTp": "L", "returnType": "XML", "startPage": "1", "display": "10", "keyword": keyword}
    try:
        res = requests.get(url, params=params)
        xml_data = xmltodict.parse(res.text)
        return int(xml_data.get('wantedRoot', {}).get('total', 0))
    except:
        return 0

# ==========================================
# 8. Gemini AI 분석 (RAG 종목 주입)
# ==========================================
def analyze_with_gemini(law_data_text, qual_list_str):
    if not law_data_text: return []
    model = genai.GenerativeModel(
        model_name='gemini-3.5-flash',
        generation_config={"temperature": 0.0, "response_mime_type": "application/json"}
    )
    
    # 🌟 프롬프트에 오피셜 종목 리스트를 직접 주입하여 환각 방지
    prompt = SYSTEM_PROMPT + f"\n\n[★필수: 491개 오피셜 국가기술자격 종목 리스트 (이 안에서만 추출하세요)]\n{qual_list_str}\n\n[금일 수집된 법령 데이터]\n{law_data_text}"
    
    response = model.generate_content(prompt)
    try:
        return json.loads(response.text)
    except json.JSONDecodeError:
        print("🚨 AI 출력 결과가 올바른 JSON 형식이 아닙니다.")
        return []

# ==========================================
# 9. 메인 파이프라인 (스마트 크로스 체크 포함)
# ==========================================
def main():
    print("1. [데이터 로드] 직능연 오피셜 파일 및 종목 리스트 로드 중...")
    reference_dict = load_reference_csv("2026년 국가기술자격 우대법령 정리본(중대재해처벌법 포함).csv")
    qual_list_str = load_qualification_list("26년 국가기술자격 종목.csv") 

    print("2. [법제처 API] 최근 제/개정 법령 수집 중...")
    filtered_law_text, total_reviewed_count = fetch_recent_laws() 
    if not filtered_law_text: return
        
    print("3. [Gemini AI] RAG 기반 신규 법령 1차 분석 중...")
    analyzed_data = analyze_with_gemini(filtered_law_text, qual_list_str)
    if not analyzed_data: return

    print("4. [스마트 크로스 체크] AI 분석 결과 vs 직능연 과거 데이터 교차 검증 중...")
    for item in analyzed_data:
        u_key_clean = item.get("unique_key", "").replace(" ", "")
        ai_pref = item.get("preference_type", "")
        
        # 직능연 과거 데이터에 해당 법령_조문이 존재하는지 확인 (부분 일치 허용)
        matched_official_key = next((k for k in reference_dict.keys() if u_key_clean in k or k in u_key_clean), None)
        
        if matched_official_key:
            official_pref = reference_dict[matched_official_key]["preference_type"]
            official_sapa = reference_dict[matched_official_key]["sapa_target"]
            
            # 🌟 핵심: 직능연 과거 기준과 AI의 현재 판단이 다를 경우, AI의 판단을 우선하고 인사이트에 알림 추가
            if official_pref and ai_pref and (official_pref != ai_pref):
                warning_msg = f"💡 [AI 스마트 보정] 기존 직능연 분류({official_pref})와 다르나, 최신 법령 맥락에 따라 ({ai_pref})(으)로 자체 분석했습니다. "
                item["insight"] = warning_msg + item.get("insight", "")
                print(f"   🚨 [보정 알림] {u_key_clean} : 직능연({official_pref}) -> AI({ai_pref})")
            
            # AI가 우대분류를 못 찾았거나 비어있을 경우에만 직능연 오피셜 데이터로 덮어쓰기
            elif not ai_pref and official_pref:
                item["preference_type"] = official_pref
                
            # 중처법 판단이 비어있다면 직능연 데이터를 끌어옴
            if not item.get("sapa_target") and official_sapa:
                item["sapa_target"] = official_sapa

        # 고용24 실시간 구인수요 매쉬업 (가장 첫 번째 자격증 기준)
        qualifications = item.get("related_qualifications", [])
        item["worknet_job_count"] = f"{fetch_worknet_jobs(qualifications[0])}건" if qualifications else "0건"
        
    print("5. [Google Sheets] 마스터 DB 업데이트 (MST_ID 연동 Upsert) 중...")
    client = get_gspread_client()
    update_google_sheet(client, analyzed_data, total_reviewed_count)
    
    print("✅ 타법개정 방어 & 스마트 크로스체크 AI 파이프라인 완벽 가동!")

if __name__ == "__main__":
    main()
