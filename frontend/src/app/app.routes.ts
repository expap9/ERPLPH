import { Routes } from '@angular/router';
import { authGuard } from './core/auth.guard';

// หน้าจอชุดเดียวกับ Stock5 โดยตัดส่วนส่งข้อมูลกระทรวงและหน้าตั้งค่าฐานข้อมูลออก
// (ผู้ใช้สั่ง 17 ก.ย. 2569) ERPLPH อ่านจากคลังข้อมูลของตัวเองเท่านั้น จึงไม่มีหน้ากำหนดชื่อตาราง
export const routes: Routes = [
  { path: 'login', title: 'เข้าสู่ระบบ', loadComponent: () => import('./features/login/login.component').then(m => m.LoginComponent) },

  { path: 'dashboard', canActivate: [authGuard], title: 'Dashboard ภาพรวมคลังทั้งโรงพยาบาล', data: { mode: 'dashboard' }, loadComponent: () => import('./features/monitor/monitor.component').then(m => m.MonitorComponent) },
  { path: 'purchasing', canActivate: [authGuard], title: 'ส่วนของด้านจัดซื้อ', data: { mode: 'purchasing' }, loadComponent: () => import('./features/monitor/monitor.component').then(m => m.MonitorComponent) },
  { path: 'drug-search', canActivate: [authGuard], title: 'ค้นหาและตรวจสอบข้อมูล', loadComponent: () => import('./features/drug-search/drug-search.component').then(m => m.DrugSearchComponent) },
  { path: 'drugs/:code', canActivate: [authGuard], title: 'รายละเอียดรายการ', loadComponent: () => import('./features/drug-search/drug-search.component').then(m => m.DrugSearchComponent) },
  { path: 'procurement-plan', canActivate: [authGuard], title: 'แผนการจัดซื้อ ประจำปีงบประมาณ', loadComponent: () => import('./features/procurement-plan/procurement-plan.component').then(m => m.ProcurementPlanComponent) },

  { path: 'monitor', redirectTo: 'dashboard' },
  { path: '', pathMatch: 'full', redirectTo: 'dashboard' },
  { path: '**', redirectTo: 'dashboard' },
];
