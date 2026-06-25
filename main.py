"""
HRDK LAW-RADAR - main.py (백필 구조판)
--------------------------------------------
🌟 백필(Backfill) 전략:
  법제처 IP 차단으로 며칠 건너뛰어도, 연결되는 날 밀린 날짜를 모두 따라잡습니다.
  - 시작 시 연결 확인 → 안 되면 즉시 종료 (재시도로 시간 낭비 안 함)
  - 마지막 성공일+1 ~ 어제까지 과거→현재 순으로 처리
구조:
  main()           - 1회 초기화(별칭·대장) + 연결확인 + 밀린 날짜 순회
  process_one_day()- 하루치 수집·분석·워크넷·하이브리드·저장·보고
"""

import os
import time
import sys

from config import (
    LAW_API_KEY, WORKNET_API_KEY,
    GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL,
    DB_PATH,
)

from hrdk_law_core.scraper  import get_base_laws
from hrdk_law_core.certs    import get_qnet_certs_text, get_relevant_certs_text, detect_name_change_signal, normalize_cert_string
from hrdk_law_core.worknet  import get_worknet_job_count
from hrdk_law_core.db       import KnowledgeBase
from hrdk_law_core.hybrid   import verify_with_krivet
from hrdk_law_core.backfill import check_law_reachable, pending_dates, mark_done, is_valid_target_date

from brain_gemini   import run_ai_analysis
from report_maker   import (
    upload_to_google_sheet, create_excel_report, send_webhook_with_file,
    export_held_laws_to_sheet, ensure_alias_sheet_exists, read_alias_overrides_from_sheet,
    init_ledger_baseline, apply_cert_rename_to_ledger,
)


