"""İlan kaynakları. Her kaynak normalize edilmiş Ilan listesi döndürür."""
from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

EPOSTA_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]*[a-zA-Z]{2,}")
ATS_RE = re.compile(r"greenhouse\.io|lever\.co|workable\.com|myworkdayjobs|smartrecruiters|breezy\.hr|recruitee|teamtailor", re.I)
ZAMAN_ASIMI = 60


@dataclass
class Ilan:
    baslik: str
    sirket: str
    konum: str
    aciklama: str
    kaynak: str
    linkler: list[dict] = field(default_factory=list)   # [{"ad": "LinkedIn", "url": "..."}]
    yayin: str = ""                                      # serbest metin ("2 gün önce") veya ISO tarih
    sabit_id: str = ""                                   # varsa (ör. ilan URL'si) tekrar kontrolü buna göre

    @property
    def id(self) -> str:
        anahtar = self.sabit_id or f"{_normal(self.sirket)}|{_normal(self.baslik)}"
        return hashlib.sha1(anahtar.encode()).hexdigest()[:16]

    @property
    def epostalar(self) -> list[str]:
        bulunan = EPOSTA_RE.findall(self.aciklama)
        return list(dict.fromkeys(e.rstrip(".") for e in bulunan if not e.lower().startswith(("noreply", "no-reply"))))

    @property
    def ats_linki(self) -> str | None:
        return next((l["url"] for l in self.linkler if ATS_RE.search(l["url"])), None)

    def metin(self) -> str:
        linkler = "\n".join(f"- {l['ad']}: {l['url']}" for l in self.linkler)
        return (f"Pozisyon: {self.baslik}\nŞirket: {self.sirket}\nKonum: {self.konum}\nYayın: {self.yayin}\n\n"
                f"{self.aciklama}\n\nBaşvuru linkleri:\n{linkler}")


def _normal(s: str) -> str:
    return re.sub(r"[^a-z0-9ğüşöçı]+", " ", s.lower()).strip()


def _html_temizle(s: str) -> str:
    s = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def _gun_once(yayin: str) -> int | None:
    """'3 gün önce', '5 days ago', '2 saat önce' → gün sayısı."""
    m = re.search(r"(\d+)\s*(saat|hour|gün|day|hafta|week|ay|month)", yayin, re.I)
    if not m:
        return None
    n, birim = int(m.group(1)), m.group(2).lower()
    return 0 if birim in ("saat", "hour") else n * {"gün": 1, "day": 1, "hafta": 7, "week": 7, "ay": 30, "month": 30}[birim]


def yeterince_yeni(ilan: Ilan, max_gun: int) -> bool:
    if not ilan.yayin:
        return True
    try:
        tarih = datetime.fromisoformat(ilan.yayin.replace("Z", "+00:00"))
        if tarih.tzinfo is None:
            tarih = tarih.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - tarih <= timedelta(days=max_gun)
    except ValueError:
        gun = _gun_once(ilan.yayin)
        return gun is None or gun <= max_gun


# ---------------------------------------------------------------- SerpApi Google Jobs
def google_jobs(sorgu: str, konum: str = "", api_key: str | None = None) -> list[Ilan]:
    api_key = api_key or os.environ.get("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_KEY .env dosyasında yok")
    params = {"engine": "google_jobs", "q": sorgu, "hl": "tr", "api_key": api_key}   # gl=tr desteklenmiyor
    if konum:
        params["location"] = konum
    r = requests.get("https://serpapi.com/search", timeout=ZAMAN_ASIMI, params=params)
    if r.status_code != 200:
        try:
            mesaj = r.json().get("error", r.text[:200])
        except ValueError:
            mesaj = r.text[:200]
        if konum and "location" in mesaj.lower():   # konum tanınmadıysa konumsuz tekrar dene
            return google_jobs(sorgu, "", api_key)
        raise RuntimeError(f"SerpApi {r.status_code}: {mesaj}")   # URL (ve anahtar) yazdırılmaz
    return google_jobs_ayristir(r.json())


def google_jobs_ayristir(veri: dict) -> list[Ilan]:
    if veri.get("error"):
        raise RuntimeError(f"SerpApi: {veri['error']}")
    ilanlar = []
    for j in veri.get("jobs_results", []):
        ek = j.get("detected_extensions", {}) or {}
        ilanlar.append(Ilan(
            baslik=j.get("title", ""), sirket=j.get("company_name", ""), konum=j.get("location", ""),
            aciklama=j.get("description", ""), kaynak=f"google_jobs ({j.get('via', '').replace('via ', '')})",
            linkler=[{"ad": a.get("title", ""), "url": a.get("link", "")} for a in j.get("apply_options", [])],
            yayin=ek.get("posted_at", ""),
        ))
    return ilanlar


