"""OpenDART 분기/반기보고서 수집기."""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import requests

from models.schemas import EarningsEvent
from services.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
CORP_CODE_CACHE = DATA_DIR / "corpcode.xml"


class DartCollector(BaseCollector):
    name = "dart"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://opendart.fss.or.kr/api"
        self._corp_map: dict[str, str] = {}  # stock_code(6) -> corp_code(8)
        self._name_map: dict[str, str] = {}  # corp_code(8) -> corp_name

    def _load_corp_codes(self):
        """corpcode.xml 다운로드 및 stock_code ↔ corp_code 매핑 구축."""
        if self._corp_map:
            return

        # 캐시가 30일 이내면 재사용
        if CORP_CODE_CACHE.exists():
            import time
            age = time.time() - CORP_CODE_CACHE.stat().st_mtime
            if age < 30 * 86400:
                try:
                    self._parse_corp_xml(CORP_CODE_CACHE.read_bytes())
                    return
                except Exception:
                    pass

        try:
            resp = requests.get(
                f"{self.base_url}/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=60,
            )
            resp.raise_for_status()

            # ZIP 해제
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                xml_name = zf.namelist()[0]
                xml_data = zf.read(xml_name)
                CORP_CODE_CACHE.parent.mkdir(parents=True, exist_ok=True)
                CORP_CODE_CACHE.write_bytes(xml_data)
                self._parse_corp_xml(xml_data)

        except Exception as e:
            logger.error("[dart] Failed to load corp codes: %s", e)

    def _parse_corp_xml(self, xml_bytes: bytes):
        root = ElementTree.fromstring(xml_bytes)
        for corp in root.findall("list"):
            corp_code = corp.findtext("corp_code", "")
            corp_name = corp.findtext("corp_name", "")
            stock_code = corp.findtext("stock_code", "").strip()
            if stock_code:  # 상장사만
                self._corp_map[stock_code] = corp_code
                self._name_map[corp_code] = corp_name
        logger.info("[dart] Loaded %d listed companies", len(self._corp_map))

    async def collect(self, lookback_days: int = 3) -> list[EarningsEvent]:
        if not self.api_key:
            logger.warning("[dart] No API key configured, skipping")
            return []

        self._load_corp_codes()
        events = []
        today = datetime.now()
        from_date = (today - timedelta(days=lookback_days)).strftime("%Y%m%d")
        to_date = today.strftime("%Y%m%d")

        try:
            # 분기보고서(11013), 반기보고서(11012), 사업보고서(11011)
            for pblntf_ty in ["A003"]:  # A003 = 정기공시
                resp = requests.get(
                    f"{self.base_url}/list.json",
                    params={
                        "crtfc_key": self.api_key,
                        "bgn_de": from_date,
                        "end_de": to_date,
                        "pblntf_ty": pblntf_ty,
                        "page_count": 100,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()

                if data.get("status") != "000":
                    logger.warning("[dart] API response: %s - %s",
                                   data.get("status"), data.get("message"))
                    continue

                for item in data.get("list", []):
                    report_nm = item.get("report_nm", "")
                    # 분기/반기/사업보고서만 필터
                    if not any(kw in report_nm for kw in ["분기보고서", "반기보고서", "사업보고서"]):
                        continue

                    corp_code = item.get("corp_code", "")
                    corp_name = item.get("corp_name", "")
                    rcept_dt = item.get("rcept_dt", "")  # YYYYMMDD

                    # corp_code에서 stock_code 역매핑
                    stock_code = ""
                    for sc, cc in self._corp_map.items():
                        if cc == corp_code:
                            stock_code = sc
                            break

                    if not stock_code:
                        continue

                    report_date = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"

                    events.append(EarningsEvent(
                        ticker=stock_code,
                        company_name=corp_name,
                        report_date=report_date,
                        source="dart",
                        market="KR",
                    ))

        except Exception as e:
            logger.error("[dart] Collection failed: %s", e)

        self._log_result(events)
        return events
