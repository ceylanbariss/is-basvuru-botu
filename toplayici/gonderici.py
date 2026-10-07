"""Başvuru e-postası gönderimi: ön yazı gövdede, CV ekte, Gmail SMTP üzerinden.
Gmail bu e-postaları 'Gönderilmiş' klasörüne kendisi kaydeder; ne gittiğini oradan görebilirsin."""
from __future__ import annotations

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from pathlib import Path

from ai.eslestirici import cv_dosya_adi

GENEL_SAGLAYICILAR = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yandex.com", "yandex.com.tr",
                      "icloud.com", "live.com", "msn.com", "mail.com", "hotmail.com.tr", "outlook.com.tr"}


def sirket_anahtari(eposta: str) -> str:
    """Tekrar kontrolü için şirket kimliği: kurumsal adreste alan adı, gmail vb. adreste adresin kendisi."""
    alan = eposta.split("@")[-1].lower()
    return eposta.lower() if alan in GENEL_SAGLAYICILAR else alan


def imza(profil: dict) -> str:
    k = profil["kisisel"]
    return f"\n{k['telefon']} · {k['eposta']}\n{k['linkedin']} · {k['github']}"


def gonder(alici: str, konu: str, govde: str, cv_yolu: Path, profil: dict) -> None:
    gonderen = os.environ["GMAIL_ADRES"]
    m = MIMEMultipart()
    m["From"] = formataddr((profil["kisisel"]["ad_soyad"], gonderen))
    m["To"] = alici
    m["Subject"] = konu
    m["Message-ID"] = make_msgid(domain=gonderen.split("@")[-1])
    m.attach(MIMEText(govde + imza(profil), "plain", "utf-8"))
    ek = MIMEApplication(cv_yolu.read_bytes(), _subtype="pdf")
    ek.add_header("Content-Disposition", "attachment", filename=cv_dosya_adi(profil))
    m.attach(ek)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(gonderen, os.environ["GMAIL_UYGULAMA_SIFRESI"].replace(" ", ""))
        smtp.sendmail(gonderen, [alici], m.as_string())
