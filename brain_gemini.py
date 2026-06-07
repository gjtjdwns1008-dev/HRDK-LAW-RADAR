import json
import re
import time
from google import genai
from google.genai import types

from config import GEMINI_API_KEY

# 🚨 [V29 최신 방식] 옛날 방식인 genai.configure는 완전히 사라졌습니다!
client = genai.Client(api_key=GEMINI_API_KEY)

# 🌟🌟🌟 [추가된 부분 1] 링크 조립 공장 (RESTful 포맷 생성기) 🌟🌟🌟
def generate_new_law_link(law_name, enforce_date, prom_num, prom_date, article_name):
    """별표/서식인지 일반 조항인지 구분해서 법제처 RESTful 링크를 완성합니다."""
    star_match = re.search(r'(별표|서식)\s*(\d+)', article_name)
    if star_match:
        target_id = f"{star_match.group(1)}{star_match.group(2)}" # 예: 별표2
        return f"https://www.law.go.kr/법령별표서식/({law_name},{enforce_date},{target_id})"
    
    jo_match = re.search(r'(제\d+조(?:의\d+)?)', article_name)
    if jo_match:
        target_id = jo_match.group(1) # 예: 제5조
        return f"https://www.law.go.kr/법령/{law_name}/({enforce_date},{prom_num},{prom_date})/{target_id}"
    
    # 조문 매칭 실패 시 그냥 기본 법령 링크로 보냄
    return f"https://www.law.go.kr/법령/{law_name}"


