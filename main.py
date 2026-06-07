import time
from config import TARGET_DATE
from law_scrapper import get_base_laws
from brain_gemini import run_ai_analysis
from report_maker import upload_to_google_sheet, create_excel_report, send_webhook_with_file

def main():
    print(f"🚀 [HRDK LAW-RADAR] {TARGET_DATE} 데이터 수집 및 분석 시작...\n" + "="*50)
    start_time = time.time()

# ==========================================
    # 1. 법령 수집 (프리필터링 포함)
    # ==========================================
    laws = get_base_laws()
    if not laws:
        print(f"  ℹ️ {TARGET_DATE} 시행되는 법령이 없습니다. (0건 기록 및 빈 리포트 전송)")
        
        # 🌟 [해결 1] 구글 시트에 '0건'을 명시적으로 기록합니다.
        upload_to_google_sheet(0, [], [])
        
        # 🌟 [해결 2] 0건이라도 '빈 엑셀 파일'을 생성하여 Make.com 에러를 방지합니다.
        empty_excel = create_excel_report([], [])
        
        # 🌟 [해결 3] 빈 파일과 함께 웹훅을 쏩니다.
        send_webhook_with_file(empty_excel, 0, 0, 0)
        
        return # 이제 시트 기록과 파일 전송을 마쳤으므로 종료해도 안전합니다.

    high_impact_laws, simple_related_laws, failed_queue = [], [], []
    all_results_for_sheet = [] # 구글 시트에 넣을 전체 마스터 데이터 모음

    # ==========================================
    # 2. AI 정밀 분석 루프
    # ==========================================
    print(f"\n🏎️  총 {len(laws)}건 분석 시작 (직제/조직 법령은 0.1초 컷으로 패스합니다)...")
    for idx, law in enumerate(laws):
        
        # 💡 [핵심 수정] end="" 를 빼서 무조건 화면에 즉시 글자가 뜨게 만듭니다!
        print(f"  [{idx+1}/{len(laws)}] 🔍 {law['법령명']} (제미나이 서버로 전송... 응답 대기중!)")
        
        start_time = time.time()

        if law.get("스킵여부") == True:
            print("    ⏩ [스킵: 조직/직제 관련]")
            skip_info = {
                "시행일자": law["시행일자"], "법령명": law["법령명"], 
                "주요 제·개정내용": "조직/직제 관련 법령으로 AI 분석 생략", 
                "활용도 분석 구분": "일반", "검토 필요": "X", "조문별 다이렉트 링크": law["링크"]
            }
            all_results_for_sheet.append(skip_info)
            continue

        success, cat, law_info = run_ai_analysis(law)
        
        elapsed = time.time() - start_time
        
        if success:
            if cat == "연관높음": high_impact_laws.append(law_info); print(f"    🔥 연관높음 ({elapsed:.1f}초)")
            elif cat == "단순관련": simple_related_laws.append(law_info); print(f"    🟡 단순관련 ({elapsed:.1f}초)")
            else: print(f"    ❌ 일반 ({elapsed:.1f}초)")
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
            success, cat, law_info = run_ai_analysis(law, attempt_count=3)
            if success:
                if cat == "연관높음": high_impact_laws.append(law_info); print("🔥")
                elif cat == "단순관련": simple_related_laws.append(law_info); print("🟡")
                else: print("❌ (일반)")
                all_results_for_sheet.append(law_info)
            else:
                print("💀 [최종 실패]")
                fail_info = {"시행일자": law["시행일자"], "법령명": law["법령명"], "주요 제·개정내용": "AI 분석 최종 실패", "활용도 분석 구분": "일반", "검토 필요": "X"}
                all_results_for_sheet.append(fail_info)

    # ==========================================
    # 4. 보고서 작성 및 발송
    # ==========================================
    print("\n📝 구글 시트 마스터 DB 적재 시작...")
    # 🌟 [수정] 업그레이드된 방식에 맞춰 3개(총 건수, 연관높음 리스트, 단순관련 리스트)를 전달합니다!
    upload_to_google_sheet(len(laws), high_impact_laws, simple_related_laws)

    print("\n📊 보고용 엑셀 파일 생성 중...")
    excel_filename = create_excel_report(high_impact_laws, simple_related_laws)

    print("\n🚀 Make.com 웹훅 전송 시작...")
    send_webhook_with_file(excel_filename, len(laws), len(high_impact_laws), len(simple_related_laws))

    elapsed_time = time.time() - start_time
    print(f"\n🎉 [종료] 모든 작업이 완벽하게 완료되었습니다! (소요 시간: {elapsed_time/60:.1f}분)")

if __name__ == "__main__":
    main()
