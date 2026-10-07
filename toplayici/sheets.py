"""Google Sheets takip tablosu. Hazır başvurular her gün tabloya satır olarak eklenir;
"Durum" sütunundan (açılır liste) başvuru takibi yapılır.

Gerekli ortam değişkenleri:
    GOOGLE_SA_JSON  – service account anahtar dosyasının TÜM içeriği (JSON metni)
    SHEET_ID        – tablonun linkindeki /d/ ile /edit arasındaki kimlik
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.parse import quote

API = "https://sheets.googleapis.com/v4/spreadsheets"
SAYFA = "Başvurular"
BASLIKLAR = ["Tarih", "Puan", "Şirket", "Pozisyon", "Kanal", "İlan", "CV", "Gerekçe", "Eksikler", "Ön yazı", "Durum", "Not"]
DURUMLAR = ["Yeni", "Başvurdum", "Dönüş geldi", "Mülakat", "Teklif", "Red", "Başvurmayacağım"]
DURUM_SUTUNU = BASLIKLAR.index("Durum")
KANAL_ADI = {"eposta": "E-posta", "ats_form": "Form", "manuel": "Elle", "eposta_otomatik": "E-posta (otomatik)"}


def aktif_mi() -> bool:
    return bool(os.environ.get("GOOGLE_SA_JSON") and os.environ.get("SHEET_ID"))


def _oturum():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    bilgi = json.loads(os.environ["GOOGLE_SA_JSON"])
    kimlik = service_account.Credentials.from_service_account_info(
        bilgi, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return AuthorizedSession(kimlik)


def _kontrol(r):
    if r.status_code >= 400:
        raise RuntimeError(f"Sheets API {r.status_code}: {r.text[:300]}")
    return r.json() if r.content else {}


def _sayfa_hazirla(s, sid: str) -> None:
    """'Başvurular' sayfası yoksa oluşturur: başlık satırı, dondurma, Durum açılır listesi."""
    meta = _kontrol(s.get(f"{API}/{sid}", params={"fields": "sheets.properties"}))
    if any(sh["properties"]["title"] == SAYFA for sh in meta.get("sheets", [])):
        return
    cevap = _kontrol(s.post(f"{API}/{sid}:batchUpdate", json={"requests": [{"addSheet": {"properties": {
        "title": SAYFA, "gridProperties": {"frozenRowCount": 1}}}}]}))
    sayfa_id = cevap["replies"][0]["addSheet"]["properties"]["sheetId"]
    aralik = quote(f"'{SAYFA}'!A1")
    _kontrol(s.put(f"{API}/{sid}/values/{aralik}", params={"valueInputOption": "RAW"}, json={"values": [BASLIKLAR]}))
    _kontrol(s.post(f"{API}/{sid}:batchUpdate", json={"requests": [
        {"setDataValidation": {
            "range": {"sheetId": sayfa_id, "startRowIndex": 1, "endRowIndex": 5000,
                      "startColumnIndex": DURUM_SUTUNU, "endColumnIndex": DURUM_SUTUNU + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": d} for d in DURUMLAR]},
                     "strict": True, "showCustomUi": True}}},
        {"repeatCell": {"range": {"sheetId": sayfa_id, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                        "fields": "userEnteredFormat.textFormat.bold"}},
    ]}))


def _link(url: str) -> str:
    # Formül yerine düz link: HYPERLINK'in ayırıcısı tablo diline göre değişiyor (TR'de ";"), düz URL her yerde tıklanır.
    return url or ""


def _metin(s: str) -> str:
    s = s or ""
    return "'" + s if s[:1] in "=+-@" else s   # formül olarak yorumlanmasın


def satirlar_ekle(basvurular: list[dict]) -> int:
    """basvurular: [{puan, sirket, pozisyon, kanal, adres, secilen_cv, gerekce, eksikler, on_yazi}]"""
    if not basvurular:
        return 0
    s, sid = _oturum(), os.environ["SHEET_ID"]
    _sayfa_hazirla(s, sid)
    bugun = datetime.now().strftime("%d.%m.%Y")
    satirlar = [[bugun, b["puan"], _metin(b["sirket"]), _metin(b["pozisyon"]), KANAL_ADI.get(b["kanal"], b["kanal"]),
                 _link(b.get("url") or b["adres"]), b["secilen_cv"], _metin(b["gerekce"]),
                 _metin("; ".join(b.get("eksikler") or [])), _metin(b["on_yazi"]), "Yeni" if b.get("durum") == "Taslak" else b.get("durum", "Yeni"),
                 _metin(f"Gönderildi → {b['adres']}" if b.get("kanal") == "eposta_otomatik" and b.get("durum") == "Başvurdum"
                        else ("Taslak, gönderilmedi → " + b["adres"] if b.get("durum") == "Taslak"
                              else ("Açık başvuru" if b.get("tur") == "acik_basvuru" else "")))]
                for b in basvurular]
    aralik = quote(f"'{SAYFA}'!A1")
    _kontrol(s.post(f"{API}/{sid}/values/{aralik}:append",
                    params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
                    json={"values": satirlar}))
    return len(satirlar)


def tablo_linki() -> str:
    return f"https://docs.google.com/spreadsheets/d/{os.environ.get('SHEET_ID', '')}/edit"
