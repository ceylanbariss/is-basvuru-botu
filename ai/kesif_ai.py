"""Keşif AI: bir web sayfasını okuyup
  - 'ilan'          → e-postayla başvuru alan açık bir pozisyon var
  - 'acik_basvuru'  → şirketin kendi sitesi/İK sayfası; açık başvuru gönderilebilir
  - 'alakasiz'      → başvuru yapılmaz
olarak sınıflandırır; uygunsa adres seçer ve ön yazı yazar. Adres SADECE sayfada bulunanlardan seçilebilir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ai.eslestirici import on_yazi_duzelt, profil_metni

SISTEM = """Sen bir kariyer danışmanısın. Adayın profilini ve bir web sayfasının içeriğini alırsın. Çıktı dili Türkçe.

GÖREV: Sayfayı sınıflandır ve uygunsa adayın bu sayfadaki adrese e-postayla başvurmasını hazırla.

ADAY DURUMU: 2026 mezunu (öğrenci DEĞİL). Hedef roller: iş analisti, sistem/süreç analisti, iş zekası/veri analisti,
ERP/SAP danışmanı veya uzmanı, AI/otomasyon ve AI destekli yazılım geliştirme, BT/bilgi işlem uzmanı,
dijital dönüşüm, ürün/proje uzmanı (yardımcısı), junior yazılım. BUNLARIN DIŞINDAKİ ROLLER "alakasiz"dır:
içerik/sosyal medya, pazarlama, satış, muhasebe/finans, operasyon/stok/lojistik, üretim, grafik tasarım, İK vb.

tur:
- "ilan": Sayfada belirli bir AÇIK POZİSYON var ve başvurular e-postayla alınıyor.
- "acik_basvuru": Sayfa bir ŞİRKETİN / kurumun kendi sitesi (kariyer/İK/iletişim sayfası). Sektör kısıtı yok:
  teknoloji şirketleri kadar hastane, holding, üretim, perakende gibi BT/iş analizi ihtiyacı olabilecek her
  kurumsal firma uygundur. Açık ilan olmasa da İK adresine açık başvuru gönderilebilir.
  ŞART: Sayfada şirketin KENDİSİ İÇİN eleman aradığını gösteren bir ifade olmalı ("ekibimize katılın",
  "açık pozisyonlar", "aramıza katıl", "kariyer fırsatları", "özgeçmişinizi ... adresine gönderin" gibi).
  İSTİSNA — açık başvuru için "alakasiz": ürünü/hizmeti kariyer, CV/özgeçmiş hazırlama, iş eşleştirme, işe alım,
  İK danışmanlığı veya eleman bulma olan şirketler. Bunların sitesindeki "kariyer/CV/başvuru" kelimeleri
  müşterilerine yönelik ürün tanıtımıdır, işe alım değildir.
- "alakasiz": İş ilanı sitesi/liste sayfası, haber, forum, blog, kişisel sayfa, eğitim kursu, ilan süresi geçmiş,
  adayın alanıyla ilgisiz ROL (sektör değil), stajyer değil yalnızca 5+ yıl deneyim isteyen üst düzey rol,
  Türkiye dışında çalışmayı gerektiren (uzaktan olmayan) ilan/şirket, üniversite/okul/kamu kurumu sayfaları,
  yalnızca zorunlu/okul stajı ilanları ("zorunlu staj", "yaz stajı" gibi okul kredisi gerektirenler). Uzun dönem
  ve tam zamanlı staj ilanları UYGUNDUR (aday mezun ama uzun dönem staja açık),
  başka bir kurumun (üniversite kariyer merkezi, ilan platformu, danışmanlık aracısı) yayınladığı ilanlar vb.

KURALLAR
1. Profilde olmayan hiçbir deneyim, beceri, sayı veya başarı UYDURMA. Staj fiillerini güçlendirme.
2. eposta: SADECE verilen aday listesinden seç; adres işe alan ŞİRKETE ait olmalı (alan adı şirketin sitesi ya da
   ilanda açıkça "başvurular şu adrese" diye verilmiş adres). Kurumsal İK/kariyer adresini tercih et (ik@, hr@,
   kariyer@, cv@). Muhasebe, satış, destek gibi başka birimlerin adreslerini seçme.
   Belirli bir kişiye ait görünen ve ilanla ilgisi olmayan adresleri seçme. Uygun adres yoksa "" ve tur "alakasiz".
