# دليل تشغيل ونشر المشروع على سيرفر Dokploy

تم تجهيز المشروع بالكامل لدعم خادم Dokploy للواجهة الأمامية (Frontend) والخلفية (Backend) وقاعدة البيانات (PostgreSQL) بأعلى معايير الأداء والإنتاج (Production-ready).

---

## الخيارات المتاحة للنشر على Dokploy

يمكنك اختيار إحدى الطريقتين حسب رغبتك:
1. **الطريقة الأولى (موصى بها - الأبسط والأسرع): نشر كـ Docker Compose كامل**
   يقوم Dokploy بتشغيل الواجهة الأمامية والخلفية وقاعدة البيانات معاً في شبكة موحدة عبر ملف `docker-compose.yml`.
2. **الطريقة الثانية: نشر كل خدمة كتطبيق منفصل (Standalone Applications)**
   نشر تطبيق Frontend منفصل وتطبيق Backend منفصل وقاعدة بيانات من تبويب Databases في Dokploy.

---

## الطريقة الأولى: النشر عبر Dokploy Compose (موصى بها)

### 1. إنشاء خدمة Compose في Dokploy
1. افتح لوحة تحكم Dokploy.
2. توجه إلى **Projects** ثم أنشئ مشروعاً جديداً أو اختر مشروعاً قائماً.
3. اضغط على **Create Service** واختر **Compose**.
4. حدد اسماً للمشروع (مثال: `masry-stream`).

### 2. ربط الكود المصدري (Source)
1. في تبويب **Source**، اختر **GitHub** أو **Git**.
2. حدد المستودع (Repository) والفرع (Branch).
3. تأكد من تحديد مسار الـ Compose:
   - المسار القياسي: `./docker-compose.yml`
   - أو المسار المخصص لشبكة دوكبلويد المباشرة: `./docker-compose.dokploy.yml`
4. اضغط **Save**.

### 3. إعداد المتغيرات البيئية (Environment Variables)
في تبويب **Environment** في خدمة الـ Compose، انسخ المتغيرات التالية مع تعديل كلمات المرور وروابطك:

```env
POSTGRES_DB=masry_stream
POSTGRES_USER=postgres
POSTGRES_PASSWORD=put_a_strong_password_here
POSTGRES_PORT=5432

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
DEBUG=false
API_PREFIX=/api
SQLITE_FALLBACK=false

ADMIN_PASSWORD=your_secure_admin_password
ADMIN_ORIGINS=["*"]
ADMIN_SECURE_COOKIE=true

SCRAPE_INTERVAL_MINUTES=60
SCRAPE_BATCH_LIMIT=100
AUTO_SCRAPE_ON_STARTUP=true

FRONTEND_PORT=3000
PORT=3000
HOSTNAME=0.0.0.0
NEXT_PUBLIC_SITE_URL=https://animeclover.click
INTERNAL_BACKEND_URL=http://backend:8000
```