# ---------------------------------------------------------------- Remotive
def remotive(sorgu: str) -> list[Ilan]:
    r = requests.get("https://remotive.com/api/remote-jobs", params={"search": sorgu, "limit": 50}, timeout=ZAMAN_ASIMI)
    r.raise_for_status()
    return remotive_ayristir(r.json())


def remotive_ayristir(veri: dict) -> list[Ilan]:
    return [Ilan(
        baslik=j.get("title", ""), sirket=j.get("company_name", ""),
        konum=f"Remote ({j.get('candidate_required_location', '')})",
        aciklama=_html_temizle(j.get("description", "")), kaynak="remotive",
        linkler=[{"ad": "Remotive", "url": j.get("url", "")}], yayin=j.get("publication_date", ""),
    ) for j in veri.get("jobs", [])]


# ---------------------------------------------------------------- SerpApi Google Web (site: aramaları)
SITE_ADI = {"kariyer.net": "Kariyer.net", "linkedin.com": "LinkedIn", "yenibiris.com": "Yenibiris", "secretcv.com": "SecretCV"}


def google_web(sorgu: str, siteler: list[str], api_key: str | None = None, donem: str = "qdr:w",
               ek: str = "") -> list[Ilan]:
    api_key = api_key or os.environ.get("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_KEY .env dosyasında yok")
    site_ifadesi = f"site:{siteler[0]}" if len(siteler) == 1 else "(" + " OR ".join(f"site:{s}" for s in siteler) + ")"
    params = {"engine": "google", "q": f'{site_ifadesi} "{sorgu}" {ek}'.strip(), "gl": "tr", "hl": "tr",
              "google_domain": "google.com.tr", "tbs": donem, "api_key": api_key}
    try:
        r = requests.get("https://serpapi.com/search", timeout=ZAMAN_ASIMI, params=params)
    except requests.Timeout:   # SerpApi ara sıra yavaş; bir kez daha dene
        r = requests.get("https://serpapi.com/search", timeout=ZAMAN_ASIMI, params=params)
    if r.status_code != 200:
        try:
            mesaj = r.json().get("error", r.text[:200])
        except ValueError:
            mesaj = r.text[:200]
        raise RuntimeError(f"SerpApi {r.status_code}: {mesaj}")
    return google_web_ayristir(r.json())


def _site(url: str) -> str:
    return next((ad for alan, ad in SITE_ADI.items() if alan in url), "Web")


def _baslik_ayristir(ham: str, site: str) -> tuple[str, str]:
    """Sayfa başlığından (pozisyon, şirket) çıkarır. Emin olamazsa şirket boş kalır."""
    t = re.sub(r"\s*[|–-]\s*(LinkedIn|Kariyer\.net|Yenibiris|SecretCV)\s*$", "", ham, flags=re.I).strip()
    t = re.sub(r"^\d{1,2}\.\d{1,2}\.\d{4}\s*[–-]\s*", "", t)          # baştaki tarih
    t = re.sub(r"\s*(\.\.\.|…)$", "", t).strip()
    m = re.match(r"(.+?) (?:hiring|işe alım yapıyor:?) (.+?)(?: in (.+))?$", t, re.I)       # LinkedIn EN/TR
    if m:
        return m.group(2).strip(), m.group(1).strip()
    t = re.sub(r"\s*(İş İlanı|İş İlanları|iş ilanı)\s*$", "", t).strip()
    if " - " in t:                                                                          # Kariyer.net: Pozisyon - Şirket
        poz, _, sirket = t.partition(" - ")
        return poz.strip(), sirket.strip()
    return t, ""


def google_web_ayristir(veri: dict) -> list[Ilan]:
    if veri.get("error") and "hasn't returned any results" not in veri["error"]:
        raise RuntimeError(f"SerpApi: {veri['error']}")
    ilanlar = []
    for o in veri.get("organic_results", []):
        url = o.get("link", "")
        if not re.search(r"kariyer\.net/is-ilani/|linkedin\.com/jobs/view/|yenibiris\.com/is-ilani|secretcv\.com/is-ilani", url):
            continue   # liste/arama sayfalarını atla, sadece tekil ilan sayfaları
        site = _site(url)
        poz, sirket = _baslik_ayristir(o.get("title", ""), site)
        ilanlar.append(Ilan(
            baslik=poz, sirket=sirket or "", konum="", kaynak=f"google_web ({site})",
            aciklama=(f"[Arama özeti; ilanın tam metni değil]\nSayfa başlığı: {o.get('title', '')}\n"
                      f"Link: {url}\n{o.get('snippet', '')}"),
            linkler=[{"ad": site, "url": url}], yayin=o.get("date", ""), sabit_id=url.split("?")[0],
        ))
    return ilanlar
