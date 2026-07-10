"""Discord 발송 dry-run — 더미 데이터로 최종 Discord 메시지를 stdout에 출력 (전송 X).

로컬에 python-telegram-bot / python-dotenv 가 없어도 실제 빌더(build_report)를
재사용할 수 있도록 telegram / dotenv 모듈을 stub 한다. 네트워크 전송은 하지 않는다.
"""

from __future__ import annotations

import sys
import types


# ── 로컬에서 미설치 모듈 stub (실제 빌더 재사용을 위해) ──────────────────
def _stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


try:  # telegram 패키지 없으면 stub
    import telegram  # noqa: F401
except ModuleNotFoundError:
    _stub("telegram", Bot=object)
    _stub("telegram.constants", ParseMode=types.SimpleNamespace(HTML="HTML"))

try:  # dotenv 없으면 stub
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub("dotenv", load_dotenv=lambda *a, **k: False)


from models.schemas import ScreenResult, FilterResult  # noqa: E402
from services.discord_sender import html_to_discord, split_message  # noqa: E402
from services.telegram_sender import TelegramSender  # noqa: E402


def _r(ticker, name, market, cap, near=False, near_detail="",
       rev=None, op=None, margin=False, margin_detail="",
       desc="", ctx="", eps="") -> ScreenResult:
    return ScreenResult(
        ticker=ticker, yf_ticker=ticker, company_name=name, market=market,
        report_date="2026-07-10", market_cap=cap, description=desc,
        context=ctx, eps_surprise=eps,
        near_52w_low=FilterResult(passed=near, detail=near_detail),
        revenue_growth=FilterResult(passed=rev is not None, value=rev),
        op_income_growth=FilterResult(passed=op is not None, value=op),
        net_margin_improvement=FilterResult(passed=margin, detail=margin_detail),
    )


def build_dummy() -> list[ScreenResult]:
    return [
        # ALL 4 PASS (cat1~4 + 상세카드)
        _r("AAPL", "Apple Inc.", "US", 3.2e12,
           near=True, near_detail="52주 저가 대비 +3.1%",
           rev=0.18, op=0.27, margin=True, margin_detail="순이익률 22% → 25%",
           desc="스마트폰·서비스", ctx="영업레버리지로 이익률 개선",
           eps="EPS $1.28 Beat (est $1.25, +2.4%)"),
        # 52주 신저가만 (KR)
        _r("005930", "삼성전자", "KR", 4.1e11,
           near=True, near_detail="52주 신저가 근접 (+1.2%)",
           desc="반도체·디스플레이"),
        # 매출성장 + 영업이익>매출 (notable, pass_count 2)
        _r("NVDA", "NVIDIA Corp.", "US", 2.9e12,
           rev=0.34, op=0.52,
           desc="GPU·데이터센터", ctx="AI 수요 급증"),
        # 순이익률 개선 + 매출성장 (notable, pass_count 2)
        _r("MSFT", "Microsoft Corp.", "US", 3.0e12,
           rev=0.12, margin=True, margin_detail="순이익률 34% → 37%",
           desc="클라우드·SW"),
    ]


def main() -> int:
    results = build_dummy()
    sections = TelegramSender.build_report(results, total_collected=128, run_date="2026-07-10")

    # 추가로 <a>/<i> 변환 확인용 샘플 (build_report 는 <b> 만 사용)
    sample = ('<b>Bold</b> <i>Italic</i> <code>c</code> '
              '<a href="https://example.com">링크</a> &amp; &lt;test&gt;')

    print("=" * 60)
    print("DISCORD DRY-RUN (전송 없음)")
    print("=" * 60)

    all_output = []
    idx = 0
    for section in sections:
        converted = html_to_discord(section)
        for chunk in split_message(converted):
            idx += 1
            all_output.append(chunk)
            print(f"\n----- 메시지 {idx} (len={len(chunk)}) -----")
            print(chunk)

    print("\n----- 링크/포맷 변환 샘플 -----")
    sample_out = html_to_discord(sample)
    all_output.append(sample_out)
    print(sample_out)

    # 태그 잔존 검증
    joined = "\n".join(all_output)
    residue = [t for t in ("<b>", "</b>", "<i>", "</i>", "<a ", "</a>", "<code>") if t in joined]
    print("\n" + "=" * 60)
    if residue:
        print(f"[FAIL] HTML 태그 잔존: {residue}")
        return 1
    print("[OK] HTML 태그 잔존 없음 (b/i/a/code 모두 변환됨)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
