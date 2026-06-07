import os  # 🌟 신설: 파일 존재 여부 확인을 위해 추가
import time
from config import TARGET_DATE, QUALIFICATION_CSV_PATH  # 🌟 신설: CSV 파일 경로 변수 가져오기
from law_scrapper import get_base_laws
from brain_gemini import run_ai_analysis
from report_maker import upload_to_google_sheet, create_excel_report, send_webhook_with_file

# ==========================================
# 🌟 신설: CSV 파일을 안전하게 읽어오는 함수
# ==========================================
def load_qualification_list(csv_path):
    if not os.path.exists(csv_path):
        print(f"⚠️ 경고: {csv_path} 파일을 찾을 수 없습니다. 빈 문자열로 대체합니다.")
        return ""
    
    # 강력한 인코딩 에러 방어 로직 (utf-8 실패 시 cp949로 재시도)
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        with open(csv_path, 'r', encoding='cp949') as f:
            return f.read()

def main():
    print(f"🚀 [HRDK LAW-RADAR] {TARGET_DATE} 데이터 수집 및 분석 시작...\n" + "="*50)
    start_time = time.time()

    # 🌟 신설: AI에게 먹일 '국가기술자격 종목 리스트'를 CSV에서 텍스트로 미리 읽어옵니다.
    qnet_certs_text = load_qualification_list(QUALIFICATION_CSV_PATH)

    # ==========================================
    # 1. 법령 수집 (프리필터링 포함)
    # ==========================================
    laws = get_base_laws()
    if not laws:
        print(f"  ℹ️ {TARGET_DATE} 시행되는 법령이 없습니다. (0건 기록 및 빈 리포트 전송)")
        
        # 구글 시트 적재 함수 호출 (통합 바구니 1개 전달)
        upload_to_google_sheet(0, [])
        empty_excel = create_excel_report([])
        
        # webhook 함수는 원래 (전체, high, simple) 3개를 받도록 되어 있으므로 simple 자리에 0을 넣습니다.
        send_webhook_with_file(empty_excel, 0, 0, 0)
        
        return # 종료

    # 연관/단순 구분을 없애고 'target_laws' 하나로 통합!
    target_laws = [] 
    failed_queue = []
    all_results_for_sheet = [] # 구글 시트에 넣을 전체 마스터 데이터 모음

    # ==========================================
    # 2. AI 정밀 분석 루프
    # ==========================================
    print(f"\n🏎️  총 {len(laws)}건 분석 시작 (직제/조직 법령은 0.1초 컷으로 패스합니다)...")
    for idx, law in enumerate(laws):
        
        # 담당자님 수정 로직 완벽 반영 (로딩 즉시 송출)
        print(f"  [{idx+1}/{len(laws)}] 🔍 {law['법령명']} (제미나이 서버로 전송... 응답 대기중!)")
        
        start_time_loop = time.time()

        if law.get("스킵여부") == True:
            print("    ⏩ [스킵: 조직/직제 관련]")
            # 새로운 COLUMNS(이름표)에 맞춰 딕셔너리 키 이름 변경
            skip_info = {
                "시행일자": law["시행일자"], "법령명": law["법령명"], 
                "상세 분석결과": "조직/직제 관련 법령으로 AI 분석 생략", 
                "연관성_판별": "해당없음", "검토 필요": "X", "조문별 다이렉트 링크": law["링크"]
            }
            all_results_for_sheet.append(skip_info)
            continue

        # 🌟 핵심 수정: AI 두뇌 호출 시 읽어온 종목 리스트(qnet_certs_text)를 같이 던져줍니다!
        success, is_related, law_info = run_ai_analysis(law, qnet_certs_text)
        
        elapsed = time.time() - start_time_loop
        
        if success:
            # "해당없음"만 아니면 몽땅 target_laws (관련법령) 바구니에 담습니다.
            if is_related != "해당없음": 
                target_laws.append(law_info)
                print(f"    ✅ 관련 법령 식별 ({elapsed:.1f}초)")
            else: 
                print(f"    ❌ 해당없음 ({elapsed:.1f}초)")
            
            all_results_for_sheet.append(law_info)
        else:
            failed_queue.append(law)
            print(f"    ⏩ [분석 실패: {law_info.get('error', '알 수 없음')}] ({elapsed:.1f}초)")

    # ==========================================
    # 3. 패자부활전 (에러 났던 법령들 재시도)
    # ==========================================
    if failed_queue:
        print(f"\n🚑 패자부활전 {len(failed_queue)}건 시작... (서버 안정을 위해 20초 대기)")
        time.sleep(20)
        for law in failed_queue:
            print(f"  [재시도] {law['법령명']}... ", end="", flush=True)
            
            # 🌟 핵심 수정: 재시도할 때도 종목 리스트(qnet_certs_text)를 같이 던져줍니다!
            success, is_related, law_info = run_ai_analysis(law, qnet_certs_text, attempt_count=3)
            
            if success:
                if is_related != "해당없음": 
                    target_laws.append(law_info)
                    print("✅ (관련 법령 식별)")
                else: 
                    print("❌ (해당없음)")
                all_results_for_sheet.append(law_info)
            else:
                print("💀 [최종 실패]")
                # 실패 시에도 새로운 COLUMNS에 맞춰서 구조화
                fail_info = {"시행일자": law["시행일자"], "법령명": law["법령명"], "상세 분석결과": "AI 분석 최종 실패", "연관성_판별": "해당없음", "검토 필요": "X"}
                all_results_for_sheet.append(fail_info)

    # ==========================================
    # 4. 보고서 작성 및 발송
    # ==========================================
    print("\n📝 구글 시트 마스터 DB 적재 시작...")
    # 함수에 넘겨주는 바구니를 'target_laws' 하나로 통일!
    upload_to_google_sheet(len(laws), target_laws)

    print("\n📊 보고용 엑셀 파일 생성 중...")
    excel_filename = create_excel_report(target_laws)

    print("\n🚀 Make.com 웹훅 전송 시작...")
    # send_webhook_with_file 함수는 건수 3개를 받으므로 맨 마지막(단순관련 자리)에 0을 넣어주면 에러 없이 완벽히 동작합니다!
    send_webhook_with_file(excel_filename, len(laws), len(target_laws), 0)

    elapsed_time = time.time() - start_time
    print(f"\n🎉 [종료] 모든 작업이 완벽하게 완료되었습니다! (소요 시간: {elapsed_time/60:.1f}분)")

if __name__ == "__main__":
    main()
