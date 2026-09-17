import { ChangeDetectorRef, Component, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';

interface ProblemItem {
  WORKING_CODE: string;
  name: string;
  target_budget: number;
  actual_value: number;
  remaining_budget: number;
  percent_purchased: number;
  target_qty: number;
  actual_qty: number;
  unit_cost: number;
  actual_unit_cost: number;
  receipt_rows: number;
}

@Component({
  selector: 'app-procurement-plan',
  imports: [FormsModule, RouterLink],
  templateUrl: './procurement-plan.component.html',
  styleUrl: './procurement-plan.component.css',
})
export class ProcurementPlanComponent implements OnInit {
  loading = false;
  error = '';
  data: any = null;

  activeTab: 'over_procurement' | 'lagging' | 'price_variance' | 'zero_purchase' | 'unplanned' | 'all' = 'price_variance';
  searchQuery = '';

  readonly tabNotes: Record<string, string> = {
    over_procurement: 'รายการยาที่มีมูลค่าจัดซื้อจริงเกินกว่างบประมาณที่ได้รับอนุมัติในแผนประจำปี',
    lagging: 'รายการยาที่มีแผนจัดซื้อตั้งแต่ 10,000 บาทขึ้นไป แต่จัดซื้อจริงยังไม่ถึง 25% ของเป้าหมาย',
    price_variance: 'รายการยาที่ราคาซื้อจริงเฉลี่ยต่อหน่วยสูงกว่าราคาที่กำหนดไว้ในแผนเกินกว่า 5%',
    zero_purchase: 'รายการยาที่มีงบประมาณตามแผนแต่ยังไม่มีการจัดซื้อเลยตลอดปีงบประมาณ',
    unplanned: 'รายการยาที่มีการสั่งซื้อจริงในปีงบประมาณนี้ แต่ไม่ได้ระบุไว้ในแผนจัดซื้อยาประจำปี',
    all: 'แสดงรายการยาทั้งหมดที่มีการระบุในแผนจัดซื้อยาประจำปีงบประมาณ',
  };

  constructor(
    private readonly api: ApiService,
    private readonly cdr: ChangeDetectorRef
  ) {}

  // ---- ตั้งค่าแผนประจำปี (เฉพาะผู้มีสิทธิ์) ----
  planFile: any = null;
  showPlanManager = false;
  planUpload: File | null = null;
  planFileName = '';
  planBusy = false;
  planMessage = '';
  planError = '';

  ngOnInit(): void {
    this.load();
    this.loadPlanFile();
  }

  loadPlanFile(): void {
    this.api.procurementPlanFile().subscribe({
      next: info => { this.planFile = info; this.cdr.detectChanges(); },
      error: () => { this.planFile = null; },
    });
  }

  pickPlanFile(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.planUpload = input.files?.[0] || null;
    this.planFileName = this.planUpload?.name || '';
    this.planMessage = '';
    this.planError = '';
  }

  uploadPlan(): void {
    if (!this.planUpload) return;
    this.planBusy = true;
    this.planMessage = '';
    this.planError = '';
    this.api.uploadProcurementPlan(this.planUpload).subscribe({
      next: res => {
        this.planBusy = false;
        this.planUpload = null;
        this.planFileName = '';
        this.planMessage = res?.message || 'ติดตั้งแผนใหม่แล้ว';
        if (res?.previous_saved_as) this.planMessage += ` (เก็บแผนเดิมไว้เป็น ${res.previous_saved_as})`;
        if (res?.duplicate_count) {
          this.planMessage += ` — พบรหัสยาซ้ำ ${res.duplicate_count} รหัส งบของรหัสซ้ำจะถูกรวมกัน`;
        }
        this.loadPlanFile();
        this.load();
      },
      error: err => {
        this.planBusy = false;
        this.planError = err?.error?.message || 'อัปโหลดแผนไม่สำเร็จ';
        this.cdr.detectChanges();
      },
    });
  }

  restorePlan(name: string): void {
    this.planBusy = true;
    this.planMessage = '';
    this.planError = '';
    this.api.restoreProcurementPlan(name).subscribe({
      next: res => {
        this.planBusy = false;
        this.planMessage = res?.message || 'กู้คืนแผนแล้ว';
        this.loadPlanFile();
        this.load();
      },
      error: err => {
        this.planBusy = false;
        this.planError = err?.error?.message || 'กู้คืนแผนไม่สำเร็จ';
        this.cdr.detectChanges();
      },
    });
  }

  load(): void {
    this.loading = true;
    this.error = '';
    this.api.procurementPlan().subscribe({
      next: (res) => {
        this.data = res;
        this.loading = false;
        // If price_variance is empty or another tab has items, default smartly
        if (res.problem_counts) {
          if (res.problem_counts.over_procurement > 0) {
            this.activeTab = 'over_procurement';
          } else if (res.problem_counts.lagging > 0) {
            this.activeTab = 'lagging';
          } else if (res.problem_counts.price_variance > 0) {
            this.activeTab = 'price_variance';
          } else if (res.problem_counts.zero_purchase > 0) {
            this.activeTab = 'zero_purchase';
          } else if (res.problem_counts.unplanned > 0) {
            this.activeTab = 'unplanned';
          }
        }
        this.cdr.detectChanges();
      },
      error: (err) => {
        this.loading = false;
        this.error = err?.error?.message || err?.message || 'ไม่สามารถโหลดข้อมูลแผนการจัดซื้อยาได้';
        this.cdr.detectChanges();
      },
    });
  }

  get currentItems(): ProblemItem[] {
    if (!this.data) return [];
    let list: ProblemItem[] = [];
    if (this.activeTab === 'all') {
      list = this.data.all_items || [];
    } else {
      list = this.data.problems?.[this.activeTab] || [];
    }

    const q = this.searchQuery.trim().toLowerCase();
    if (!q) return list;

    return list.filter(
      (item) =>
        item.WORKING_CODE.toLowerCase().includes(q) ||
        item.name.toLowerCase().includes(q)
    );
  }

  money(value: number): string {
    return new Intl.NumberFormat('th-TH', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(value || 0);
  }

  number(value: number, digits = 0): string {
    return new Intl.NumberFormat('th-TH', {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(value || 0);
  }
}
