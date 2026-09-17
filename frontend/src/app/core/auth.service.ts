import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { Observable, catchError, map, of, tap } from 'rxjs';
import { dashboardReadOnly } from './deployment';

export interface CurrentUser {
  username: string;
  name: string;
  access_group: string;
  facility: string;
}

interface MeResponse {
  status: string;
  user: CurrentUser | null;
}

interface LoginResponse {
  status: string;
  user?: CurrentUser;
  message?: string;
}

/**
 * Per-user login session (see app/his_login.py + app/server.py). Verified
 * against the hospital's own SYSCONFIG credentials — the same login used by
 * the pharmacy dispensing program — so this only tracks "who is logged in"
 * for this browser; it does not replace the MOPH sender token, which stays
 * one shared credential per environment (see api.service.ts sender methods),
 * now scoped per user on the server side.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  readonly currentUser = signal<CurrentUser | null>(null);
  readonly checkingAuth = signal(true);
  private checked = false;

  constructor(private readonly http: HttpClient) {}

  /** Call once at app bootstrap (see App.ngOnInit). */
  bootstrap(): void {
    // The read-only report server has no session endpoints; asking it who is
    // logged in only produced a 404 in the console on every page load.
    if (dashboardReadOnly()) {
      this.checked = true;
      this.checkingAuth.set(false);
      return;
    }
    this.refreshMe().subscribe();
  }

  /** Used by the route guard — resolves the current user, asking the server only once. */
  ensureChecked(): Observable<CurrentUser | null> {
    if (this.checked) return of(this.currentUser());
    return this.refreshMe();
  }

  private refreshMe(): Observable<CurrentUser | null> {
    this.checkingAuth.set(true);
    return this.http.get<MeResponse>('/api/auth/me').pipe(
      map(res => res.user),
      tap(user => {
        this.currentUser.set(user);
        this.checkingAuth.set(false);
        this.checked = true;
      }),
      catchError(() => {
        this.currentUser.set(null);
        this.checkingAuth.set(false);
        this.checked = true;
        return of(null);
      }),
    );
  }

  login(username: string, password: string): Observable<LoginResponse> {
    return this.http.post<LoginResponse>('/api/auth/login', { username, password }).pipe(
      tap(res => {
        if (res?.user) {
          this.currentUser.set(res.user);
          this.checked = true;
        }
      }),
      catchError((err: HttpErrorResponse) => {
        const message = err?.error?.message || 'เข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่';
        return of({ status: 'error', message } as LoginResponse);
      }),
    );
  }

  logout(): Observable<unknown> {
    return this.http.post('/api/auth/logout', {}).pipe(
      tap(() => this.clearUser()),
      catchError(() => {
        this.clearUser();
        return of(null);
      }),
    );
  }

  /** Used by the 401 interceptor when a session expires mid-use. */
  clearUser(): void {
    this.currentUser.set(null);
    this.checked = true;
  }
}
