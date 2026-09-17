import { Component, HostListener, OnInit, effect } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ApiService } from './core/api.service';
import { AuthService } from './core/auth.service';
import { dashboardReadOnly } from './core/deployment';

export interface ThemeOption {
  id: string;
  label: string;
  swatchBg: string;
  swatchBorder: string;
  accent: string;
}

interface NavigationItem {
  path: string;
  icon: string;
  label: string;
  detail: string;
  /** หน้าของ ERPLPH ที่เซิร์ฟเวอร์สร้างเอง (ไม่ใช่หน้าจอ Angular) เปิดด้วยลิงก์ธรรมดา */
  external?: boolean;
}

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App implements OnInit {
  readonly readOnly = dashboardReadOnly();
  // ERPLPH ใช้หน้าจอชุดเดียวกับ Stock5 แต่ตัดส่วนส่งข้อมูลกระทรวงออก (ผู้ใช้สั่ง 17 ก.ย. 2569)
  // และดูได้ทุกประเภทของ ทุกคลัง ไม่ใช่เฉพาะยาของคลัง 2
  readonly navigation: NavigationItem[] = [
    { path: '/dashboard', icon: '📊', label: 'Dashboard', detail: 'ภาพรวมทุกคลัง ทุกประเภทของ มูลค่า และอัตราสำรอง' },
    { path: '/purchasing', icon: '🛒', label: 'ส่วนของด้านจัดซื้อ', detail: 'ตารางวิเคราะห์ปฏิบัติการจัดซื้อ' },
    { path: '/drug-search', icon: '🔎', label: 'ค้นหาและตรวจสอบข้อมูล', detail: 'ยา พัสดุ ครุภัณฑ์ อาหาร งานจ้าง' },
    { path: '/procurement-plan', icon: '📋', label: 'แผนการจัดซื้อ ประจำปีงบประมาณ', detail: 'เปรียบเทียบแผน vs ซื้อจริง ไตรมาส 1-4' },
    { path: 'departments', icon: '🏥', label: 'แต่ละแผนกเบิกอะไรไปเท่าไร', detail: 'กลุ่มงาน › งาน › ส่วนย่อย › รายการ', external: true },
    { path: 'cut-status', icon: '⏱️', label: 'คลังไหนข้อมูลค้าง', detail: 'คลังที่หยุดตัดยอด / ช้ากว่าปกติ', external: true },
  ];

  alertsData: any = null;
  showNotificationDropdown = false;
  sidebarCollapsed = true; // ย่อแถบเมนูข้างไว้เป็นค่าเริ่มต้นตามคำขอ

  // 4 Themes: Navy (Current), Pastel Green, Pastel White, Pastel Pink
  readonly availableThemes: ThemeOption[] = [
    { id: 'navy', label: 'สีกรมท่า (สีเดิม)', swatchBg: '#08111f', swatchBorder: '#38bdf8', accent: '#38bdf8' },
    { id: 'pastel-green', label: 'เขียวพาสเทล', swatchBg: '#eef7f2', swatchBorder: '#86efac', accent: '#16a34a' },
    { id: 'pastel-white', label: 'ขาวพาสเทล', swatchBg: '#ffffff', swatchBorder: '#cbd5e1', accent: '#0284c7' },
    { id: 'pastel-pink', label: 'ชมพูพาสเทล', swatchBg: '#fdf2f4', swatchBorder: '#f9a8d4', accent: '#db2777' },
  ];

  currentTheme = 'navy';
  showThemeMenu = false;

  get currentThemeLabel(): string {
    return this.availableThemes.find(t => t.id === this.currentTheme)?.label || 'สีกรมท่า';
  }

  get currentThemeColor(): string {
    return this.availableThemes.find(t => t.id === this.currentTheme)?.accent || '#38bdf8';
  }

  setTheme(themeId: string): void {
    this.currentTheme = themeId;
    try {
      localStorage.setItem('stock5_theme', themeId);
    } catch {}
    this.applyThemeClass(themeId);
    this.showThemeMenu = false;
  }

  private applyThemeClass(themeId: string): void {
    const body = document.body;
    body.classList.remove('theme-navy', 'theme-pastel-green', 'theme-pastel-white', 'theme-pastel-pink');
    body.classList.add(`theme-${themeId}`);
  }

  toggleThemeMenu(event: MouseEvent): void {
    event.stopPropagation();
    this.showThemeMenu = !this.showThemeMenu;
  }

  constructor(private readonly api: ApiService, readonly auth: AuthService, private readonly router: Router) {
    // ดึงการแจ้งเตือนใหม่ทันทีที่ล็อกอินสำเร็จ (หรือเมื่อเปิดแอปมาพร้อม session เดิม)
    // และเคลียร์ทิ้งเมื่อออกจากระบบ — ไม่ยิง /api/pos/alerts ตอนยังไม่ได้ล็อกอิน
    effect(() => {
      if (this.auth.currentUser()) {
        this.fetchAlerts();
      } else {
        this.alertsData = null;
      }
    });
  }

  toggleSidebar(): void {
    this.sidebarCollapsed = !this.sidebarCollapsed;
  }

  ngOnInit(): void {
    try {
      const savedTheme = localStorage.getItem('stock5_theme') || 'navy';
      this.setTheme(savedTheme);
    } catch {
      this.setTheme('navy');
    }
    this.auth.bootstrap();
    setInterval(() => this.fetchAlerts(), 45000);
  }

  fetchAlerts(): void {
    if (!this.auth.currentUser()) { return; }
    this.api.getPoAlerts().subscribe({
      next: data => {
        this.alertsData = data;
      },
      error: () => {}
    });
  }

  logout(): void {
    this.auth.logout().subscribe(() => this.router.navigate(['/login']));
  }

  toggleAlertsDropdown(): void {
    this.showNotificationDropdown = !this.showNotificationDropdown;
    if (this.showNotificationDropdown) {
      this.fetchAlerts();
    }
  }

  @HostListener('document:click')
  onDocumentClick(): void {
    this.showNotificationDropdown = false;
    this.showThemeMenu = false;
  }
}
