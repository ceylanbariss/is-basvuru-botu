"""AI eşleştirici: ilan metni + profil  →  puan, havuzdan CV seçimi, ön yazı.

CV ÜRETMEZ. profil.yaml içindeki cv_havuzu'ndan ilana en uygun hazır CV'yi seçer.

Kullanım:
    python -m ai.eslestirici ilan.txt            # gerçek (Gemini)
    python -m ai.eslestirici ilan.txt --sahte    # API'siz test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

KOK = Path(__file__).resolve().parent.parent
HAVUZ = KOK / "cv_havuzu"

try:
    from dotenv import load_dotenv
    load_dotenv(KOK / ".env")
except ImportError:
    pass


def cv_dosya_adi(profil: dict) -> str:
    """'Deniz Yılmaz' → 'Deniz_Yilmaz_CV.pdf' (e-posta ekleri için ASCII)."""
    tablo = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    ad = re.sub(r"[^A-Za-z0-9]+", "_", profil["kisisel"]["ad_soyad"].translate(tablo)).strip("_")
    return f"{ad}_CV.pdf"


def profil_yukle(yol: Path = KOK / "profil.yaml") -> dict:
    if not yol.exists() and (KOK / "profil.ornek.yaml").exists():   # demo: kendi profilin yoksa örnek profil
        yol = KOK / "profil.ornek.yaml"
    return yaml.safe_load(yol.read_text(encoding="utf-8"))


def sema_olustur(profil: dict) -> dict:
    return {
        "type": "object",
        "properties": {
            "pozisyon": {"type": "string"},
            "sirket": {"type": "string"},
            "kidem": {"type": "string", "enum": ["stajyer", "yeni_mezun", "junior", "mid", "senior", "belirsiz"]},
            "ilan_dili": {"type": "string", "enum": ["tr", "en", "diger"]},
            "puan": {"type": "integer", "minimum": 0, "maximum": 100},
            "gerekce": {"type": "string"},
            "eksikler": {"type": "array", "items": {"type": "string"}},
            "secilen_cv": {"type": "string", "enum": [c["id"] for c in profil["cv_havuzu"]]},
            "cv_gerekce": {"type": "string"},
            "on_yazi": {"type": "string"},
            "basvuru_kanali": {"type": "string", "enum": ["eposta", "form", "bilinmiyor"]},
            "basvuru_adresi": {"type": "string"},
        },
        "required": ["pozisyon", "sirket", "kidem", "ilan_dili", "puan", "gerekce", "secilen_cv",
                     "cv_gerekce", "on_yazi", "basvuru_kanali", "basvuru_adresi"],
    }


SISTEM = """Sen bir kariyer danışmanı ve ATS uzmanısın. Adayın profilini, hazır CV havuzunu ve bir iş ilanını alırsın.
Çıktı dili her zaman Türkçe.

KESİN KURALLAR
1. Profilde olmayan hiçbir deneyim, beceri, araç, sayı veya başarı UYDURMA.
2. puan (0-100): rol uyumu %40, beceri/araç uyumu %30, kıdem uyumu %20, konum/çalışma şekli uyumu %10.
   Adayın temel bir gereksinimi (ör. 5+ yıl, zorunlu teknoloji) açıkça karşılamadığı ilanlarda puan 50'yi geçmesin.
   İlan metni "[Arama özeti" ile başlıyorsa bilgi azdır: başlık ve özetten puanla, belirsizlik için puanı
   düşürme, ama özette açıkça uyumsuz bir şart (ör. senior, 5+ yıl) varsa uygula.
   "Analist", "Uzman", "Specialist" gibi belirsiz unvanlarda alanı şirket ve özetten çıkar: laboratuvar, kimya,
   finans, kredi, pazarlama analisti gibi IT/iş analizi DIŞI bir rolse puan 40'ı geçmesin.
   sirket ve pozisyon alanlarını temiz yaz (tarih, "İş İlanı", site adı olmadan).