def run_ai_analysis(law, qnet_certs_text, attempt_count=5):
    # ==========================================
    # 🌟 [개편된 프롬프트] Track 1(정책) & Track 2(국민) 투트랙 입체 분석 모드
    # ==========================================
    system_prompt = f"""
    당신은 한국산업인력공단(HRDK)의 '국가기술자격 법령 모니터링 시스템(LAW-RADAR)'을 담당하는 수석 연구원(AI)입니다.
    당신의 임무는 매일 수집되는 제·개정 법령 조문을 분석하여, 해당 법령이 국가기술자격에 미치는 영향을 두 가지 독립적인 트랙(Track)으로 완벽하게 분류하고 정형화된 JSON 형태로 출력하는 것입니다.

    입력되는 법령이 다음 491개 국가기술자격 종목 중 어느 것과 연관되는지 파악하십시오.
    [국가기술자격 종목 리스트]
    {qnet_certs_text}   # <-- 밖에서 받아온 텍스트가 여기에 쏙 들어갑니다!

    ### 🎯 [핵심 분류 기준 (반드시 숙지)]

    #### Track 1. 정책 담당자 관점 : 「경력이음형 자격제도」와의 정합성 (정책 연계)
    해당 조문이 국가기술자격을 어떻게 취급(요구)하는지 분석하여 '모순 강도'를 도출하세요.
    * 1차 축 (법령의 취급):
      - A. 신분형성형 (예: 자격 취득자만 특정 명칭/신분 사용)
      - B. 영업요건형 (예: 기업이 사업을 등록/지정받기 위해 자격자 고용)
      - C. 직역독점형 (예: 특정 업무/행위는 자격자만 수행 가능)
      - D. 인사가산형 (예: 채용, 보수, 승진 시 가점 부여)
      - E. 검정연계형 (예: 타 시험 응시자격 부여 또는 과목 면제)
    * 2차 축 (위험도 - 선경력을 요구하는 경력이음형과 충돌하는 정도):
      - C (임계위험): 오직 단일 자격만 인정하고 대체 경로가 전혀 없음. (경력이음 적용 시 치명적 모순 발생)
      - H (고위험): '자격 + 경력 N년'을 동시에 요구함. (자격이 없으면 경력 시작 불가)
      - M (중위험): 복수의 자격을 OR 조건으로 대체 가능함 (타 자격으로 회피 가능).
      - L (저위험): 자격이 없어도 '관련 학과 졸업 + 경력' 등으로 진입 우회 가능.
      - N (무관): 직역 진입 자체를 막지 않는 단순 부가우대 (D, E 유형).

    #### Track 2. 국민(구직자) 관점 : 노동시장 효용 (대국민 알림용)
    구직자 입장에서 "이 자격증을 따면 취업에 어떤 구체적인 이득이 있는가?"를 11개 세부 유형으로 분류하세요.
    * Ⅰ 직업창출형: Ⅰ-1(면허전환형), Ⅰ-2(개업창업형)
    * Ⅱ 취업관문형: Ⅱ-1(등록필수형), Ⅱ-2(지정인력형), Ⅱ-3(전속배치형), Ⅱ-4(선택배치형), Ⅱ-5(현장배치형)
    * Ⅲ 부가우대형: Ⅲ-1(시험면제형), Ⅲ-2(보수수당우대형), Ⅲ-3(채용승진가점형), Ⅲ-4(위원회위촉형)
    * Ⅳ 제외: Ⅳ-0 (단순 중복, 삭제, 직접 관련 없음)

    ---
    ### 📤 [출력 JSON 포맷 (Strict Rule)]
    반드시 아래 JSON 형식만을 출력해야 하며, 설명이나 마크다운 백틱(```json)을 포함하지 마십시오.

    {{
      "연관성_판별": "연관높음" | "단순관련" | "해당없음",
      "종목": "관련된 자격 종목명 (쉼표로 구분, 해당하는 모든 자격 기재. 없으면 '없음')",
      "요약": "조문의 핵심 내용을 3문장 이내로 요약 (구직자 친화적 톤)",
      "Track1_취급유형": "A" | "B" | "C" | "D" | "E" | "N/A",
      "Track1_위험도": "C" | "H" | "M" | "L" | "N" | "N/A",
      "Track2_효용코드": "Ⅰ-1" | "Ⅰ-2" | "Ⅱ-1" | "Ⅱ-2" | "Ⅱ-3" | "Ⅱ-4" | "Ⅱ-5" | "Ⅲ-1" | "Ⅲ-2" | "Ⅲ-3" | "Ⅲ-4" | "Ⅳ-0",
      "분석결과_상세": "이 법령이 정책적(경력이음)으로 어떤 모순 위험을 가지며, 구직자에게는 어떤 취업 기회를 여는지 5문장 이내로 상세 분석",
      "AI_신뢰도": "높음" | "보통" | "낮음",
      "검토필요": "O" | "X",
      "검토사유": "만약 판단이 모호하거나 특이사항이 있다면 사유 기재 (없으면 공란)"
    }}
    """

    for attempt in range(attempt_count):
        if attempt > 0:
            print(f"\n    🔄 [재시도 {attempt}/{attempt_count-1}] 구글 서버 다시 찌르는 중... ", end="", flush=True)

        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash', 
                contents=prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=32768, 
                    temperature=0.1 
                )
            )
            
            raw_text = response.text.strip()

            match = re.search(r'```json\s*(.*?)\s*```', raw_text, re.DOTALL | re.IGNORECASE)
            if match:
                json_str = match.group(1)
            else:
                json_str = raw_text.replace("```json", "").replace("```JSON", "").replace("```", "").strip()
            
            json_str = json_str.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            
            try:
                data = json.loads(json_str, strict=False)
            except json.JSONDecodeError as je:
                print(f"\n    🚨 [AI 문법 파괴 발생! 범인 색출 블랙박스 로그]")
                print(f"    >> AI가 뱉은 날것의 텍스트:\n{json_str}\n")
                raise Exception(f"JSON 문법 오류: {je}")

            jomun_list = data.get("조문리스트", [])
            if not jomun_list or not isinstance(jomun_list, list):
                jomun_list = [{"조문명": "내용 확인", "숫자": ""}]
                
            links_str_list = []
            names_str_list = []
            
            # 🌟🌟🌟 [추가된 부분 2] 링크를 조립해서 리스트에 넣는 로직 🌟🌟🌟
            for j in jomun_list:
                j_name = j.get("조문명", "확인불가")
                if "별표" in j_name:
                    j_name = re.sub(r'별표\s*(\d+)', r'별표 \1', j_name)
                
                if j_name == "내용 확인":
                    names_str_list.append("전체 (세부 조문 미지정)")
                    links_str_list.append(f"▶ {law['법령명']}\n{law['링크']}")
                else:
                    names_str_list.append(j_name)
                    
                    # law_api.py에서 주머니에 넣어둔 재료(공포번호 등)를 꺼내서 링크 완성!
                    new_link = generate_new_law_link(
                        law_name=law.get('법령명', ''),
                        enforce_date=law.get('시행일자', ''),
                        prom_num=law.get('공포번호', ''),
                        prom_date=law.get('공포일자', ''),
                        article_name=j_name
                    )
                    links_str_list.append(f"▶ {law['법령명']} {j_name}\n{new_link}")
            # 🌟🌟🌟 (여기까지 변경됨) 🌟🌟🌟
                
            links_str = "\n\n".join(links_str_list)
            names_str = ", ".join(names_str_list)
            
            # ==========================================
            # 🌟 [개편된 출력부] 투트랙 매트릭스 결과를 담아내는 새 딕셔너리
            # ==========================================
            law_info = {
                "시행일자": law["시행일자"],
                "소관부처": law.get("소관부처", ""),  # <-- 스크래퍼가 챙겨온 부처명 꽂아넣기!
                "법령명": law["법령명"],
                "연관성_판별": data.get("연관성_판별", "해당없음"),  # (라우팅용 핵심 변수)
                "관련 종목": data.get("종목", ""),
                "조문 요약": data.get("요약", ""),
                "Track1_취급유형": data.get("Track1_취급유형", ""), # (A~E)
                "Track1_위험도": data.get("Track1_위험도", ""),     # (C, H, M, L, N)
                "Track2_효용코드": data.get("Track2_효용코드", ""), # (Ⅰ-1 ~ Ⅳ-0)
                "상세 분석결과": data.get("분석결과_상세", ""),
                "근거 조문": names_str,
                "AI 신뢰도": data.get("AI_신뢰도", ""),
                "검토 필요": data.get("검토필요", "X"),
                "검토 사유": data.get("검토사유", ""),
                "조문별 다이렉트 링크": links_str
            }
            
            # 🌟 리턴 시 '연관성_판별' 값을 넘겨주어 main.py가 어느 시트로 보낼지 결정하게 함
            return True, data.get("연관성_판별", "해당없음"), law_info
            
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "high demand" in error_msg.lower():
                wait_time = 60 * (attempt + 1)
                print(f"\n    🚨 [서버 폭주] {wait_time}초 대기 후 재시도합니다...", end="", flush=True)
            elif "timeout" in error_msg.lower():
                wait_time = 15 * (attempt + 1)
                print(f"\n    🚨 [구글 무응답(Timeout)] {wait_time}초 대기 후 재시도...", end="", flush=True)
            else:
                wait_time = 15 * (attempt + 1)
                print(f"\n    🚨 [기타 에러: {error_msg[:30]}...] {wait_time}초 대기...", end="", flush=True)
                
            time.sleep(wait_time)
            
    return False, "", {"error": error_msg if 'error_msg' in locals() else "재시도 초과"}