def process_one_day(target_date: str, kb, qnet_certs_text: str, run_note: str = "",
                    prefetched_laws: list | None = None) -> bool:
    """하루치 수집·분석·저장·보고. 반환: 성공 여부(수집 실패 시 False).
    run_note: 수동 실행 시 로그에 붙일 접두어 (예: '[수동 6/17 실행] ').
    prefetched_laws: 미리 스크랩해둔 법령 리스트. 주어지면 법제처를 재호출하지 않고
                     이것으로 분석함 (스크랩/분석 분리 모드). None이면 직접 수집."""
    print(f"\n{'='*50}\n📅 [{target_date}] 처리 시작\n{'='*50}")

    if prefetched_laws is not None:
        laws = prefetched_laws
        print(f"  📂 [{target_date}] 저장된 스크랩 사용 ({len(laws)}건) — 법제처 재호출 안 함")
    else:
        laws = get_base_laws(api_key=LAW_API_KEY, target_date=target_date)

    if laws is None:
        print(f"  ❌ [{target_date}] 법제처 수집 실패 (다음 기회에 재시도)")
        return False

    if not laws:
        print(f"  ℹ️ [{target_date}] 시행 법령 없음 (0건)")
        upload_to_google_sheet(
            total_len=0, target_laws=[], target_date=target_date,
            status="🟢 정상 작동 (공포 법령 없음)",
            log=f"{run_note}새로 시행되는 국가 법령이 없습니다.",
        )
        return True

    target_laws, failed_queue, all_results = [], [], []

    print(f"\n🏎️  총 {len(laws)}건 분석 시작...")
    for idx, law in enumerate(laws):
        print(f"  [{idx+1}/{len(laws)}] 🔍 {law['법령명']}")
        t0 = time.time()

        if law.get("스킵여부"):
            hold_reason = law.get("스킵사유", "조직/직제 관련")
            print(f"    ⏩ [보류: {hold_reason}]")
            try:
                kb.add_held_law(
                    law_name=law["법령명"], enforce_date=law.get("시행일자", ""),
                    ministry=law.get("소관부처", ""), hold_reason=hold_reason,
                    law_link=law.get("링크", ""),
                )
            except Exception as he:
                print(f"      ⚠️ 보류 로그 기록 실패: {he}")
            all_results.append({
                "시행일자": law["시행일자"], "법령명": law["법령명"],
                "상세 분석결과": f"AI 분석 보류 ({hold_reason})",
                "연관성_판별": "해당없음", "검토 필요": "X",
                "조문별 다이렉트 링크": law["링크"],
            })
            continue

        success, is_related, law_info = run_ai_analysis(law, get_relevant_certs_text(law.get("원본", "")))
        elapsed = time.time() - t0

        # 🌟 [B 알림] 자격 명칭 변경 의심 감지
        if detect_name_change_signal(law.get("법령명", ""), law.get("원본", "")):
            print(f"    🔔 [명칭변경 의심] '{law['법령명']}' — 변천사 업데이트 검토 필요")
            try:
                kb.add_held_law(
                    law_name=law["법령명"], enforce_date=law.get("시행일자", ""),
                    ministry=law.get("소관부처", ""),
                    hold_reason="⚠️ 자격명칭 변경 의심 — 변천사 자료 업데이트 검토 필요",
                    law_link=law.get("링크", ""),
                )
            except Exception:
                pass

        if success:
            # 종목 사전 정규화: AI 종목을 541종목 사전의 정식명만 남김(범주형/오타 제외)
            if is_related != "해당없음":
                _std, _dropped = normalize_cert_string(law_info.get("관련 종목", ""))
                law_info["관련 종목"] = _std
                if _dropped:
                    _note = f"사전에 없어 제외된 종목 표현: {', '.join(_dropped)}"
                    law_info["검토사유"] = (str(law_info.get("검토사유", "")).strip() + " / " + _note).strip(" /")
                    law_info["검토필요"] = "O"
            if is_related != "해당없음":
                print(f"    📞 워크넷 수요 조회 중... ({law_info.get('관련 종목')})")
                job_demand = get_worknet_job_count(law_info.get("관련 종목", ""), api_key=WORKNET_API_KEY)
                law_info["워크넷 실시간 구인건수"] = job_demand
                law_info = verify_with_krivet(law_info, kb)
                hybrid_tag = {
                    "기준조항_확정": "📌 기준조항", "기준조항_보정": "📌 기준조항(보정)",
                    "직능연_검증": "✅ 직능연", "AI_스마트_보정": "💡 AI보정", "AI_신규판단": "🆕 신규",
                }.get(law_info.get("hybrid_status", ""), "")
                target_laws.append(law_info)
                print(f"    ✅ 관련 법령 ({elapsed:.1f}초) [구인:{job_demand}] [{hybrid_tag}]")
            else:
                law_info["워크넷 실시간 구인건수"] = "-"
                print(f"    ❌ 해당없음 ({elapsed:.1f}초)")
            all_results.append(law_info)
        else:
            law["error_msg"] = law_info.get("error", "알 수 없음")
            failed_queue.append(law)
            print(f"    ⏩ [분석 실패: {law['error_msg']}] ({elapsed:.1f}초)")

    # 패자부활전
    if failed_queue:
        print(f"\n🚑 패자부활전 {len(failed_queue)}건... (20초 대기)")
        time.sleep(20)
        for law in failed_queue:
            print(f"  [재시도] {law['법령명']}... ", end="", flush=True)
            success, is_related, law_info = run_ai_analysis(law, get_relevant_certs_text(law.get("원본", "")), attempt_count=3)
            if success:
                if is_related != "해당없음":
                    _std, _dropped = normalize_cert_string(law_info.get("관련 종목", ""))
                    law_info["관련 종목"] = _std
                    if _dropped:
                        _note = f"사전에 없어 제외된 종목 표현: {', '.join(_dropped)}"
                        law_info["검토사유"] = (str(law_info.get("검토사유", "")).strip() + " / " + _note).strip(" /")
                        law_info["검토필요"] = "O"
                    job_demand = get_worknet_job_count(law_info.get("관련 종목", ""), api_key=WORKNET_API_KEY)
                    law_info["워크넷 실시간 구인건수"] = job_demand
                    law_info = verify_with_krivet(law_info, kb)
                    target_laws.append(law_info)
                    print(f"✅ (구인:{job_demand}) [{law_info.get('hybrid_status','')}]")
                else:
                    law_info["워크넷 실시간 구인건수"] = "-"
                    print("❌ (해당없음)")
                all_results.append(law_info)
            else:
                final_err = law.get("error_msg", "Gemini 크레딧 소진")
                print(f"💀 [최종 실패] {final_err}")
                all_results.append({
                    "시행일자": law["시행일자"], "법령명": law["법령명"],
                    "상세 분석결과": f"AI 분석 최종 실패 (사유: {final_err})",
                    "연관성_판별": "해당없음", "검토필요": "O", "워크넷 실시간 구인건수": "-",
                })

    # SQLite 저장
    if target_laws:
        print(f"\n💾 SQLite 누적 저장... ({len(target_laws)}건)")
        for law_info in target_laws:
            try:
                kb.upsert_daily(law_info)
            except Exception as e:
                print(f"  ⚠️ SQLite 저장 실패 ({law_info.get('법령명', '')}): {e}")

    # 구글 시트 & 보고서
    print("\n📝 구글 시트 적재...")
    ai_fail_count = sum(1 for r in all_results if "AI 분석 최종 실패" in str(r.get("상세 분석결과", "")))
    status_text = "🟡 부분 지연/실패" if ai_fail_count > 0 else "🟢 정상 작동"
    log_text = (
        f"{run_note}총 {len(laws)}건 중 {len(target_laws)}건 매칭. AI실패 {ai_fail_count}건. "
        f"기준조항={sum(1 for r in target_laws if r.get('hybrid_status','').startswith('기준조항'))}건, "
        f"직능연={sum(1 for r in target_laws if r.get('hybrid_status')=='직능연_검증')}건, "
        f"신규={sum(1 for r in target_laws if r.get('hybrid_status')=='AI_신규판단')}건"
    )
    upload_to_google_sheet(len(laws), target_laws, target_date=target_date, status=status_text, log=log_text)

    print("📊 엑셀 보고서 생성...")
    excel_filename = create_excel_report(target_laws)
    print("🚀 웹훅 전송...")
    send_webhook_with_file(excel_filename, len(laws), len(target_laws), 0)

    print("📋 보류목록 시트 반영...")
    export_held_laws_to_sheet(kb)
    return True


