# 다운사이드봇 (earnings-screener · Discord)

미국·한국 상장사 실적을 모아 재무제표를 분석하고, **하락/개선 신호가 있는 종목을 필터링**해 하루 두 번 Discord로 보냅니다.

> 원본 텔레그램 발송 버전을 Discord 웹훅 발송으로 전환한 버전입니다.

## 동작

1. **수집** — 실적 이벤트 수집 (Finnhub / EDGAR / DART) → 중복 제거
2. **재무 보강** — EDGAR(미국) · DART(한국)에서 분기 재무데이터
3. **4개 필터 스크리닝** (`services/screener.py`)
   - 52주 신저가 근접 (저가 대비 5% 이내)
   - 매출 성장 QoQ
   - 영업이익 성장 > 매출 성장 (영업 레버리지)
   - 순이익률 개선 QoQ
4. **S&P500 52주 신저가 독립 스캔** 추가
5. **발송** — 카테고리별 정리, 2+ 통과 "주목" / 4개 통과 "🏆 ALL 4 PASS" → Discord

## 발송

- `DISCORD_WEBHOOK_URL` 이 설정돼 있으면 **Discord 웹훅**으로 발송, 없으면 텔레그램 폴백
- HTML → Discord 마크다운 변환, 1,900자 분할 (`services/discord_sender.py`)

## 스케줄 (GitHub Actions)

- 일일: 매일 **07:00 / 19:00 KST** (`0 22,10 * * *` UTC)
- 주간 다이제스트: 매주 **금 21:00 KST** (`0 12 * * 5` UTC) — 주간 3+ 통과 종목 누적분

## 필요한 Secret

| 이름 | 용도 |
|---|---|
| `DISCORD_WEBHOOK_URL` | 발송 대상 Discord 채널 |
| `FINNHUB_API_KEY` | 미국 실적/재무 데이터 |
| `OPENDART_API_KEY` | 한국(DART) 재무 데이터 |

> 텔레그램 발송용 Secret은 Discord 전환으로 불필요합니다.