3. eksikler: ilanın isteyip adayda olmayan önemli şeyler (dürüst, kısa).
4. secilen_cv: CV havuzundaki açıklamalara bakarak bu ilana en uygun CV'nin id'si. cv_gerekce: tek cümle.
5. on_yazi: 140-190 kelime, Türkçe, sade ve samimi-profesyonel ton. Yapı (paragraflar arasında boş satır):
   - Hitap: "Merhaba," (ilanda isim varsa "Merhaba [Ad] Hanım/Bey,")
   - 1. paragraf (1-2 cümle): hangi pozisyona başvurduğu ve ilanın hangi somut ihtiyacının ilgisini çektiği.
   - 2. paragraf (en fazla 4 cümle): ilanın 2 somut gereksinimini profildeki 2 somut deneyimle eşleştir.
   - 3. paragraf (1-2 cümle): kısa kapanış ve görüşme talebi.
   - Son satır: "Saygılarımla," ve bir alt satırda adayın adı soyadı.
   DOĞRULUK: Her iddia profildeki bir maddeye dayanmalı ve o maddenin gücünü AŞMAMALI.
   Stajlarda "gözlemledim/destekledim/yer aldım" gibi fiiller profildeyse "yönettim/liderlik ettim/köprü kurdum"
   gibi daha güçlü fiillere ÇEVİRME. Profilde olmayan "hakimiyet", "uzmanlık", "yıllık deneyim" iddiası kurma.
   "Beceriler" listesindeki araçları (ör. Postman, Swagger) belirli bir işe/staja BAĞLAMA; bir iş deneyiminde
   yalnızca o deneyimin maddelerinde geçenleri kullan. Profilde geçmeyen kavram (ör. kullanıcı hikayesi, UAT) kullanma.
   İmzada adayın gerçek adını yaz, asla [Ad Soyad] gibi yer tutucu bırakma.
   Şirket hakkında ilanda yazmayan bilgi kullanma; "şirketinizi yakından takip ediyorum" gibi doğrulanamayan cümle kurma.
   YASAK İFADELER: "öğrenme tutkusu", "katma değer", "dinamik", "vizyoner", "sonuç odaklı", "tutkulu",
   "başarıyla", "etkin bir şekilde", "inanıyorum", "hedefliyorum", "yakından takip".
6. basvuru_kanali/basvuru_adresi: ilanda başvuru e-postası varsa "eposta" ve adres; form/link varsa "form" ve URL;
   yoksa "bilinmiyor" ve boş metin.
