import { ApplicationConfig, provideBrowserGlobalErrorListeners, provideZoneChangeDetection } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { deploymentPrefixInterceptor } from './core/deployment';
import { authInterceptor } from './core/auth.interceptor';
import { provideRouter } from '@angular/router';

import { routes } from './app.routes';
export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(withInterceptors([deploymentPrefixInterceptor, authInterceptor]))
  ]
};
