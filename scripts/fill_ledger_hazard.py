"""
scripts/fill_ledger_hazard.py
------------------------------
[일회성] 우대사항_대장의 '중처법대상' 칸을 채웁니다.

배경:
  대장에 중처법대상 칸을 추가했으나, 기존 419행은 비어 있음.
  init_ledger_baseline은 '이미 데이터 있으면 건너뜀'이라 자동으로 안 채워짐.
  이 스크립트가 (법령명+조문) 매칭으로 중처법대상 칸만 채움 (기존 행/메모 보존).

실행: python scripts/fill_ledger_hazard.py
  (GitHub Actions에서는 fill-hazard.yml 워크플로우로 수동 실행)

한 번 실행 후에는 제거해도 됩니다 (이후 새 법령은 분석 시 자동으로 채워짐).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DB_PATH
from hrdk_law_core.db import KnowledgeBase
from report_maker import fill_ledger_hazard_column


def main():
    print("=" * 60)
    print("🧩 [일회성] 우대사항_대장 중처법대상 칸 채우기")
    print("=" * 60)

    kb = KnowledgeBase(DB_PATH)
    print(f"📚 지식베이스 로드 ({DB_PATH})\n")

    print("📒 대장 중처법대상 칸 채우는 중...")
    filled = fill_ledger_hazard_column(kb)

    print("\n" + "=" * 60)
    print(f"완료: {filled}개 행에 중처법대상 채움")
    print("=" * 60)


if __name__ == "__main__":
    main()
