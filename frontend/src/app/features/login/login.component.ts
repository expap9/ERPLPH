import { ChangeDetectorRef, Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent implements OnInit {
  username = '';
  password = '';
  submitting = false;
  error = '';
  private returnUrl = '/dashboard';

  constructor(
    private readonly auth: AuthService,
    private readonly router: Router,
    private readonly route: ActivatedRoute,
    private readonly cdr: ChangeDetectorRef,
  ) {}

  ngOnInit(): void {
    this.returnUrl = this.route.snapshot.queryParamMap.get('returnUrl') || '/dashboard';
    if (this.auth.currentUser()) {
      this.router.navigateByUrl(this.returnUrl);
    }
  }

  submit(): void {
    const username = this.username.trim();
    if (!username || !this.password) {
      this.error = 'กรุณากรอกชื่อผู้ใช้และรหัสผ่าน';
      return;
    }
    this.submitting = true;
    this.error = '';
    this.auth.login(username, this.password).subscribe(res => {
      this.submitting = false;
      if (res.user) {
        this.router.navigateByUrl(this.returnUrl);
      } else {
        this.error = res.message || 'เข้าสู่ระบบไม่สำเร็จ';
      }
      this.cdr.detectChanges();
    });
  }
}
