import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

/**
 * Catches a session expiring mid-use (server restarted, TTL elapsed) and sends
 * the user back to /login. The login call itself is expected to 401 on bad
 * credentials, so it is excluded — that error belongs on the login form, not
 * a redirect.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const isLoginCall = request.url.includes('api/auth/login');

  return next(request).pipe(
    catchError((error: unknown) => {
      if (!isLoginCall && error instanceof HttpErrorResponse && error.status === 401) {
        auth.clearUser();
        if (!router.url.startsWith('/login')) {
          router.navigate(['/login'], { queryParams: { returnUrl: router.url } });
        }
      }
      return throwError(() => error);
    }),
  );
};
