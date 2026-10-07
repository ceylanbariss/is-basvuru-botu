"""İş alarmı okuyucu: LinkedIn ve Kariyer.net'in Gmail'e gönderdiği günlük iş alarmı e-postalarından
ilanları çıkarır. Siteye giriş/otomasyon yok; sadece senin gelen kutundaki e-postalar okunur (IMAP, salt okunur).
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from html.parser import HTMLParser

from toplayici.kaynaklar import Ilan

KAYNAKLAR = {
    "LinkedIn": {"gonderen": "linkedin.com",
                 "link": re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(?:[^/?\s\"]*?-)?(\d{6,})", re.I),
                 "url": "https://www.linkedin.com/jobs/view/{}/"},
    "Kariyer.net": {"gonderen": "kariyer.net",
                    "link": re.compile(r"kariyer\.net/is-ilani/([a-z0-9\-]+-\d{5,})", re.I),
                    "url": "https://www.kariyer.net/is-ilani/{}"},
}
GURULTU = re.compile(r"^(apply|easy apply|kolay başvuru|başvur|view job|ilanı gör|see all jobs|tümünü gör|"
                     r"daha fazla|unsubscribe|abonelikten çık|\d+ (applicants|başvuru)|promoted|öne çıkan|new|yeni)$", re.I)


class _Baglantilar(HTMLParser):
    """<a> etiketlerini (href, görünen metin, hemen ardından gelen düz metin) olarak toplar.
    İş alarmlarında şirket/konum çoğu zaman linkin altında düz metin olarak durur."""
    def __init__(self):
        super().__init__()
        self.linkler: list[list[str]] = []
        self._href: str | None = None
        self._metin: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._metin = []
        elif tag in ("br", "p", "div", "td", "tr", "li") and self.linkler and self._href is None:
            self.linkler[-1][2] += "\n"

    def handle_data(self, data):
        if self._href is not None:
            self._metin.append(data)
        elif self.linkler and len(self.linkler[-1][2]) < 300:
            self.linkler[-1][2] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.linkler.append([self._href, re.sub(r"\s+", " ", " ".join(self._metin)).strip(), ""])
            self._href = None


def _html_govde(mesaj) -> str:
    for parca in mesaj.walk():
        if parca.get_content_type() == "text/html":
            return parca.get_payload(decode=True).decode(parca.get_content_charset() or "utf-8", errors="ignore")
    return ""


KARIYER_TAKIP = re.compile(r"link\.kariyer\.net/p/be/cl/([0-9a-f\-]{36})", re.I)


def _kariyer_takip_eslemesi(html: str) -> dict[str, str]:
    """Kariyer.net e-postalarında linkler takip adresine gider (link.kariyer.net/p/be/cl/<kod>/...).
    Gerçek ilan adresi, aynı ilan bloğundaki Outlook yorumunda durur: <!--[if mso]><a href="www.kariyer.net/is-ilani/...">
    HTML'i sırayla tarayıp her ilan adresini kendisinden önce gelen son takip koduna bağlar."""
    olaylar = sorted([(m.start(), "takip", m.group(1)) for m in KARIYER_TAKIP.finditer(html)]
                     + [(m.start(), "ilan", m.group(1)) for m in KAYNAKLAR["Kariyer.net"]["link"].finditer(html)])
    esleme: dict[str, str] = {}
    son_takip = None
    for _, tur, deger in olaylar:
        if tur == "takip":
            son_takip = deger
        elif son_takip and son_takip not in esleme:
            esleme[son_takip] = deger
    return esleme