def main():
    print("🚀 [HRDK LAW-RADAR] 시작\n" + "=" * 50)
    start_time = time.time()

    kb = KnowledgeBase(DB_PATH)
    print(f"📚 지식베이스 로드 완료 ({DB_PATH})")

    # ── [1회 초기화] 별칭사전 + 명칭변경 양쪽 반영 ────────
    ensure_alias_sheet_exists()
    try:
        from hrdk_law_core.certs import register_alias_overrides
        overrides = read_alias_overrides_from_sheet()
        if overrides:
            register_alias_overrides(overrides)
            print(f"  🔤 담당자 추가 별칭 {len(overrides)}건 반영")
            for old_name, new_name in overrides.items():
                if old_name == new_name:
                    continue
                moved = kb.rename_cert_everywhere(old_name, new_name)
                if moved:
                    print(f"    • SQLite: {old_name} → {new_name} ({moved}건)")
                    apply_cert_rename_to_ledger(old_name, new_name)
    except Exception as e:
        print(f"  ⚠️ 별칭 오버라이드 반영 실패: {e}")

    # ── [1회 초기화] 우대사항 대장 기준선 ─────────────────
    try:
        from hrdk_law_core.certs import resolve_current_name as _resolve
        init_ledger_baseline(kb, resolve_fn=_resolve)
    except Exception as e:
        print(f"  ⚠️ 우대사항 대장 기준선 처리 실패: {e}")

    # ── [수동 실행 모드] 특정 일자만 처리 (연결 확인보다 먼저 — 대상 날짜를 알아야 함) ──
    manual_date = os.environ.get("MANUAL_DATE", "").strip()
    if manual_date:
        from datetime import datetime, timezone, timedelta
        run_day = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        if not is_valid_target_date(manual_date):
            print(f"❌ 잘못된 날짜: '{manual_date}'. YYYYMMDD 형식의 과거(또는 오늘) 날짜여야 합니다.")
            sys.exit(1)
        print(f"🔧 [수동 실행] {manual_date} 한 날짜만 처리합니다. (자동 백필 상태는 변경하지 않음)")
        # 수동 실행도 연결 확인 — 실패 시 시트를 건드리지 않고 종료(기존 🟢 숫자 0 덮어쓰기 방지)
        if not check_law_reachable(LAW_API_KEY):
            print(f"❌ [수동 실행] 법제처 연결 불가. {manual_date} 처리 실패. (시트는 변경하지 않음)")
            print("   → 연결되는 날 다시 실행하세요. 기존 분석 결과는 보존됩니다.")
            sys.exit(1)
        qnet_certs_text = get_qnet_certs_text()
        ok = process_one_day(manual_date, kb, qnet_certs_text, run_note="[수동 실행] ")
        # ⚠️ mark_done 호출하지 않음 — 수동 실행이 자동 백필을 꼬이게 하면 안 됨
        print(f"\n🎉 [수동 실행 종료] {manual_date} 처리 {'성공' if ok else '실패'}")
        if not ok:
            sys.exit(1)
        return

    # ── 1. 오늘이 '되는 날'인지 확인 (자동 실행) ──────────
    if not check_law_reachable(LAW_API_KEY):
        print("❌ 법제처 연결 불가 (오늘은 IP 차단일). 재시도 없이 종료합니다.")
        print("   → 밀린 날짜는 연결되는 다음 날 자동으로 따라잡습니다.")
        from datetime import datetime, timezone, timedelta
        # 처리하려던 시행일자(=어제). 자동 실행은 '실행일 −1'.
        target_efyd = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=1)).strftime("%Y%m%d")
        # 🔑 연결 실패 시엔 시트에 0을 쓰지 않는다(기존 🟢 숫자 덮어쓰기 방지).
        #    이미 처리한 날이면 어차피 재처리 불필요, 아직 안 한 날이면 다음 연결일에 백필됨.
        try:
            from hrdk_law_core.sheets import read_last_success_date
            last_ok = read_last_success_date(GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL)
            if last_ok and last_ok >= target_efyd:
                print(f"ℹ️ {target_efyd}는 이미 처리 완료(마지막 성공일 {last_ok}).")
        except Exception as e:
            print(f"ℹ️ 마지막 성공일 확인 불가({str(e)[:40]}) — 시트는 건드리지 않고 종료.")
        print("   → 밀린 날짜는 연결되는 다음 날 자동으로 따라잡습니다. (총괄현황표 변경 없음)")
        sys.exit(0)
    print("✅ 법제처 연결 확인됨. 처리 시작.")

    # ── 2. 밀린 날짜 계산 ─────────────────────────────────
    # SQLite는 GitHub Actions에서 휘발되므로, 영구 저장소인 구글시트(총괄현황표)에서
    # 마지막 성공일을 읽어 이미 처리한 날짜를 다시 분석하지 않도록 함.
    last_ok = ""
    try:
        from hrdk_law_core.sheets import read_last_success_date
        last_ok = read_last_success_date(GCP_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_URL)
        if last_ok:
            print(f"📌 시트 기준 마지막 성공일: {last_ok}")
    except Exception as e:
        print(f"  ⚠️ 시트에서 마지막 성공일 읽기 실패(무시하고 진행): {e}")
    dates = pending_dates(kb, last_success_override=last_ok or None)
    if not dates:
        print("ℹ️ 처리할 밀린 날짜가 없습니다 (이미 최신).")
        return
    print(f"📋 처리 대상 {len(dates)}일: {dates[0]} ~ {dates[-1]}")
    if len(dates) > 10:
        print(f"   ⚠️ 밀린 날짜 {len(dates)}일. 순서대로 모두 처리합니다.")

    # ── 3. 종목 텍스트는 1회만 로드 후 재사용 ─────────────
    qnet_certs_text = get_qnet_certs_text()

    # ── 4. 과거→현재 순으로 따라잡기 ──────────────────────
    done, failed = 0, 0
    for d in dates:
        try:
            if process_one_day(d, kb, qnet_certs_text):
                mark_done(kb, d)
                done += 1
            else:
                failed += 1
                print(f"  ⏸️ [{d}] 수집 실패로 백필 중단. 다음 실행에서 이어서 처리합니다.")
                break
        except Exception as e:
            print(f"  💥 [{d}] 처리 중 오류: {e}")
            failed += 1
            break

    elapsed_total = time.time() - start_time
    print(f"\n🎉 [종료] 완료 {done}일 / 실패 {failed}일 (소요: {elapsed_total/60:.1f}분)")
    if failed and not done:
        sys.exit(1)