3. puan (0-100): ilan için rol+beceri+kıdem uyumu; açık başvuru için şirketin adayın alanına uygunluğu.
   5+ yıl gibi açıkça karşılanmayan şart varsa 45'i geçmesin.
4. konu: e-posta konu satırı. İlan: "<Pozisyon> Başvurusu – <adayın adı soyadı>".
   Açık başvuru: "Açık Başvuru – <en uygun rol> – <adayın adı soyadı>".
5. on_yazi: 120-180 kelime, "Merhaba," ile başla, 3 kısa paragraf, sonunda "Saygılarımla," ve adayın adı.
   İlanda: ilanın 2 somut gereksinimini profildeki 2 somut deneyimle eşleştir.
   Açık başvuruda: şirketin sayfada yazan faaliyet alanına değin (sayfada yazmayan bilgi uydurma), hangi rollere
   (iş analisti, ERP/Odoo danışmanı, AI destekli geliştirme) katkı verebileceğini 2 somut deneyimle anlat,
   uygun bir pozisyon açıldığında değerlendirilmeyi rica et. CV'nin ekte olduğunu belirt.
   Yasak: "öğrenme tutkusu", "katma değer", "dinamik", "vizyoner", "tutkulu", "inanıyorum", "yakından takip".
6. secilen_cv: CV havuzundan id.
7. turkiyede: şirket/iş Türkiye'de mi (ya da Türkiye'den uzaktan çalışılabilir mi)? Yurt dışı merkezli ve Türkiye'de
   ofisi/uzaktan seçeneği görünmeyen şirket → false.
8. gercek_kurum: sayfa gerçek bir şirkete/kuruma mı ait? Bir kişinin kendi sitesi, portföyü, blogu, freelancer
   sayfası → false.
