"""Keşif: Google'da (SerpApi) başvuru e-postası içeren sayfaları bulur, sayfayı açıp metni ve adresleri çıkarır.

Hedef iki tür sayfa:
  - Açık ilan: "CV'nizi ik@firma.com adresine gönderin" gibi ifadeler içeren ilan sayfaları / paylaşımlar
  - Açık başvuru: şirketlerin kendi kariyer / insan kaynakları sayfaları (İK e-postasıyla)
"""
from __future__ import annotations

import html as html_lib
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urljoin, urlparse

import requests

ZAMAN_ASIMI = 12
TARAYICI = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/140.0 Safari/537.36", "Accept-Language": "tr-TR,tr;q=0.9"}
EPOSTA_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Bu alan adlarındaki adresler şirket İK adresi değildir (platformlar, örnek adresler vb.)
YASAK_ALAN_ADLARI = {
    "kariyer.net", "linkedin.com", "indeed.com", "secretcv.com", "yenibiris.com", "eleman.net", "glassdoor.com",
    "example.com", "example.org", "domain.com", "email.com", "sentry.io", "wixpress.com", "w3.org",
    "google.com", "googlemail.com", "facebook.com", "instagram.com", "twitter.com", "x.com", "youtube.com",
    "microsoft.com", "apple.com", "cloudflare.com", "godaddy.com",
}
YASAK_ONEKLER = ("noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster", "abuse",
                 "privacy", "kvkk", "gdpr", "webmaster", "hostmaster", "support", "destek", "satis", "sales",
                 "muhasebe", "fatura", "billing", "press", "basin", "pazarlama", "marketing")
IK_IPUCLARI = ("ik", "hr", "kariyer", "career", "careers", "insankaynaklari", "insan.kaynaklari", "jobs", "job",
               "cv", "basvuru", "recruit", "recruitment", "talent", "is", "isealim", "ise.alim")


