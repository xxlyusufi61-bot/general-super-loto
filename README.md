GENERAL AI Agent Project

1. Projenin Amacı

GENERAL, Super Loto arşivini araştırmak, verileri doğrulamak, matematiksel modelleri test etmek ve uzman ajanların çalışmalarını koordine etmek için geliştirilen bir yapay zekâ ajan sistemidir.

2. Mevcut Altyapı

- GitHub deposu: "xxlyusufi61-bot/general-super-loto"
- GitHub Actions iş akışı: ".github/workflows/general.yml"
- Telegram bot bağlantısı başarıyla test edilmiştir.
- "TELEGRAM_BOT_TOKEN", GitHub Secrets içinde tutulmaktadır.
- Daha önce kurulmuş AlwaysData hesabı, "heartbeat.py" dosyası ve zamanlanmış görev korunmalıdır.
- Mevcut Heartbeat testi, tam bir yapay zekâ ajanının çalıştığını kanıtlamaz.

3. Bağlayıcı Altyapı Kuralları

- Ücretsiz kullanım hedeflenir.
- Kart doğrulaması isteyen hizmetler yasaktır.
- Otomatik ücretli geçiş veya kontrolsüz ücret riski kabul edilmez.
- Telefon kapalıyken de çalışabilen uzak altyapı hedeflenir.
- GitHub Actions zamanlanmış görevleri sürekli çalışan bir sunucuyla aynı kabul edilmez.
- Çalışan dosyalar ve ayarlar gerekçe ve onay olmadan silinmez veya değiştirilmez.
- API anahtarları kaynak koduna yazılmaz ve sohbetlerde paylaşılmaz.

4. Uzman Ajanlar

- Research: Araştırma ve yöntem keşfi.
- Debug: Hataları bulma ve giderme.
- Validation: Arşiv, veri ve deney sonuçlarını doğrulama.
- Crossover: Modelleri ve yöntemleri karşılaştırma, ağırlıklandırma ve birleştirme.

Ajanların görevleri, veri erişimleri ve birbirleriyle iletişim yöntemleri uygulama planında tanımlanacaktır.

5. Super Loto Analiz Kuralları

- Çekiliş verileri kronolojik olarak işlenir.
- Hedef çekiliş, keşif ve eğitim sürecinde gizli tutulur.
- Hedef tarihinden sonraki veriler hedef değerlendirmesine sızamaz.
- Arşivde eksik ve tekrarlanan çekilişler kontrol edilir.
- A→B→C, A→T ve MOD5→T araştırma yöntemleri korunur.
- Walk-forward değerlendirmesi ve çapraz doğrulama uygulanır.
- Crossover sonuçları yalnızca uygun eğitim ve doğrulama verileriyle belirlenir.
- Önceki kararlar veya kilitli hedefler sessizce değiştirilmez.
- Hiçbir yöntem başarılı sonuç vermeye zorlanmaz; başarısız deneyler de raporlanır.

6. Güvenlik ve Değişiklik Yönetimi

- Bağlayıcı kurallar, kullanıcı onayı olmadan değiştirilemez.
- Önemli mimari değişiklikler uygulanmadan önce gerekçelendirilir.
- Gizli bilgiler günlüklerde veya Telegram mesajlarında yayımlanmaz.
- Her yeni özellik için doğrulama testi hazırlanır.
- Değişiklikler mümkün olduğunda sürüm kontrolüyle izlenir.

7. Geliştirme Aşamaları

1. Mevcut altyapı ve dosyaların denetlenmesi.
2. Eksik gereksinimlerin ve risklerin belirlenmesi.
3. Ücretsiz ve kart doğrulaması gerektirmeyen yapay zekâ altyapısının araştırılması.
4. Ajan mimarisi ve güvenli iletişimin tasarlanması.
5. Arşiv doğrulama ve sızıntısız deney sisteminin kurulması.
6. Uzman ajanların aşamalı uygulanması.
7. Test sonuçlarının ve hataların raporlanması.

8. Önemli Not

Bu belge, projenin başlangıç gereksinimlerini tanımlar. Gerçekleştirilmiş özelliklerle planlanan özellikler birbirinden ayrı raporlanmalıdır. Bir özellik yalnızca test edilip doğrulandığında tamamlanmış sayılır.