9. kariyer_urunu: şirketin ana ürünü/hizmeti kariyer, CV, işe alım, iş eşleştirme veya İK danışmanlığı mı? → true.
Yalnızca şemaya uyan JSON döndür."""


def sema(profil: dict, adresler: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "tur": {"type": "string", "enum": ["ilan", "acik_basvuru", "alakasiz"]},
            "sirket": {"type": "string"},
            "pozisyon": {"type": "string"},
            "puan": {"type": "integer", "minimum": 0, "maximum": 100},
            "gerekce": {"type": "string"},
            "eposta": {"type": "string", "enum": adresler + [""]},
            "secilen_cv": {"type": "string", "enum": [c["id"] for c in profil["cv_havuzu"]]},
            "konu": {"type": "string"},
            "on_yazi": {"type": "string"},
            "turkiyede": {"type": "boolean"},
            "kariyer_urunu": {"type": "boolean"},
            "gercek_kurum": {"type": "boolean"},
        },
        "required": ["tur", "sirket", "pozisyon", "puan", "gerekce", "eposta", "secilen_cv", "konu", "on_yazi", "turkiyede", "gercek_kurum", "kariyer_urunu"],
    }


@dataclass
class KesifSonucu:
    tur: str
    sirket: str
    pozisyon: str
    puan: int
    gerekce: str
    eposta: str
    secilen_cv: str
    konu: str
    on_yazi: str
    uyarilar: list[str] = field(default_factory=list)


GENEL_SAGLAYICI = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "yandex.com", "yandex.com.tr",
                   "icloud.com", "live.com", "hotmail.com.tr", "outlook.com.tr"}


def _kok_alan(alan: str) -> str:
    """firma.com.tr / www.firma.com → firma"""
    parcalar = alan.lower().removeprefix("www.").split(".")
    while len(parcalar) > 1 and parcalar[-1] in {"com", "net", "org", "tr", "io", "ai", "co", "info", "biz", "online", "tech", "app", "dev"}:
        parcalar.pop()
    return parcalar[-1] if parcalar else alan


ISE_ALIM_SINYALLERI = ("ekibimize katıl", "ekibimize katil", "aramıza katıl", "aramiza katil", "bize katıl",
                       "açık pozisyon", "acik pozisyon", "kariyer fırsat", "kariyer firsat", "iş fırsat",
                       "özgeçmişinizi", "ozgecmisinizi", "cv'nizi", "cv’nizi", "cvnizi", "başvurunuzu", "basvurunuzu",
                       "join our team", "we're hiring", "we are hiring", "open positions", "careers at",
                       "insan kaynakları politika", "işe alım süreci", "ise alim sureci")


def ise_alim_sinyali(aday) -> bool:
    metin = f"{aday.baslik} {aday.ozet} {aday.metin}".lower()
    return any(s in metin for s in ISE_ALIM_SINYALLERI)


def alan_uyumlu(eposta: str, site_alan: str) -> bool:
    e_alan = eposta.split("@")[-1].lower()
    if e_alan in GENEL_SAGLAYICI:
        return True
    return _kok_alan(e_alan) == _kok_alan(site_alan)


def degerlendir(aday, profil: dict, llm) -> KesifSonucu:
    girdi = f"{profil_metni(profil)}\n\n# WEB SAYFASI\n{aday.ai_metni()}"
    ham = llm.json_uret(SISTEM, girdi, sema(profil, aday.epostalar))
    uyarilar: list[str] = []
    eposta = (ham.get("eposta") or "").strip().lower()
    if eposta and eposta not in aday.epostalar:          # güvenlik: sayfada olmayan adres asla
        uyarilar.append(f"sayfada olmayan adres atıldı: {eposta}")
        eposta = ""
    tur = ham.get("tur", "alakasiz")
    if not eposta:
        tur = "alakasiz"
    if ham.get("turkiyede") is False:
        uyarilar.append("Türkiye dışı"); tur = "alakasiz"
    if ham.get("gercek_kurum") is False:
        uyarilar.append("kişisel site"); tur = "alakasiz"
    if tur == "acik_basvuru" and ham.get("kariyer_urunu") is True:
        uyarilar.append("kariyer/İK ürünü şirketi"); tur = "alakasiz"
    if tur == "acik_basvuru" and not ise_alim_sinyali(aday):
        uyarilar.append("sayfada işe alım ifadesi yok"); tur = "alakasiz"
    if tur == "acik_basvuru" and eposta and not alan_uyumlu(eposta, aday.alan):
        uyarilar.append(f"alan adı uyuşmuyor: {eposta} / {aday.alan}"); tur = "alakasiz"
    cv_idler = {c["id"] for c in profil["cv_havuzu"]}
    cv = ham.get("secilen_cv") if ham.get("secilen_cv") in cv_idler else profil["cv_havuzu"][0]["id"]
    ad = profil["kisisel"]["ad_soyad"]
    konu = (ham.get("konu") or "").strip() or f"Açık Başvuru – {ad}"
    if ad.split()[0] not in konu:
        konu = f"{konu} – {ad}"
    return KesifSonucu(
        tur=tur, sirket=ham.get("sirket", "").strip(), pozisyon=ham.get("pozisyon", "").strip(),
        puan=max(0, min(100, int(ham.get("puan", 0)))), gerekce=ham.get("gerekce", ""), eposta=eposta,
        secilen_cv=cv, konu=re.sub(r"\s+", " ", konu)[:150],
        on_yazi=on_yazi_duzelt(ham.get("on_yazi", ""), profil, uyarilar), uyarilar=uyarilar,
    )


class SahteKesifLLM:
    """Test: sayfadaki ilk adresi seçer; 'kimya' geçen sayfaları alakasız sayar."""
    def json_uret(self, sistem: str, girdi: str, sema_: dict) -> dict:
        adresler = [a for a in sema_["properties"]["eposta"]["enum"] if a]
        sayfa = girdi.split("# WEB SAYFASI", 1)[-1]
        acik = "kariyer" in sayfa.lower() and "ilan" not in sayfa.lower()
        return {"tur": "alakasiz" if "kimya" in sayfa.lower() else ("acik_basvuru" if acik else "ilan"),
                "sirket": "Test A.Ş.", "pozisyon": "İş Analisti", "puan": 80, "gerekce": "test",
                "eposta": adresler[0] if adresler else "", "secilen_cv": "fotografli",
                "konu": "İş Analisti Başvurusu", "turkiyede": True, "gercek_kurum": True, "kariyer_urunu": False, "on_yazi": "Merhaba,\n\nA.\n\nB.\n\nC.\n\nSaygılarımla,"}
