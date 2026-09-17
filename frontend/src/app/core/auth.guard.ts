import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { of } from 'rxjs';
import { map } from 'rxjs/operators';
import { AuthService } from './auth.service';
import { dashboardReadOnly } from './deployment';

/** Redirects to /login (keeping the intended URL) unless a session is already active. */
export const authGuard: CanActivateFn = (_route, state) => {
  // The published report server (app/dashboard_public.py) serves no session
  // endpoints at all, so this guard could only ever send every visitor to a
  // login page that cannot succeed. Access there is the hospital proxy's job;
  // the server itself refuses every non-GET request.
  if (dashboardReadOnly()) return of(true);
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.ensureChecked().pipe(
    map(user => user ? true : router.createUrlTree(['/login'], { queryParams: { returnUrl: state.url } })),
  );
};
