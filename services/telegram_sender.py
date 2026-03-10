"""스크리닝 결과 텔레그램 발송."""

from __future__ import annotations

import logging
from datetime import datetime

from telegram import Bot
from telegram.constants import ParseMode

from config.settings import TelegramBotConfig
from models.schemas import ScreenResult

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4096
WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


class TelegramSender:
    def __init__(self, config: TelegramBotConfig):
        self.bot = Bot(token=config.bot_token)
        self.chat_id = config.chat_id

    def build_report(self, results: list[ScreenResult], total_collected: int,
                     run_date: str) -> list[str]:
        dt = datetime.strptime(run_date, "%Y-%m-%d")
        weekday = WEEKDAY_KR[dt.weekday()]

        cat1 = [r for r in results if r.near_52w_low.passed]
        cat2 = [r for r in results if r.revenue_growth.passed]
        cat3 = [r for r in results if r.op_income_growth.passed]
        cat4 = [r for r in results if r.net_margin_improvement.passed]
        all_pass = [r for r in results if r.all_pass]
        notable = [r for r in results if r.pass_count >= 2 and not r.all_pass]
        notable.sort(key=lambda r: r.pass_count, reverse=True)

        # ── 메시지 1: 카테고리 요약 ──
        msg1 = []
        msg1.append(f"📊 <b>Earnings Screener</b>")
        msg1.append(f"📅 {run_date} ({weekday})  |  수집 {total_collected}  |  스크리닝 {len(results)}")
        msg1.append("")

        # 52주 신저가
        msg1.append(f"📉 <b>52주 신저가</b> ({len(cat1)})")
        if cat1:
            for r in cat1:
                msg1.append(f"   ▸ <b>{self._name(r)}</b>  {r.near_52w_low.detail}")
                if r.description:
                    msg1.append(f"      🏢 {r.description}")
                if r.context:
                    msg1.append(f"      💡 {r.context}")
                msg1.append("")
        else:
            msg1.append("   없음")
            msg1.append("")

        # 매출 성장
        msg1.append(f"📈 <b>매출 성장 QoQ</b> ({len(cat2)})")
        if cat2:
            for r in cat2:
                v = r.revenue_growth.value
                bar = self._bar(v)
                msg1.append(f"   ▸ <b>{self._name(r)}</b>  {bar} {v:+.1%}")
                msg1.append("")
        else:
            msg1.append("   없음")
            msg1.append("")

        # 영업이익 > 매출
        msg1.append(f"⚡ <b>영업이익 > 매출 성장</b> ({len(cat3)})")
        if cat3:
            for r in cat3:
                rv = r.revenue_growth.value or 0
                ov = r.op_income_growth.value or 0
                msg1.append(f"   ▸ <b>{self._name(r)}</b>  매출 {rv:+.1%} → 영업 {ov:+.1%}")
                msg1.append("")
        else:
            msg1.append("   없음")
            msg1.append("")

        # 순이익률 개선
        msg1.append(f"💰 <b>순이익률 개선</b> ({len(cat4)})")
        if cat4:
            for r in cat4:
                msg1.append(f"   ▸ <b>{self._name(r)}</b>  {r.net_margin_improvement.detail}")
                msg1.append("")
        else:
            msg1.append("   없음")

        messages = ["\n".join(msg1)]

        # ── 메시지 2: 주목 종목 상세 카드 ──
        if all_pass or notable:
            msg2 = []

            if all_pass:
                msg2.append(f"🏆 <b>ALL 4 PASS</b> ({len(all_pass)})")
                msg2.append("")
                for r in all_pass:
                    msg2.append(self._card(r))
                    msg2.append("")

            if notable:
                msg2.append(f"🔍 <b>주목 종목</b> ({len(notable)})")
                msg2.append("")
                for r in notable:
                    msg2.append(self._card(r))
                    msg2.append("")

            messages.append("\n".join(msg2).rstrip())

        # 각 메시지를 4096자 제한으로 분할
        final = []
        for msg in messages:
            final.extend(self._split(msg))
        return final

    # ── 포맷 헬퍼 ──

    def _name(self, r: ScreenResult) -> str:
        """티커 (+ 회사명 for KR) + 시가총액."""
        if r.market == "KR" and r.company_name and r.company_name != "unknown":
            base = f"{r.ticker} {r.company_name}"
        else:
            base = r.ticker
        cap = self._mcap(r.market_cap)
        return f"{base}  {cap}" if cap else base

    def _full_name(self, r: ScreenResult) -> str:
        """티커 + 회사명 + 시가총액."""
        if r.company_name and r.company_name != r.ticker and r.company_name != "unknown":
            base = f"{r.ticker}  {r.company_name}"
        else:
            base = r.ticker
        cap = self._mcap(r.market_cap)
        return f"{base}  {cap}" if cap else base

    def _mcap(self, cap: float | None) -> str:
        """시가총액 축약 표시."""
        if not cap:
            return ""
        if cap >= 1e12:
            return f"💎${cap / 1e12:.1f}T"
        elif cap >= 1e9:
            return f"💎${cap / 1e9:.1f}B"
        elif cap >= 1e6:
            return f"💎${cap / 1e6:.0f}M"
        return ""

    def _bar(self, value: float | None) -> str:
        """성장률 시각 바."""
        if value is None:
            return ""
        pct = abs(value) * 100
        if pct >= 50:
            return "▓▓▓▓▓"
        elif pct >= 20:
            return "▓▓▓▓░"
        elif pct >= 10:
            return "▓▓▓░░"
        elif pct >= 5:
            return "▓▓░░░"
        else:
            return "▓░░░░"

    def _card(self, r: ScreenResult) -> str:
        """종목 상세 카드."""
        lines = []

        # 1줄: 이름 + 배지
        badge = self._badge(r)
        lines.append(f"{'━' * 20}")
        lines.append(f"{badge}  <b>{self._full_name(r)}</b>")

        # 2줄: 회사 설명
        if r.description:
            lines.append(f"🏢 {r.description}")

        # 3줄: EPS
        if r.eps_surprise:
            emoji = "✅" if "Beat" in r.eps_surprise else "❌" if "Miss" in r.eps_surprise else "📋"
            lines.append(f"{emoji} {r.eps_surprise}")

        # 4줄: 필터 수치 한 줄
        metrics = []
        if r.near_52w_low.passed:
            metrics.append(f"📉 {r.near_52w_low.detail}")
        if r.revenue_growth.value is not None:
            metrics.append(f"📈 Rev {r.revenue_growth.value:+.1%}")
        if r.op_income_growth.value is not None:
            metrics.append(f"⚡ OP {r.op_income_growth.value:+.1%}")
        if r.net_margin_improvement.detail and r.net_margin_improvement.detail != "데이터 없음":
            metrics.append(f"💰 {r.net_margin_improvement.detail}")
        if metrics:
            lines.append("   ".join(metrics))

        # 5줄: 컨텍스트
        if r.context:
            lines.append(f"💡 {r.context}")

        return "\n".join(lines)

    def _badge(self, r: ScreenResult) -> str:
        """통과 개수 이모지 배지."""
        n = r.pass_count
        if n == 4:
            return "🏆"
        elif n == 3:
            return "🔸"
        elif n == 2:
            return "🔹"
        return "▫️"

    def _split(self, text: str) -> list[str]:
        if len(text) <= MAX_MESSAGE_LENGTH:
            return [text]
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > MAX_MESSAGE_LENGTH:
                chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks

    async def send_results(self, results: list[ScreenResult], total_collected: int,
                           run_date: str) -> int:
        sections = self.build_report(results, total_collected, run_date)
        sent = 0
        for chunk in sections:
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id, text=chunk, parse_mode=ParseMode.HTML,
                )
                sent += 1
            except Exception as e:
                logger.error("Failed to send message: %s", e)
                try:
                    await self.bot.send_message(chat_id=self.chat_id, text=chunk)
                    sent += 1
                except Exception as e2:
                    logger.error("Plain text fallback failed: %s", e2)
        logger.info("Sent %d messages to telegram", sent)
        return sent

    async def send_text(self, text: str):
        for chunk in self._split(text):
            await self.bot.send_message(chat_id=self.chat_id, text=chunk)

    async def send_error(self, error_msg: str):
        text = f"⚠️ [Earnings Screener ERROR]\n{error_msg}"
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=text)
        except Exception as e:
            logger.error("Failed to send error notification: %s", e)
