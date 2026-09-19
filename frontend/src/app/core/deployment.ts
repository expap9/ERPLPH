import { DOCUMENT } from '@angular/common';
import { inject } from '@angular/core';
import { HttpInterceptorFn } from '@angular/common/http';

// เซิร์ฟเวอร์ตั้ง <base href="/ERPLPH/"> (หรือ "/stock/" ตอนอยู่ใต้ /stock) เดสก์ท็อป
// ในเครื่องยังเป็น "/" — api.service.ts เรียกด้วย path สัมพัทธ์ (ไม่มี / นำหน้า เช่น
// "api/status") อยู่แล้ว ตัว interceptor นี้แค่ resolve ให้ตรงกับ base href ปัจจุบัน
// อย่างชัดเจน กันกรณี fetch/XHR ของบางเบราว์เซอร์ไม่ตาม <base href> โดยปริยาย
export const deploymentPrefixInterceptor: HttpInterceptorFn = (request, next) => {
  const doc = inject(DOCUMENT);
  if (!request.url.startsWith('api/')) return next(request);
  const baseUri = doc.baseURI.endsWith('/') ? doc.baseURI : doc.baseURI + '/';
  return next(request.clone({url: new URL(request.url, baseUri).toString()}));
};

export function dashboardReadOnly(): boolean {
  return document.querySelector('meta[name="stock5-readonly"]')?.getAttribute('content') === 'true';
}
