"""İş alarmı ilanlarını tek çağrıda toplu puanlar (her ilan için ayrı çağrı yerine; kota dostu)."""
from __future__ import annotations

from ai.eslestirici import profil_metni

SISTEM = """Sen bir kariyer danışmanısın. Adayın profilini ve numaralı kısa ilan listesini (başlık · şirket · konum)
alırsın. Her ilan için adayın bu ilana başvurmasının ne kadar mantıklı olduğunu puanla (0-100).
Ölçüt: rol uyumu %50 (iş analisti, ERP/SAP danışmanlığı, AI/otomasyon, ürün, junior yazılım/BT rolleri yüksek),
kıdem %30 (senior/lead/manager/müdür/5+ yıl → en fazla 35; stajyer/junior/yeni mezun/uzman yardımcısı → yüksek),
konum %20 (Türkiye'de, adayın şehri, uzaktan veya hibrit uygun).
ÖNEMLİ: Aday makine öğrenmesi mühendisi DEĞİL. Profilinde model eğitimi, derin öğrenme, bilgisayarlı görü, veri bilimi
veya MLOps deneyimi YOK. Bu yüzden "Data Scientist", "Machine Learning Engineer", "Deep Learning", "Computer Vision",
"NLP Engineer", "AI Research", "MLOps" gibi roller en fazla 45 puan alır (başlıkta "AI/Agentic" geçse bile).
Yüksek puan alan AI rolleri: AI otomasyon/entegrasyon, AI ürün/proje, prompt/LLM uygulama, AI destekli iş analizi,
no-code/low-code otomasyon. Aday 2026 MEZUNU: UZUN DÖNEM / tam zamanlı staj ve yeni mezun programları (stajyer, intern,
MT, graduate) normal puanlanır. Sadece "zorunlu staj", "okul stajı" veya açıkça öğrenci şartı (ör. "3. veya 4. sınıf öğrencisi") isteyen ilanlar en fazla 40. Yazılım rollerinde junior/stajyer olmayan backend/full-stack/mobil geliştirici en fazla 55.
Bilgi kısa olduğu için belirsizlikte ortalama puan ver; kesin uyumsuz roller (satış temsilcisi, muhasebe, üretim,
makine/kimya mühendisliği vb.) 25'i geçmesin. gerekce en fazla 12 kelime. Tüm ilanları döndür.
Yalnızca şemaya uyan JSON döndür."""

SEMA = {
    "type": "object",
    "properties": {"sonuclar": {"type": "array", "items": {
        "type": "object",
        "properties": {"no": {"type": "integer"}, "puan": {"type": "integer", "minimum": 0, "maximum": 100},
                       "gerekce": {"type": "string"}},
        "required": ["no", "puan", "gerekce"]}}},
    "required": ["sonuclar"],
}


def toplu_puanla(ilanlar: list, profil: dict, llm, parti: int = 25) -> dict[str, tuple[int, str]]:
    """{ilan.id: (puan, gerekçe)} döndürür. Cevapta eksik kalan ilanlar sonuçta yer almaz."""
    sonuc: dict[str, tuple[int, str]] = {}
    for bas in range(0, len(ilanlar), parti):
        dilim = ilanlar[bas:bas + parti]
        liste = "\n".join(f"{n}. {i.baslik} · {i.sirket} · {i.konum}" for n, i in enumerate(dilim, 1))
        ham = llm.json_uret(SISTEM, f"{profil_metni(profil)}\n\n# İLANLAR\n{liste}", SEMA)
        for s in ham.get("sonuclar", []):
            n = s.get("no")
            if isinstance(n, int) and 1 <= n <= len(dilim):
                sonuc[dilim[n - 1].id] = (max(0, min(100, int(s.get("puan", 0)))), s.get("gerekce", ""))
    return sonuc


class SahteTopluLLM:
    def json_uret(self, sistem, girdi, sema):
        satirlar = [s for s in girdi.split("# İLANLAR", 1)[-1].strip().splitlines() if s.strip()]
        return {"sonuclar": [{"no": n, "puan": 30 if "Senior" in s else 75, "gerekce": "test"}
                             for n, s in enumerate(satirlar, 1)]}