def ilanlari_ayikla(html: str, kaynak: str) -> list[Ilan]:
    """Aynı ilana giden tüm linklerin metinlerini birleştirir: genelde başlık, şirket ve konum ayrı linklerdedir."""
    k = KAYNAKLAR[kaynak]
    p = _Baglantilar()
    p.feed(html)
    takip = _kariyer_takip_eslemesi(html) if kaynak == "Kariyer.net" else {}
    gruplar: dict[str, list[str]] = {}
    for href, metin, sonra in p.linkler:
        href = href.replace("%2F", "/")
        m = k["link"].search(href)
        if m:
            kimlik = m.group(1)
        else:
            t = KARIYER_TAKIP.search(href) if takip else None
            if not t or t.group(1) not in takip:
                continue
            kimlik = takip[t.group(1)]
        parcalar = gruplar.setdefault(kimlik, [])
        # önce linkin kendi metni, sonra altındaki ilk birkaç satır (şirket, konum)
        satirlar = [metin] + [x for x in re.split(r"\n+", sonra) if x.strip()][:2]
        for parca in re.split(r"\s+[·•|]\s+|\n", "\n".join(satirlar)):
            parca = re.sub(r"\s+", " ", parca).strip()
            if parca and not GURULTU.match(parca) and parca not in parcalar and len(parca) < 120:
                parcalar.append(parca)
    ilanlar = []
    for kimlik, parcalar in gruplar.items():
        if not parcalar:
            continue
        url = k["url"].format(kimlik)
        ilanlar.append(Ilan(
            baslik=parcalar[0], sirket=parcalar[1] if len(parcalar) > 1 else "",
            konum=parcalar[2] if len(parcalar) > 2 else "",
            aciklama="[İş alarmı e-postası; ilanın tam metni değil]\n" + " · ".join(parcalar),
            kaynak=f"alarm ({kaynak})", linkler=[{"ad": kaynak, "url": url}], sabit_id=url,
        ))
    return ilanlar


def alarm_ilanlari(gun: int = 1) -> tuple[list[Ilan], dict[str, int]]:
    """Son `gun` gündeki alarm e-postalarındaki ilanlar ve kaynak başına e-posta sayısı."""
    kullanici, sifre = os.environ["GMAIL_ADRES"], os.environ["GMAIL_UYGULAMA_SIFRESI"].replace(" ", "")
    tarih = (datetime.now() - timedelta(days=gun)).strftime("%d-%b-%Y")
    ilanlar, sayac = [], {}
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(kullanici, sifre)
        imap.select("INBOX", readonly=True)
        for kaynak, k in KAYNAKLAR.items():
            _, veri = imap.search(None, f'(SINCE {tarih} FROM "{k["gonderen"]}")')
            numaralar = veri[0].split()[-40:]
            sayac[kaynak] = 0
            for no in numaralar:
                _, parcalar = imap.fetch(no, "(RFC822)")
                mesaj = email.message_from_bytes(parcalar[0][1])
                konu = str(make_header(decode_header(mesaj.get("Subject", ""))))
                bulunan = ilanlari_ayikla(_html_govde(mesaj), kaynak)
                if bulunan:          # sadece ilan içeren e-postaları say (bildirim/mesaj e-postalarını atla)
                    sayac[kaynak] += 1
                    ilanlar += bulunan
    tekil = {i.sabit_id: i for i in ilanlar}
    return list(tekil.values()), sayac


def ornek_kaydet(kaynak: str, yol: str, gun: int = 3) -> bool:
    """Ayrıştırıcıyı ayarlamak için son alarm e-postasının HTML'ini dosyaya yazar (yerel teşhis aracı)."""
    k = KAYNAKLAR[kaynak]
    tarih = (datetime.now() - timedelta(days=gun)).strftime("%d-%b-%Y")
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(os.environ["GMAIL_ADRES"], os.environ["GMAIL_UYGULAMA_SIFRESI"].replace(" ", ""))
        imap.select("INBOX", readonly=True)
        _, veri = imap.search(None, f'(SINCE {tarih} FROM "{k["gonderen"]}")')
        for no in reversed(veri[0].split()):
            _, parcalar = imap.fetch(no, "(RFC822)")
            html = _html_govde(email.message_from_bytes(parcalar[0][1]))
            if k["link"].search(html):
                open(yol, "w", encoding="utf-8").write(html)
                return True
    return False