@dataclass
class Aday:
    url: str
    baslik: str
    ozet: str
    metin: str = ""
    epostalar: list[str] = field(default_factory=list)

    @property
    def alan(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")

    def ai_metni(self, sinir: int = 6000) -> str:
        return (f"Sayfa: {self.url}\nBaşlık: {self.baslik}\nArama özeti: {self.ozet}\n"
                f"Sayfada bulunan e-postalar: {', '.join(self.epostalar)}\n\nSayfa metni:\n{self.metin[:sinir]}")


# ---------------------------------------------------------------- sorgular
def gunun_sorgulari(ayar: dict, bugun: date | None = None) -> list[tuple[str, str]]:
    """Tüm şablon × rol kombinasyonlarından her gün farklı bir dilim seçer (kota sabit, kapsama geniş).
    (sorgu, zaman_filtresi) döndürür: ilan sorguları ayardaki döneme, açık başvuru sorguları filtresiz."""
    tum = []
    for sablon in ayar.get("ilan_sablonlari", []):
        tum += [(sablon.format(rol=r), ayar.get("donem", "qdr:m")) for r in ayar.get("roller", [])]
    for sablon in ayar.get("acik_basvuru_sablonlari", []):
        tum += [(sablon.format(alan=a), "") for a in ayar.get("alanlar", [])]
    tum = sorted(set(tum))
    if not tum:
        return []
    adet = min(ayar.get("gunluk_sorgu", 8), len(tum))
    gun = (bugun or date.today()).toordinal()
    bas = (gun * adet) % len(tum)
    return [tum[(bas + i) % len(tum)] for i in range(adet)]


def google_ara(sorgu: str, donem: str = "qdr:w", api_key: str | None = None) -> list[Aday]:
    api_key = api_key or os.environ.get("SERPAPI_KEY")
    if not api_key:
        raise RuntimeError("SERPAPI_KEY yok")
    params = {"engine": "google", "q": sorgu, "gl": "tr", "hl": "tr", "google_domain": "google.com.tr",
              "num": 10, "api_key": api_key}
    if donem:
        params["tbs"] = donem
    try:
        r = requests.get("https://serpapi.com/search", params=params, timeout=60)
    except requests.Timeout:
        r = requests.get("https://serpapi.com/search", params=params, timeout=60)
    if r.status_code != 200:
        try:
            mesaj = r.json().get("error", r.text[:200])
        except ValueError:
            mesaj = r.text[:200]
        raise RuntimeError(f"SerpApi {r.status_code}: {mesaj}")
    return google_ayristir(r.json())


def google_ayristir(veri: dict) -> list[Aday]:
    return [Aday(url=o.get("link", ""), baslik=o.get("title", ""), ozet=o.get("snippet", ""))
            for o in veri.get("organic_results", []) if o.get("link", "").startswith("http")]


# ---------------------------------------------------------------- sayfa okuma
def _html_metin(ham: str) -> str:
    ham = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", ham)
    ham = re.sub(r"(?i)<(br|/p|/div|/li|/h\d|/tr)[^>]*>", "\n", ham)
    ham = re.sub(r"<[^>]+>", " ", ham)
    ham = html_lib.unescape(ham)
    ham = re.sub(r"[ \t\r\f\v]+", " ", ham)
    return re.sub(r"\n\s*\n+", "\n", ham).strip()


def _gizli_adresleri_ac(metin: str) -> str:
    """'ik [at] firma [dot] com' / 'ik(at)firma.com' gibi yazımları normal adrese çevirir."""
    metin = re.sub(r"\s*[\[\(\{]\s*(at|@)\s*[\]\)\}]\s*", "@", metin, flags=re.I)
    return re.sub(r"\s*[\[\(\{]\s*(dot|nokta)\s*[\]\)\}]\s*", ".", metin, flags=re.I)


def _ik_mi(adres: str) -> bool:
    yerel = adres.split("@")[0]
    return any(p in re.split(r"[._-]", yerel) or yerel.startswith(p) for p in IK_IPUCLARI if p != "is")


def gecerli_epostalar(adresler: list[str]) -> list[str]:
    sonuc = []
    for a in adresler:
        a = a.strip().strip(".").lower()
        yerel, _, alan = a.partition("@")
        if not alan or alan in YASAK_ALAN_ADLARI or any(alan.endswith("." + y) for y in YASAK_ALAN_ADLARI):
            continue
        if re.search(r"\.(edu|gov|k12|bel|pol|tsk|mil|ac)(\.[a-z]{2})?$", alan):   # üniversite, kamu, okul
            continue
        if yerel.startswith(YASAK_ONEKLER) or re.search(r"\.(png|jpe?g|gif|webp|svg)$", a):
            continue
        if len(yerel) > 40 or re.fullmatch(r"[0-9a-f]{16,}", yerel):   # takip kodu vb.
            continue
        if a not in sonuc:
            sonuc.append(a)
    # İK'ya benzeyenler öne
    return sorted(sonuc, key=lambda a: 0 if _ik_mi(a) else 1)


ILAN_SITELERI = ("kariyer.net", "linkedin.com", "indeed.com", "secretcv.com", "yenibiris.com", "eleman.net",
                 "isbul.net", "kariyerim.com", "glassdoor.com", "youthall.com", "toptalent.co")
# AI'a hiç gönderilmeyecek siteler (ilan siteleri, çeviri/CV araçları, ansiklopedi, video vb.)
ATLANACAK = ILAN_SITELERI + ("resume.io", "translate.google", "docs.google", "wikipedia.org", "youtube.com",
                             "facebook.com", "instagram.com", "twitter.com", "x.com", "reddit.com", "eksisozluk.com",
                             "sikayetvar.com", "novoresume", "zety.", "canva.com")


def atlanacak_mi(aday: "Aday") -> bool:
    return any(s in aday.alan for s in ATLANACAK) or bool(re.search(r"\.(edu|gov|k12)(\.[a-z]{2})?$", aday.alan))
ALT_SAYFALAR = ("/kariyer", "/insan-kaynaklari", "/careers", "/iletisim", "/contact")


def _getir(url: str) -> str:
    try:
        r = requests.get(url, headers=TARAYICI, timeout=ZAMAN_ASIMI, stream=True)
        if r.status_code == 200 and "html" in r.headers.get("content-type", "html"):
            return r.raw.read(1_500_000, decode_content=True).decode(r.encoding or "utf-8", errors="ignore")
    except requests.RequestException:
        pass
    return ""


def _adresler(ham: str, ek: str = "") -> list[str]:
    mailto = re.findall(r"mailto:([^\"'?>\s]+)", ham, flags=re.I)
    metin = _gizli_adresleri_ac(_html_metin(ham)) if ham else ""
    return gecerli_epostalar(EPOSTA_RE.findall(_gizli_adresleri_ac(f"{metin}\n{ek}\n{' '.join(mailto)}")))


def sayfa_oku(aday: Aday) -> Aday:
    ham = _getir(aday.url)
    aday.metin = _gizli_adresleri_ac(_html_metin(ham)) if ham else ""
    aday.epostalar = _adresler(ham, f"{aday.ozet}\n{aday.baslik}")
    # Sayfada adres yoksa ve bu bir ilan sitesi değilse, aynı sitenin iletişim/kariyer sayfalarına bak
    if not aday.epostalar and not any(s in aday.alan for s in ILAN_SITELERI):
        kok = f"{urlparse(aday.url).scheme}://{urlparse(aday.url).netloc}"
        toplanan: list[str] = []
        for yol in ALT_SAYFALAR:
            alt = _getir(urljoin(kok, yol))
            if not alt:
                continue
            bulunan = _adresler(alt)
            if bulunan:
                toplanan += [a for a in bulunan if a not in toplanan]
                aday.metin += f"\n\n[{yol} sayfasından]\n" + _gizli_adresleri_ac(_html_metin(alt))[:2000]
                if _ik_mi(bulunan[0]):      # İK/kariyer adresi bulundu, yeter
                    break
        aday.epostalar = gecerli_epostalar(toplanan)
    if not aday.metin:
        aday.metin = aday.ozet
    return aday


def sayfalari_oku(adaylar: list[Aday], paralel: int = 8) -> list[Aday]:
    with ThreadPoolExecutor(max_workers=paralel) as havuz:
        return list(havuz.map(sayfa_oku, adaylar))


def ilgili_mi(aday: Aday, anahtarlar: list[str]) -> bool:
    """AI'ya göndermeden önce hızlı eleme: e-posta olmalı ve metinde hedef alan/İK kelimelerinden biri geçmeli."""
    if not aday.epostalar:
        return False
    metin = f"{aday.baslik} {aday.ozet} {aday.metin[:8000]}".lower()
    return any(k in metin for k in anahtarlar)
