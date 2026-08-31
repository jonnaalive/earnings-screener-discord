"""Discord 웹훅 발송 (텔레그램 발송부의 Discord 대체)."""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
import urllib.request
import urllib.error

from models.schemas import ScreenResult

logger = logging.getLogger(__name__)

DISCORD_MAX = 2000
SPLIT_LIMIT = 1900  # 여유분

USERNAME = "다운사이드봇"

_A_TAG = re.compile(r'<a\s+href="([^"]*)"\s*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_TAG = re.compile(r"</?[a-zA-Z][^>]*>")


def html_to_discord(text: str) -> str:
    """텔레그램 HTML 포맷 문자열을 Discord 마크다운으로 변환."""
    # 링크: <a href="U">T</a> -> T: U  (Discord 일반 메시지는 masked link 미지원)
    text = _A_TAG.sub(lambda m: f"{_strip_tags(m.group(2))}: {m.group(1)}", text)
    # bold / italic / code
    text = re.sub(r"</?(b|strong)>", "**", text, flags=re.IGNORECASE)
    text = re.sub(r"</?(i|em)>", "*", text, flags=re.IGNORECASE)
    text = re.sub(r"</?code>", "`", text, flags=re.IGNORECASE)
    text = re.sub(r"</?pre>", "```", text, flags=re.IGNORECASE)
    # 남은 태그 제거 + 엔티티 복원
    text = _TAG.sub("", text)
    return _html.unescape(text)


def _strip_tags(text: str) -> str:
    return _html.unescape(_TAG.sub("", text))


def split_message(text: str, limit: int = SPLIT_LIMIT) -> list[str]:
    """라인 경계 기준으로 limit 이하 청크 분할."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        # 한 줄 자체가 limit 초과하면 강제 분할
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


def post_webhook(webhook_url: str, content: str, username: str | None = None) -> None:
    """단일 청크를 웹훅으로 POST (429 재시도 포함). HTML 입력을 Discord로 변환해 전송."""
    payload = {"content": html_to_discord(content)}
    if username:
        payload["username"] = username
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(5):
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "discord-webhook (github.com/jonnaalive)"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    retry = json.loads(e.read().decode()).get("retry_after", 1)
                except Exception:
                    retry = 1
                time.sleep(min(float(retry) + 0.5, 10))
                continue
            logger.error("Discord webhook HTTP %s: %s", e.code, e.reason)
            raise
        except urllib.error.URLError as e:
            logger.error("Discord webhook 전송 실패: %s", e)
            raise
    logger.error("Discord webhook 재시도 초과 (429)")


def send_html(webhook_url: str, text: str, username: str | None = None) -> None:
    """HTML 포맷 텍스트를 변환·분할·전송."""
    # 변환 후 길이로 분할해야 정확 -> 먼저 변환, 그다음 분할, post는 재변환 안 하도록 raw 전송
    converted = html_to_discord(text)
    for chunk in split_message(converted):
        _post_raw(webhook_url, chunk, username)


def _post_raw(webhook_url: str, content: str, username: str | None = None) -> None:
    payload = {"content": content}
    if username:
        payload["username"] = username
    data = json.dumps(payload).encode("utf-8")
    for _ in range(5):
        req = urllib.request.Request(
            webhook_url, data=data,
            headers={"Content-Type": "application/json", "User-Agent": "discord-webhook (github.com/jonnaalive)"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    retry = json.loads(e.read().decode()).get("retry_after", 1)
                except Exception:
                    retry = 1
                time.sleep(min(float(retry) + 0.5, 10))
                continue
            logger.error("Discord webhook HTTP %s: %s", e.code, e.reason)
            raise
        except urllib.error.URLError as e:
            logger.error("Discord webhook 전송 실패: %s", e)
            raise


class DiscordSender:
    """TelegramSender와 동일한 공개 시그니처를 갖는 Discord 웹훅 발송기.

    메시지 조립은 TelegramSender의 정적 빌더(build_report 등)를 재사용하고,
    결과 HTML을 send_html()로 변환·분할·전송한다.
    """

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.username = USERNAME

    async def send_results(self, results: list[ScreenResult], total_collected: int,
                           run_date: str) -> int:
        # 지연 import: telegram 패키지 의존을 발송 시점으로 미룸.
        from services.telegram_sender import TelegramSender

        sections = TelegramSender.build_report(results, total_collected, run_date)
        sent = 0
        failures = []
        for chunk in sections:
            try:
                send_html(self.webhook_url, chunk, username=self.username)
                sent += 1
            except Exception as e:
                logger.error("Failed to send message: %s", e)
                failures.append(e)
        if failures:
            raise RuntimeError(
                f"Discord delivery failed for {len(failures)} of {len(sections)} messages"
            ) from failures[0]
        logger.info("Sent %d messages to discord", sent)
        return sent

    async def send_text(self, text: str):
        send_html(self.webhook_url, text, username=self.username)

    async def send_error(self, error_msg: str):
        text = f"⚠️ [Earnings Screener ERROR]\n{error_msg}"
        try:
            send_html(self.webhook_url, text, username=self.username)
        except Exception as e:
            logger.error("Failed to send error notification: %s", e)