def scrape_only():
    """[스크랩 모드] 법제처에서 밀린 날짜를 수집해 디스크에 JSON 저장만 한다.
    AI 분석은 하지 않음. 00~06시에 자주 돌려 '한 번 받으면 안 사라지게' 함."""
    from hrdk_law_core.scrape_store import is_scraped, save_scraped
    print("🛰️ [스크랩 모드] 법제처 수집 → 디스크 저장\n" + "=" * 50)

    kb = KnowledgeBase(DB_PATH)

    # 연결 확인 (막혔으면 조용히 종료 — 다음 스케줄에 재시도)
    if not check_law_reachable(LAW_API_KEY):
        print("❌ 법제처 연결 불가 (IP 차단일 추정). 다음 스케줄에 재시도.")
        sys.exit(0)  # 실패가 아니라 '아직 안 됨' — exit 0
    print("✅ 법제처 연결 확인됨.")

    # 분석 대기 중인 날짜 = 백필 대상 (마지막 성공일+1 ~ 어제)
    dates = pending_dates(kb)
    if not dates:
        print("ℹ️ 스크랩할 밀린 날짜가 없습니다.")
        return

    print(f"📋 스크랩 대상 {len(dates)}일: {dates[0]} ~ {dates[-1]}")
    scraped, skipped, failed = 0, 0, 0
    for d in dates:
        if is_scraped(d):
            print(f"  ⏭️ [{d}] 이미 스크랩됨 (건너뜀)")
            skipped += 1
            continue
        laws = get_base_laws(api_key=LAW_API_KEY, target_date=d)
        if laws is None:
            print(f"  ❌ [{d}] 수집 실패 — 다음 스케줄에 재시도")
            failed += 1
            break  # 연결이 끊긴 것일 수 있으니 중단
        save_scraped(d, laws)
        print(f"  💾 [{d}] 스크랩 저장 완료 ({len(laws)}건)")
        scraped += 1

    print(f"\n🎉 [스크랩 종료] 신규 {scraped}일 / 기존 {skipped}일 / 실패 {failed}일")


