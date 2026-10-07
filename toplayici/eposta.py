"""Sabah özet e-postası (kendine):
  1) Bugün otomatik gönderilen başvurular
  2) LinkedIn / Kariyer.net iş alarmlarından son 24 saatin ilanları — puana göre sıralı
  3) Elle başvuru için ön yazısı hazırlananlar

Gerekli ortam değişkenleri: GMAIL_ADRES, GMAIL_UYGULAMA_SIFRESI, (isteğe bağlı) BILDIRIM_ADRESI
"""
from __future__ import annotations

import html
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

TUR_ADI = {"ilan": "İlan", "acik_basvuru": "Açık başvuru"}
MAVI, YESIL, GRI, KOYU = "#2456c9", "#1f8a4c", "#6b7385", "#1d2433"


def aktif_mi() -> bool:
    return bool(os.environ.get("GMAIL_ADRES") and os.environ.get("GMAIL_UYGULAMA_SIFRESI"))


def _e(s) -> str:
    return html.escape(str(s or ""))


def _renk(puan: int) -> str:
    return YESIL if puan >= 70 else ("#b86a00" if puan >= 55 else GRI)


def icerik(gonderilenler: list[dict], alarm_listesi: list[dict], hazirlar: list[dict], ozet: dict,
           tablo: str | None) -> tuple[str, str, str]:
    tarih = datetime.now().strftime("%d.%m.%Y")
    kuru = ozet.get("kuru")
    n_g = len(gonderilenler)
    parcalar = []
    if n_g:
        parcalar.append(f"{n_g} başvuru {'hazırlandı (gönderilmedi)' if kuru else 'gönderildi'}")
    if alarm_listesi:
        parcalar.append(f"{len(alarm_listesi)} yeni ilan")
    konu = f"{' · '.join(parcalar) or 'Bugün yeni ilan yok'} · {tarih}"

    b = [f'<div style="font-family:Segoe UI,Arial,sans-serif;max-width:680px;margin:auto;color:{KOYU}">',
         f'<h2 style="margin:0 0 4px">Başvuru özeti · {tarih}</h2>']
    kr, ar = ozet.get("kesif", {}), ozet.get("alarm", {})
    b.append(f'<p style="color:{GRI};margin:0 0 18px;font-size:13px">Keşif: {kr.get("sorgu", 0)} arama, '
             f'{kr.get("sayfa", 0)} sayfa incelendi · Alarm e-postası: '
             f'{", ".join(f"{k} {v}" for k, v in ar.items()) or "yok"}</p>')

    baslik = "Hazırlanan başvurular (kuru çalışma — gönderilmedi)" if kuru else "Otomatik gönderilen başvurular"
    b.append(f'<h3 style="margin:18px 0 8px">{baslik} ({n_g})</h3>')
    if gonderilenler:
        for g in gonderilenler:
            b.append(f'<div style="border:1px solid #e3e6eb;border-radius:8px;padding:10px 12px;margin-bottom:8px">'
                     f'<b>{_e(g["sirket"])}</b> · {_e(g["pozisyon"])} '
                     f'<span style="color:{GRI}">({TUR_ADI.get(g["tur"], g["tur"])}, puan {g["puan"]})</span><br>'
                     f'<span style="font-size:13px;color:{GRI}">→ {_e(g["adres"])} · '
                     f'<a href="{_e(g["url"])}" style="color:{MAVI}">kaynak sayfa</a></span></div>')
    else:
        b.append(f'<p style="color:{GRI}">Bugün uygun e-postalı ilan / şirket bulunamadı.</p>')

    b.append(f'<h3 style="margin:22px 0 8px">LinkedIn &amp; Kariyer.net — son 24 saat ({len(alarm_listesi)})</h3>')
    if alarm_listesi:
        b.append('<table style="width:100%;border-collapse:collapse;font-size:14px">')
        for x in alarm_listesi:
            b.append(f'<tr><td style="padding:6px 8px 6px 0;border-top:1px solid #eef0f3;width:34px;font-weight:700;'
                     f'color:{_renk(x["puan"])}">{x["puan"]}</td>'
                     f'<td style="padding:6px 0;border-top:1px solid #eef0f3">'
                     f'<a href="{_e(x["url"])}" style="color:{MAVI};text-decoration:none"><b>{_e(x["pozisyon"])}</b></a>'
                     f' · {_e(x["sirket"])}<br><span style="color:{GRI};font-size:12px">{_e(x["kaynak"])}'
                     f'{" · " + _e(x["konum"]) if x.get("konum") else ""} · {_e(x["gerekce"])}</span></td></tr>')
        b.append('</table>')
    else:
        b.append(f'<p style="color:{GRI}">Alarm e-postası gelmedi ya da yeni ilan yok. LinkedIn ve Kariyer.net\'te iş '
                 f'alarmlarının açık olduğundan emin ol.</p>')

    if hazirlar:
        b.append(f'<h3 style="margin:22px 0 8px">Ön yazısı hazır — elle başvur ({len(hazirlar)})</h3>')
        for h in hazirlar:
            b.append(f'<p style="margin:0 0 6px"><b style="color:{_renk(h["puan"])}">{h["puan"]}</b> '
                     f'<a href="{_e(h["adres"])}" style="color:{MAVI}">{_e(h["pozisyon"])}</a> · {_e(h["sirket"])}</p>')
    if tablo:
        b.append(f'<p style="margin-top:22px"><a href="{_e(tablo)}" style="color:{MAVI}">Takip tablosunu aç</a> '
                 f'<span style="color:{GRI}">(ön yazılar ve başvuru durumları orada)</span></p>')
    b.append("</div>")

    duz = konu + "\n\n" + "\n".join(f"[gönderildi] {g['sirket']} → {g['adres']}" for g in gonderilenler) + "\n\n" + \
        "\n".join(f"{x['puan']} · {x['pozisyon']} · {x['sirket']} · {x['url']}" for x in alarm_listesi)
    return konu, duz, "".join(b)


def gonder(gonderilenler, alarm_listesi, hazirlar, ozet, tablo=None) -> None:
    gonderen = os.environ["GMAIL_ADRES"]
    alici = os.environ.get("BILDIRIM_ADRESI") or gonderen
    konu, duz, govde = icerik(gonderilenler, alarm_listesi, hazirlar, ozet, tablo)
    m = MIMEMultipart("alternative")
    m["Subject"], m["From"], m["To"] = konu, gonderen, alici
    m.attach(MIMEText(duz, "plain", "utf-8"))
    m.attach(MIMEText(govde, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(gonderen, os.environ["GMAIL_UYGULAMA_SIFRESI"].replace(" ", ""))
        smtp.sendmail(gonderen, [alici], m.as_string())
