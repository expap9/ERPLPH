import { DOCUMENT } from '@angular/common';
import { inject } from '@angular/core';
import { HttpInterceptorFn } from '@angular/common/http';

// The server supplies <base href="/STOCK/"> or <base href="/stock/">; local desktop keeps "/".
export const deploymentPrefixInterceptor: HttpInterceptorFn = (request, next) => {
  const doc = inject(DOCUMENT);
  if (!request.url.startsWith('/api/')) return next(request);
  const baseUri = doc.baseURI.endsWith('/') ? doc.baseURI : doc.baseURI + '/';
  return next(request.clone({url: new URL(request.url.slice(1), baseUri).toString()}));
};

export function dashboardReadOnly(): boolean {
  return document.querySelector('meta[name="stock5-readonly"]')?.getAttribute('content') === 'true';
}