def analyze_only():
    """[분석 모드] 디스크에 저장된 스크랩을 읽어 AI 분석·저장. 법제처 재호출 없음.
    06시 이후 1회 실행. 제미나이 재시도 로직(llm_client)이 일시 장애를 흡수함."""
    from hrdk_law_core.scrape_store import load_scraped, is_scraped
    print("🧠 [분석 모드] 저장된 스크랩 → AI 분석\n" + "=" * 50)

    kb = KnowledgeBase(DB_PATH)
    print(f"📚 지식베이스 로드 완료 ({DB_PATH})")

    # 1회 초기화 (별칭·대장) — 분석 모드에서도 필요
    ensure_alias_sheet_exists()
    try:
        from hrdk_law_core.certs import register_alias_overrides
        overrides = read_alias_overrides_from_sheet()
        if overrides:
            register_alias_overrides(overrides)
            for old_name, new_name in overrides.items():
                if old_name == new_name:
                    continue
                moved = kb.rename_cert_everywhere(old_name, new_name)
                if moved:
                    apply_cert_rename_to_ledger(old_name, new_name)
    except Exception as e:
        print(f"  ⚠️ 별칭 오버라이드 반영 실패: {e}")
    try:
        from hrdk_law_core.certs import resolve_current_name as _resolve
        init_ledger_baseline(kb, resolve_fn=_resolve)
    except Exception as e:
        print(f"  ⚠️ 우대사항 대장 기준선 처리 실패: {e}")

    # 분석할 날짜 = 백필 대상 중 '스크랩이 저장된' 날짜만
    dates = pending_dates(kb)
    if not dates:
        print("ℹ️ 분석할 밀린 날짜가 없습니다.")
        return

    qnet_certs_text = get_qnet_certs_text()
    done, failed, no_data = 0, 0, 0
    for d in dates:
        laws = load_scraped(d)
        if laws is None:
            print(f"  ⏳ [{d}] 스크랩 데이터 없음 — 아직 수집 안 됨 (백필 미완료, 중단)")
            no_data += 1
            break  # 순서대로 처리해야 하므로, 스크랩 안 된 날에서 멈춤
        try:
            if process_one_day(d, kb, qnet_certs_text, prefetched_laws=laws):
                mark_done(kb, d)
                done += 1
            else:
                failed += 1
                print(f"  ⏸️ [{d}] 분석 실패. 다음 실행에서 재시도.")
                break
        except Exception as e:
            print(f"  💥 [{d}] 분석 중 오류: {e}")
            failed += 1
            break

    print(f"\n🎉 [분석 종료] 완료 {done}일 / 실패 {failed}일 / 미수집 {no_data}일")


if __name__ == "__main__":
    run_mode = os.environ.get("RUN_MODE", "").strip().lower()
    if run_mode == "scrape":
        scrape_only()
    elif run_mode == "analyze":
        analyze_only()
    else:
        # 기본: 기존 통합 동작 (수집+분석 한 번에) — 수동 실행·호환용
        main()