Yalnızca şemaya uyan JSON döndür."""


@dataclass
class EslesmeSonucu:
    pozisyon: str
    sirket: str
    kidem: str
    ilan_dili: str
    puan: int
    gerekce: str
    secilen_cv: str
    cv_gerekce: str
    on_yazi: str
    basvuru_kanali: str
    basvuru_adresi: str
    eksikler: list[str] = field(default_factory=list)
    uyarilar: list[str] = field(default_factory=list)


def varsayilan_cv(profil: dict, ilan: str, ilan_dili: str) -> str:
    """AI geçersiz seçim yaparsa kural tabanlı yedek."""
    ids = [c["id"] for c in profil["cv_havuzu"]]
    ats = re.search(r"greenhouse|lever\.co|workday|myworkdayjobs|smartrecruiters|successfactors|taleo", ilan, re.I)
    if (ats or ilan_dili != "tr") and "fotografsiz" in ids:
        return "fotografsiz"
    return ids[0]


YASAKLI = ["öğrenme tutku", "katma değer", "dinamik", "vizyoner", "sonuç odaklı", "tutkulu",
           "başarıyla", "etkin bir şekilde", "inanıyorum", "hedefliyorum", "yakından takip"]


def on_yazi_duzelt(metin: str, profil: dict, uyarilar: list[str]) -> str:
    ad = profil["kisisel"]["ad_soyad"]
    metin = re.sub(r"^(Merhaba[^,\n]{0,30},)\s*", r"\1\n\n", metin.strip())
    if metin.count("\n\n") < 2:  # tek blok gelirse cümle gruplarına böl
        cumleler = re.split(r"(?<=[.!?])\s+", metin)
        if len(cumleler) >= 5:
            hitap, _, govde_metin = metin.partition("\n\n") if metin.startswith("Merhaba") else (None, "", metin)
            govde = re.split(r"(?<=[.!?])\s+", govde_metin)
            n = len(govde)
            parcalar = [govde[:2], govde[2:n - 2], govde[n - 2:]]
            metin = ((hitap + "\n\n") if hitap else "") + "\n\n".join(" ".join(p) for p in parcalar if p)
            uyarilar.append("ön yazı paragraflara bölündü")
    metin = re.sub(r"\[[^\]]*(ad|isim|name)[^\]]*\]", ad, metin, flags=re.I)
    govde = re.split(r"\n\s*(Saygılarımla|Saygılarımla,|Saygılar)\b", metin, maxsplit=1)[0].rstrip()
    if govde.rstrip(" ,.").endswith(ad):  # imza "Saygılarımla" olmadan gelmişse
        govde = govde[: govde.rfind(ad)].rstrip()
    govde = re.sub(r"\s*(Saygılarımla|Saygılar)[.,]?\s*$", "", govde).rstrip()   # cümle sonuna yapışmış kapanış
    if govde and govde[-1] not in ".!?":
        govde += "."
    yeni = f"{govde}\n\nSaygılarımla,\n{ad}"
    if yeni != metin:
        uyarilar.append("imza düzeltildi")
    metin = yeni
    kelime = len(metin.split())
    if kelime > 210:
        uyarilar.append(f"ön yazı uzun ({kelime} kelime)")
    bulunan = [y for y in YASAKLI if y in metin.lower()]
    if bulunan:
        uyarilar.append("ön yazıda yasaklı ifade: " + ", ".join(bulunan))
    return metin


def dogrula(ham: dict, profil: dict, ilan: str) -> EslesmeSonucu:
    uyarilar: list[str] = []
    ids = {c["id"] for c in profil["cv_havuzu"]}
    cv = ham.get("secilen_cv")
    if cv not in ids:
        cv = varsayilan_cv(profil, ilan, ham.get("ilan_dili", "tr"))
        uyarilar.append(f"geçersiz CV seçimi, yedek kural kullanıldı: {cv}")
    on_yazi = on_yazi_duzelt(ham.get("on_yazi", ""), profil, uyarilar)
    return EslesmeSonucu(
        pozisyon=ham.get("pozisyon", ""), sirket=ham.get("sirket", ""), kidem=ham.get("kidem", "belirsiz"),
        ilan_dili=ham.get("ilan_dili", "tr"), puan=max(0, min(100, int(ham.get("puan", 0)))),
        gerekce=ham.get("gerekce", ""), secilen_cv=cv, cv_gerekce=ham.get("cv_gerekce", ""),
        on_yazi=on_yazi, basvuru_kanali=ham.get("basvuru_kanali", "bilinmiyor"),
        basvuru_adresi=ham.get("basvuru_adresi", ""), eksikler=ham.get("eksikler", []), uyarilar=uyarilar,
    )


# ---------------------------------------------------------------- LLM sağlayıcıları
class LLM(Protocol):
    def json_uret(self, sistem: str, girdi: str, sema: dict) -> dict: ...


class GeminiLLM:
    def __init__(self, model: str | None = None):
        from google import genai
        from google.genai import types
        self.types = types
        self.client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-3-flash-preview")

    def json_uret(self, sistem: str, girdi: str, sema: dict) -> dict:
        t = self.types
        try:
            config = t.GenerateContentConfig(system_instruction=sistem, temperature=0.3,
                                             response_mime_type="application/json", response_json_schema=sema)
        except Exception:
            config = t.GenerateContentConfig(system_instruction=sistem + "\nŞema:\n" + json.dumps(sema),
                                             temperature=0.3, response_mime_type="application/json")
        for deneme in range(4):
            try:
                yanit = self.client.models.generate_content(model=self.model, contents=girdi, config=config)
                return _json_ayikla(yanit.text)
            except Exception as e:
                gecici = any(k in str(e) for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded"))
                if not gecici or deneme == 3:
                    raise
                bekle = 15 * (2 ** deneme)   # 15, 30, 60 sn
                print(f"    Gemini yoğun, {bekle} sn sonra tekrar deneniyor...")
                time.sleep(bekle)


class SahteLLM:
    def json_uret(self, sistem: str, girdi: str, sema: dict) -> dict:
        ilan = girdi.split("# İŞ İLANI", 1)[-1]
        poz = re.search(r"Pozisyon: (.+)", ilan)
        sir = re.search(r"Şirket: (.+)", ilan)
        return {
            "pozisyon": poz.group(1) if poz else "Odoo Fonksiyonel Danışman",
            "sirket": sir.group(1) if sir else "Örnek Yazılım A.Ş.", "kidem": "junior",
            "ilan_dili": "tr", "puan": 30 if "Senior" in ilan else 84, "gerekce": "Odoo 19 modül deneyimi ve veri göçü doğrudan eşleşiyor.",
            "eksikler": ["Muhasebe modülü deneyimi"], "secilen_cv": "fotografli",
            "cv_gerekce": "Türk şirketine doğrudan e-posta başvurusu.",
            "on_yazi": "Merhaba,\n\n...\n\nSaygılarımla,",
            "basvuru_kanali": "eposta", "basvuru_adresi": "ik@ornek.com.tr",
        }


def _json_ayikla(metin: str) -> dict:
    metin = re.sub(r"^```(?:json)?|```$", "", metin.strip(), flags=re.M).strip()
    return json.loads(metin)


# ---------------------------------------------------------------- ana akış
def profil_metni(profil: dict) -> str:
    s = ["# ADAY PROFİLİ", f"Ad Soyad: {profil['kisisel']['ad_soyad']}", f"Özet: {profil['ozet'].strip()}", "", "## Deneyimler"]
    for d in profil["deneyimler"]:
        s.append(f"- {d['rol']} – {d['sirket']} ({d['tarih']})")
        s += [f"    • {m['metin']}" for m in d["maddeler"]]
    s += ["", "## Beceriler", ", ".join(b for kat in profil["beceriler"].values() for b in kat)]
    s += ["", "## Eğitim ve sertifikalar"] + [f"- {e['bolum']}, {e['okul']} ({e['mezuniyet']})" for e in profil["egitim"]]
    s += [f"- {x['ad']}" for x in profil["sertifikalar"]]
    s += ["", "## Diller"] + [f"- {d['dil']}: {d['seviye']}" for d in profil["diller"]]
    s += ["", f"## Tercihler\nÇalışma şekli: {', '.join(profil['tercihler']['calisma_sekli'])} | Konum: {profil['kisisel']['konum']}"]
    s += ["", "# CV HAVUZU"] + [f"- id: {c['id']} → {c['aciklama'].strip()}" for c in profil["cv_havuzu"]]
    return "\n".join(s)


def eslestir(ilan: str, profil: dict, llm: LLM) -> EslesmeSonucu:
    girdi = f"{profil_metni(profil)}\n\n# İŞ İLANI\n{ilan.strip()}"
    sonuc = dogrula(llm.json_uret(SISTEM, girdi, sema_olustur(profil)), profil, ilan)
    yasakli = [u for u in sonuc.uyarilar if u.startswith("ön yazıda yasaklı")]
    if yasakli:  # tek seferlik düzeltme turu
        geri = (f"{girdi}\n\n# DÜZELTME\nÖnceki ön yazın şu kuralı ihlal etti: {yasakli[0]}. "
                f"Önceki ön yazı:\n{sonuc.on_yazi}\nKurallara tam uyarak tüm JSON'u yeniden üret.")
        sonuc = dogrula(llm.json_uret(SISTEM, geri, sema_olustur(profil)), profil, ilan)
    return sonuc


def havuzu_kontrol_et(profil: dict) -> None:
    eksik = [c["dosya"] for c in profil["cv_havuzu"] if not (HAVUZ / c["dosya"]).exists()]
    if eksik:
        raise SystemExit(f"cv_havuzu klasöründe bulunamadı: {', '.join(eksik)}")


def basvuru_hazirla(sonuc: EslesmeSonucu, profil: dict, ek: dict | None = None) -> Path:
    klasor = KOK / "cikti" / _dosya_adi(f"{sonuc.sirket}_{sonuc.pozisyon}")
    klasor.mkdir(parents=True, exist_ok=True)
    kaynak = next(c for c in profil["cv_havuzu"] if c["id"] == sonuc.secilen_cv)
    shutil.copy(HAVUZ / kaynak["dosya"], klasor / cv_dosya_adi(profil))
    (klasor / "on_yazi.txt").write_text(sonuc.on_yazi, encoding="utf-8")
    veri = {**sonuc.__dict__, **(ek or {})}
    (klasor / "sonuc.json").write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")
    return klasor


def _dosya_adi(s: str) -> str:
    return re.sub(r"[^\w]+", "_", s, flags=re.UNICODE).strip("_")[:40] or "ilan"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("ilan_dosyasi", type=Path)
    ap.add_argument("--sahte", action="store_true", help="API çağırmadan test et")
    a = ap.parse_args()

    profil = profil_yukle()
    havuzu_kontrol_et(profil)
    ilan = a.ilan_dosyasi.read_text(encoding="utf-8")
    sonuc = eslestir(ilan, profil, SahteLLM() if a.sahte else GeminiLLM())

    print(f"{sonuc.sirket} – {sonuc.pozisyon} ({sonuc.kidem})  PUAN: {sonuc.puan}")
    print("Gerekçe:", sonuc.gerekce)
    if sonuc.eksikler: print("Eksikler:", "; ".join(sonuc.eksikler))
    if sonuc.uyarilar: print("Doğrulama:", "; ".join(sonuc.uyarilar))
    print(f"Seçilen CV: {sonuc.secilen_cv} ({sonuc.cv_gerekce})")
    print(f"Başvuru: {sonuc.basvuru_kanali} {sonuc.basvuru_adresi}")

    esik = profil["tercihler"]["min_eslesme_puani"]
    if sonuc.puan < esik:
        print(f"Eşik ({esik}) altında, başvuru hazırlanmadı.")
    else:
        print(f"Hazır → {basvuru_hazirla(sonuc, profil)}")