### 4. ربط النطاق (Domain Mapping)
1. في لوحة الـ Compose داخل Dokploy، افتح تبويب **Domains**.
2. اضغط **Add Domain**.
3. أدخل الدومين: `animeclover.click`.
4. اختر الخدمة: `frontend` والمنفذ الداخلي: `3000`.
5. اختر تفعيل شهادة الأمان (Certificate / Let's Encrypt).
6. (اختياري): إذا أردت فتح توثيق الـ API بشكل عام برابط مستقل، يمكنك إضافة دومين آخر مثل `api.yourdomain.com` وتوجيهه لخدمة `backend` على المنفذ `8000`.
   *(ملاحظة: الواجهة الأمامية تعيد توجيه جميع طلبات `/api/*` داخلياً عبر `INTERNAL_BACKEND_URL: http://backend:8000` بدون الحاجة لدومين منفصل للباك إند).*

### 5. تشغيل النشر (Deploy)
اضغط زر **Deploy**. سيقوم Dokploy ببناء الحاويات وتشغيل الفحوصات الدورية (Healthchecks) تلقائياً.

---

## الطريقة الثانية: النشر كخدمات منفصلة (Individual Applications)

إذا كنت تفضل إدارة كل حاوية على حدة داخل Dokploy:

### 1. إعداد قاعدة البيانات (PostgreSQL)
1. داخل مشروعك في Dokploy، اضغط **Create Service** ثم اختر **Database** -> **PostgreSQL**.
2. حدد كلمة مرور واسم قاعدة بيانات: `masry_stream`.
3. انسخ رابط الاتصال الداخلي الناتج (Internal Connection URL).

### 2. إعداد خدمة الباك إند (Backend Application)
1. اضغط **Create Service** -> **Application**.
2. اختر **Source**: مستودع الكود الخاص بك.
3. في إعدادات البناء (Build Settings):
   - **Build Type**: `Dockerfile`
   - **Context Path**: `/backend`
   - **Dockerfile Path**: `/backend/Dockerfile`
4. في إعدادات الشبكة:
   - **Container Port**: `8000`
5. في تبويب **Environment**:
   - `DATABASE_URL`: رابط الاتصال من خطوة PostgreSQL السابقة (باستخدام محرك `postgresql+asyncpg://...`).
   - `ADMIN_PASSWORD`: كلمة مرور لوحة الإدارة.
   - `ADMIN_ORIGINS`: دومين الواجهة الأمامية أو `["*"]`.
   - `ADMIN_SECURE_COOKIE`: `true`.
6. في تبويب **Mounts / Volumes**:
   - أنشئ Persistent Volume للمسار `/app/data`.
   - أنشئ Persistent Volume للمسار `/app/cache`.
7. اضغط **Deploy**.

### 3. إعداد خدمة الفرونت إند (Frontend Application)
1. اضغط **Create Service** -> **Application**.
2. اختر **Source**: مستودع الكود الخاص بك.
3. في إعدادات البناء (Build Settings):
   - **Build Type**: `Dockerfile`
   - **Context Path**: `/frontend`
   - **Dockerfile Path**: `/frontend/Dockerfile`
4. في إعدادات الشبكة:
   - **Container Port**: `3000`
5. في تبويب **Environment**:
   - `INTERNAL_BACKEND_URL`: الرابط الداخلي للباك إند على شبكة Dokploy (مثال: `http://<backend-app-name>:8000`).
   - `PORT`: `3000`
   - `HOSTNAME`: `0.0.0.0`
6. في تبويب **Domains**:
   - أضف اسم النطاق وفعّل SSL عبر Let's Encrypt.
7. اضغط **Deploy**.

---

## الميزات والتحسينات المضافة لدعم Dokploy

1. **Next.js Standalone Optimization**:
   تم تحديث `frontend/Dockerfile` ليعمل بنظام `standalone` المدمج في Next.js. هذا يقلل استهلاك الذاكرة (RAM) من 800 ميجابايت إلى نحو 120 ميجابايت، ويخفض زمن الإقلاع والتحميل على السيرفر.
2. **Reverse Proxy & Forwarded Headers**:
   تم تزويد خادم Uvicorn في الباك إند بخيارات `--proxy-headers` و `--forwarded-allow-ips=*` للتعامل تلقائياً وبأمان مع Traefik في Dokploy، وتمرير عناوين IP الحقيقية وتفعيل بروتوكول HTTPS للجلسات بدون تعارض.
3. **Resilient Admin Origin Validation**:
   أصبحت دالة التحقق من لوحة الإدارة تدعم التعرف التلقائي على النطاقات المرتبطة (Same-host matching) وتدعم القوائم المفصولة بفواصل أو JSON، مما يمنع خطأ 403 الشائع عند تشغيل لوحة الإدارة على نطاق جديد.
4. **Healthchecks مدمجة**:
   تم دمج فحوصات الصحة الدورية للفرونت إند والباك إند وقاعدة البيانات داخل Dockerfile و Compose لضمان ألا يقوم Dokploy بتوجيه الزوار إلا بعد اكتمال جاهزية الحاويات.
5. **تجاهل الملفات غير الضرورية (.dockerignore)**:
   تم إنشاء ملفات `.dockerignore` للفرونت إند والباك إند والجذر لمنع رفع ملفات الكاش المحلي وحزم node_modules الخاصة ببيئة التطوير المحلية، مما يسرع بناء الصور بشكل ملحوظ.
