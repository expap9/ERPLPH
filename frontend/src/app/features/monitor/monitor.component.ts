import { ChangeDetectorRef, Component, HostListener, OnDestroy, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { dashboardReadOnly } from '../../core/deployment';

interface ColumnDefinition { key: string; label: string; type: string; }
interface TableDefinition { label: string; note: string; columns: ColumnDefinition[]; }

@Component({
  selector: 'app-monitor',
  imports: [FormsModule, RouterLink],
  templateUrl: './monitor.component.html',
  styleUrl: './monitor.component.css',
})
export class MonitorComponent implements OnInit, OnDestroy {
  readonly deploymentReadOnly = dashboardReadOnly();
  get readOnly(): boolean { return this.deploymentReadOnly || !!this.data?.meta?.read_only; }
  viewMode: 'dashboard' | 'purchasing' = 'dashboard';
  // Fixed reporting criteria. The screen no longer offers these as controls, so
  // everyone reads the same numbers; they still drive the chart thresholds and
  // the expiry-window label below.
  readonly targetDays = 30;
  readonly usageMonths = 6;
  readonly expiryDays = 240;
  loading = false;
  error = '';
  data: any;
  syncingLive = false;
  syncMessage = '';
  selectedTable = 'backorder';
  tableSearch = '';
  procurementData: any = null;
  autoSyncStatus: any = null;
  private autoSyncTimer: any = null;
  readonly controlMos = 1.5;
  showAllDepartments = false;
  departmentSearch = '';
  selectedDepartment: any = null;
  modalDrugSearch = '';
  leadersData: any = null;
  loadingLeaders = false;
  leadersMonths = 18;
  // ขอบเขตของ ERPLPH — Stock5 ดูได้แค่ยาของคลัง 2 ที่นี่ดูได้ทุกประเภทของ ทุกคลัง
  scopeGroup = '';
  scopeStore = '';
  scopeOptions: any = { groups: [], stores: [] };
  readonly tableKeys = ['backorder', 'excess', 'non_moving', 'expiry', 'shortage', 'top_value', 'top_receipt_value', 'top_distribution_value', 'unit_pending', 'already_expired'];
  // เชื่อมแถบสัดส่วนมูลค่าคงคลัง (stock_buckets) เข้ากับตารางรายการด้านล่าง คลิกแล้ว
  // เด้งไปเปิดตารางที่ตรงกัน — "protected" ไม่มีตารางเทียบเท่าโดยตรง จึงไม่ผูกไว้
  readonly bucketTableMap: Record<string, string> = { unit_pending: 'unit_pending', non_moving: 'non_moving', active_excess: 'excess' };
  readonly definitions: Record<string, TableDefinition> = {
    backorder: {
      label: 'ขาดจ่าย',
      note: 'รายการเวชภัณฑ์ที่หน่วยงาน/ห้องยาขอเบิกแต่คลังจ่ายให้ไม่ครบ หรือไม่มียาจ่าย (ค้างเบิก = ขอเบิก - เบิกให้) ย้อนหลัง 1 เดือน แสดงทุกใบที่มีปัญหา',
      columns: [
        { key: 'doc_date', label: 'วันที่เวลาเบิกจ่าย', type: 'text' },
        { key: 'irno', label: 'เลขที่ใบจ่าย (IRNO)', type: 'text' },
        { key: 'from_request_ref_no', label: 'เลขที่ใบเบิกห้องยา (Ref)', type: 'text' },
        { key: 'dept_name', label: 'แผนก', type: 'text' },
        { key: 'WORKING_CODE', label: 'รหัส', type: 'code' },
        { key: 'name', label: 'ชื่อรายการ', type: 'name' },
        { key: 'status', label: 'สถานะ', type: 'status' },
        { key: 'request_qty', label: 'ขอเบิก', type: 'actual' },
        { key: 'issue_qty', label: 'เบิกให้', type: 'actual' },
        { key: 'lack_qty', label: 'ค้างเบิก (ขาด Stock)', type: 'actual' },
        { key: 'unit', label: 'หน่วยนับ', type: 'text' },
      ],
    },
    unit_pending: { label: 'รอยืนยันหน่วย', note: 'รายการเหล่านี้ยังไม่ใช้คำนวณ MOS หรือมูลค่าส่วนเกิน ตรวจหน่วยต้นทางและอัตราแปลงที่ยังขาดด้านล่าง หน่วยที่โรงพยาบาลยืนยันไม่ได้หมายความว่าทุกหน่วยในใบจ่ายแปลงถึงกันได้ หากไฟล์เก่าไม่เก็บหน่วยจ่ายจริงจึงต้องดึงข้อมูลใหม่', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'},{key:'stock_quantities',label:'จำนวนคงคลัง',type:'quantity'},{key:'confirmed_unit',label:'หน่วยที่โรงพยาบาลยืนยัน',type:'text'},{key:'unit_pending_reason',label:'ข้อมูลที่ยังต้องตรวจสอบ',type:'text'}
    ]},
    excess: { label: 'คงคลังเกินเกณฑ์', note: 'จำนวนและหน่วยแสดงตามข้อมูลต้นทาง ส่วนจำนวนเดือนสำรอง จำนวนวันคงคลัง และมูลค่าส่วนเกินเป็นค่าประมาณจากหลักเกณฑ์ที่ระบุด้านบน', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'},{key:'stock_quantities',label:'จำนวนคงคลังตามข้อมูลต้นทาง',type:'quantity'},{key:'days_on_hand',label:'จำนวนวันคงคลังโดยประมาณ',type:'days'},{key:'months_on_hand',label:'จำนวนเดือนสำรอง (MOS)',type:'number1'},{key:'excess_value',label:'มูลค่าส่วนที่เกินเกณฑ์',type:'money'},{key:'avg_monthly_issue_quantities',label:'จำนวนเบิกจ่ายเฉลี่ยต่อเดือน',type:'quantity'}
    ]},
    non_moving: { label: 'ไม่พบประวัติการเบิกจ่าย', note: 'ต้องยืนยันว่าข้อมูลการเบิกจ่ายครอบคลุมทุกคลังและทุกหน่วยงานก่อนสรุปว่าเป็นยาที่ไม่มีการเคลื่อนไหว', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'stock_quantities',label:'จำนวนคงคลังตามข้อมูลต้นทาง',type:'quantity'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'},{key:'lots',label:'จำนวนรุ่นการผลิต',type:'number'}
    ]},
    expiry: { label: 'ใกล้หมดอายุ', note: 'รายการยาที่จะหมดอายุในเร็วๆ นี้ (นับจากวันนี้) เพื่อวางแผนเร่งจ่าย First-Expire First-Out หรือกระจายยา', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'lot_no',label:'เลขรุ่นการผลิต',type:'text'},{key:'expiry_date',label:'วันหมดอายุ',type:'date'},{key:'days_to_expire',label:'จำนวนวันก่อนหมดอายุ',type:'days'},{key:'stock_qty_actual',label:'จำนวนคงคลัง',type:'actual'},{key:'stock_unit',label:'หน่วยนับ',type:'text'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'}
    ]},
    already_expired: { label: '⚠️ หมดอายุแล้ว (ย้อนหลัง 1 ปี)', note: 'รายการยาที่วันหมดอายุผ่านมาแล้วในช่วง 1 ปีที่ผ่านมาแต่ยังมีคงคลังคงเหลืออยู่ในระบบ ควรเร่งดำเนินการตัดจำหน่ายหรือทำลายตามระเบียบ', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'lot_no',label:'เลขรุ่นการผลิต',type:'text'},{key:'expiry_date',label:'วันหมดอายุ',type:'date'},{key:'days_to_expire',label:'หมดอายุมาแล้ว (วัน)',type:'days'},{key:'stock_qty_actual',label:'จำนวนคงคลัง',type:'actual'},{key:'stock_unit',label:'หน่วยนับ',type:'text'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'}
    ]},
    shortage: { label: 'อาจมีไม่เพียงพอต่อการให้บริการ', note: 'ควรตรวจนับยอดคงเหลือจริงและตรวจสอบรายการค้างรับก่อนดำเนินการจัดซื้อเร่งด่วน', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'status',label:'สถานะ',type:'status'},{key:'days_on_hand',label:'จำนวนวันคงคลังโดยประมาณ',type:'days'},{key:'avg_monthly_issue_quantities',label:'จำนวนเบิกจ่ายเฉลี่ยต่อเดือน',type:'quantity'},{key:'issue_value',label:'มูลค่าการเบิกจ่าย',type:'money'}
    ]},
    top_value: { label: 'มูลค่าคงคลังสูงสุด', note: 'เรียงจากมูลค่าคงคลังสูงไปต่ำ เพื่อกำหนดลำดับรายการที่ควรตรวจสอบก่อน', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'stock_value',label:'มูลค่าคงคลัง',type:'money'},{key:'stock_quantities',label:'จำนวนคงคลังตามข้อมูลต้นทาง',type:'quantity'},{key:'lots',label:'จำนวนรุ่นการผลิต',type:'number'}
    ]},
    top_receipt_value: { label: 'มูลค่าซื้อ/จ้างสูงสุด', note: 'มูลค่ารับยารวมรายตัวยา เฉพาะช่วงเดือนเดียวกับที่ใช้คำนวณอัตราเบิกจ่ายด้านบน เรียงจากมูลค่าซื้อสูงไปต่ำ', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'receipt_value',label:'มูลค่าซื้อ',type:'money'},{key:'receipt_qty',label:'จำนวนรับรวม',type:'actual'},{key:'receipt_rows',label:'จำนวนครั้งที่รับ',type:'number'}
    ]},
    top_distribution_value: { label: 'มูลค่าจ่ายสูงสุด', note: 'มูลค่าจ่ายยารวมรายตัวยา ในช่วงเดือนเดียวกับที่ใช้คำนวณอัตราเบิกจ่ายด้านบน เรียงจากมูลค่าจ่ายสูงไปต่ำ', columns: [
      {key:'WORKING_CODE',label:'รหัส',type:'code'},{key:'name',label:'ชื่อรายการ',type:'name'},{key:'issue_value',label:'มูลค่าจ่าย',type:'money'},{key:'avg_monthly_issue_quantities',label:'จำนวนเบิกจ่ายเฉลี่ยต่อเดือน',type:'quantity'},{key:'issue_rows',label:'จำนวนครั้งที่จ่าย',type:'number'}
    ]},
  };

  sortKey: string | null = null;
  sortDirection: 'asc' | 'desc' = 'desc';

  constructor(
    private readonly api: ApiService,
    private readonly cdr: ChangeDetectorRef,
    private readonly route: ActivatedRoute,
    private readonly router: Router
  ) {}

  ngOnInit(): void {
    const isPurchasing = this.router.url.includes('/purchasing');
    this.viewMode = isPurchasing ? 'purchasing' : 'dashboard';
    this.selectedTable = isPurchasing ? 'backorder' : 'excess';

    this.route.data.subscribe(d => {
      if (d['mode'] === 'purchasing') {
        this.viewMode = 'purchasing';
        this.selectedTable = 'backorder';
      } else if (d['mode'] === 'dashboard') {
        this.viewMode = 'dashboard';
      }
    });

    this.route.queryParams.subscribe(params => {
      if (params['tab'] && this.tableKeys.includes(params['tab'])) {
        this.selectedTable = params['tab'];
      }
      const group = params['group'] || '';
      const store = params['store'] || '';
      if (group !== this.scopeGroup || store !== this.scopeStore) {
        this.scopeGroup = group;
        this.scopeStore = store;
        if (this.data) this.load();
      }
    });
    this.api.scopes().subscribe({ next: options => { this.scopeOptions = options; this.cdr.detectChanges(); }, error: () => {} });

    this.load();
    this.loadAutoSyncStatus();
    this.autoSyncTimer = setInterval(() => this.loadAutoSyncStatus(), 30000);
  }

  ngOnDestroy(): void {
    if (this.autoSyncTimer) {
      clearInterval(this.autoSyncTimer);
      this.autoSyncTimer = null;
    }
    document.body.style.overflow = '';
  }

  loadAutoSyncStatus(): void {
    this.api.getMonitorAutoSyncStatus().subscribe({
      next: (st: any) => {
        this.autoSyncStatus = st;
        this.cdr.detectChanges();
      },
      error: () => {}
    });
  }

  triggerAutoSyncNow(): void {
    this.api.triggerMonitorAutoSync().subscribe({
      next: () => {
        this.loadAutoSyncStatus();
      },
      error: () => {}
    });
  }

  get currentDefinition(): TableDefinition { return this.definitions[this.selectedTable]; }
  get rows(): any[] {
    const base: any[] = this.data?.tables?.[this.selectedTable] || [];
    let filtered = base;
    if (this.tableSearch.trim()) {
      const q = this.tableSearch.trim().toLowerCase();
      filtered = base.filter(r =>
        String(r.WORKING_CODE || '').toLowerCase().includes(q) ||
        String(r.name || '').toLowerCase().includes(q) ||
        String(r.irno || '').toLowerCase().includes(q) ||
        String(r.from_request_ref_no || '').toLowerCase().includes(q) ||
        String(r.dept_name || '').toLowerCase().includes(q) ||
        String(r.lot_no || '').toLowerCase().includes(q)
      );
    }
    const column = this.currentDefinition?.columns?.find(c => c.key === this.sortKey);
    if (!column) return filtered;
    const dir = this.sortDirection === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = this.sortRawValue(a, column);
      const bv = this.sortRawValue(b, column);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      return 0;
    });
  }

  load(): void {
    this.loading = true; this.error = '';
    this.api.monitor(this.targetDays, this.usageMonths, this.expiryDays, this.scopeGroup, this.scopeStore).subscribe({
      next: data => { this.data = data; this.loading = false; this.error = ''; this.cdr.detectChanges(); },
      error: err => {
        this.loading = false;
        if (err?.status === 404) {
          this.error = '';
          this.data = null;
        } else {
          this.error = err?.error?.message || err?.message || 'ไม่สามารถจัดทำรายงานติดตามคลังยาได้';
        }
        this.cdr.detectChanges();
      },
    });

    this.api.procurementPlan().subscribe({
      next: (plan: any) => {
        this.procurementData = plan;
        this.cdr.detectChanges();
      },
      error: () => {}
    });

    this.loadLeaders();
  }

  loadLeaders(months?: number): void {
    if (months) {
      this.leadersMonths = months;
    }
    this.loadingLeaders = true;
    this.api.requisitionLeaders(this.leadersMonths).subscribe({
      next: (res: any) => {
        this.loadingLeaders = false;
        if (res?.status === 'success' && res.data) {
          this.leadersData = res.data;
        }
        this.cdr.detectChanges();
      },
      error: () => {
        this.loadingLeaders = false;
        this.cdr.detectChanges();
      }
    });
  }

  getMedal(index: number): string {
    if (index === 0) return '🥇';
    if (index === 1) return '🥈';
    if (index === 2) return '🥉';
    return String(index + 1);
  }

  liveSync(): void {
    if (this.readOnly) return;
    this.syncingLive = true;
    this.syncMessage = 'กำลังเชื่อมต่อและดึงข้อมูลสดจาก SQL Server... (อาจใช้เวลาประมาณ 1-2 นาที)';
    this.cdr.detectChanges();
    this.api.pullMonitor().subscribe({
      next: (res: any) => {
        if (res?.status !== 'success') {
          this.syncingLive = false;
          this.syncMessage = '⚠️ ' + (res.message || 'ไม่สามารถดึงข้อมูลได้');
          this.cdr.detectChanges();
          return;
        }
        this.syncMessage = res.message || 'ดึงข้อมูลแดชบอร์ดครบสามแฟ้มแล้ว กำลังประมวลผล';
        this.cdr.detectChanges();
        this.load();
        this.syncingLive = false;
      },
      error: (err: any) => {
        this.syncingLive = false;
        const msg = err?.error?.message || err?.message || 'ไม่สามารถเชื่อมต่อ SQL Server ได้';
        this.syncMessage = '⚠️ เกิดข้อผิดพลาด: ' + msg;
        this.cdr.detectChanges();
      }
    });
  }

  clearing = false;
  clearAllData(): void {
    if (this.readOnly) return;
    if (!confirm('⚠️ ยืนยันล้างข้อมูลทั้งหมด?\n\nระบบจะล้างข้อมูลแคชและข้อมูลวิเคราะห์ทั้งหมดออก\nเพื่อให้เริ่มใช้เฉพาะข้อมูลสดที่ดึงจาก SQL Server เท่านั้น')) {
      return;
    }
    this.clearing = true;
    this.syncMessage = 'กำลังล้างข้อมูลทั้งหมด...';
    this.cdr.detectChanges();
    this.api.clearAllData().subscribe({
      next: (res: any) => {
        this.clearing = false;
        this.data = null;
        this.syncMessage = '🗑️ ล้างข้อมูลเรียบร้อยแล้ว — กรุณากดปุ่ม "🔄 ดึงข้อมูลสดจากฐานข้อมูล" เพื่อดึงข้อมูลจริงจาก SQL Server';
        this.cdr.detectChanges();
      },
      error: (err: any) => {
        this.clearing = false;
        this.syncMessage = 'เกิดข้อผิดพลาดในการล้างข้อมูล: ' + (err?.error?.message || err?.message);
        this.cdr.detectChanges();
      }
    });
  }

  // --- SVG Chart Calculations & Helpers ---
  readonly svgW = 580;
  readonly svgH = 220;
  get chartWidth(): number { return this.svgW; }
  get chartHeight(): number { return this.svgH; }
  readonly plotTop = 25;
  readonly plotBottom = 180; // baseline Y
  readonly plotLeft = 50;
  readonly plotRight = 565;

  get plotW(): number { return this.plotRight - this.plotLeft; }
  get plotH(): number { return this.plotBottom - this.plotTop; }

  get monthlyTrend(): any[] {
    return this.data?.monthly_trend || [];
  }

  // MOS Scale: dynamic and responsive to actual data
  get mosScaleMax(): number {
    const valid = this.monthlyTrend.filter((m: any) => !m.partial).map((m: any) => Number(m.mos || 0));
    const maxVal = Math.max(2.0, ...valid);
    return Math.ceil(maxVal * 1.3 * 2) / 2; // e.g. 2.5 or 3.0
  }

  mosToY(val: number): number {
    const clamped = Math.min(this.mosScaleMax, Math.max(0, val));
    return this.plotBottom - (clamped / this.mosScaleMax) * this.plotH;
  }

  get targetMosY(): number {
    return this.mosToY(this.targetDays / 30);
  }

  get mosGridLines(): { val: number; y: number }[] {
    const lines = [];
    const step = this.mosScaleMax <= 3 ? 0.5 : (this.mosScaleMax <= 6 ? 1 : 2);
    for (let v = step; v < this.mosScaleMax; v += step) {
      lines.push({ val: v, y: this.mosToY(v) });
    }
    return lines;
  }

  // --- 12-Month Historical MOS Trend Chart (with 1.5-Month Control Line) ---
  readonly svg12W = 760;
  readonly svg12H = 150;
  get chart12Width(): number { return this.svg12W; }
  get chart12Height(): number { return this.svg12H; }
  readonly plot12Top = 20;
  readonly plot12Bottom = 122; // baseline Y
  readonly plot12Left = 45;
  readonly plot12Right = 740;
  get plot12W(): number { return this.plot12Right - this.plot12Left; }
  get plot12H(): number { return this.plot12Bottom - this.plot12Top; }

  /** เปลี่ยนขอบเขตผ่าน URL เพื่อให้ส่งลิงก์ต่อกันได้ และแท็บ Dashboard/จัดซื้อใช้ขอบเขตเดียวกัน */
  setScope(group: string, store: string): void {
    this.router.navigate([], { relativeTo: this.route, queryParamsHandling: 'merge',
      queryParams: { group: group || null, store: store || null } });
  }

  get monthlyTrend12(): any[] {
    const list = this.monthlyTrend;
    if (!list || !list.length) return [];
    const sliced = list.length <= 12 ? list : list.slice(list.length - 12);
    // ERPLPH บอกมาเองว่ายังย้อนยอดคงคลังไม่ได้ (ใบรับยังไม่ครบทุกคลัง) ห้ามย้อนยอดเองที่หน้าจอ
    // ไม่งั้นจะได้ MOS ย้อนหลังสูงเกินจริงสะสมทุกเดือน
    if (this.data?.meta?.trend_mos_available === false) {
      return sliced.map((m: any) => ({ ...m, mos: null }));
    }

    // Check if items already have valid mos from backend
    const hasBackendMos = sliced.some((m: any) => m.mos !== null && m.mos !== undefined && Number.isFinite(Number(m.mos)));
    if (hasBackendMos) {
      return sliced;
    }

    // Roll-back inventory reconciliation fallback if backend mos is null
    // S_{m-1} = S_m - R_m + I_m
    const totalStock = Number(this.data?.kpis?.stock_value || 0);
    let runningStock = totalStock;
    const rev = [...sliced].reverse();
    const enriched = rev.map((m: any) => {
      const rVal = Number(m.receipt_value || 0);
      const iVal = Number(m.issue_value || 0);
      const endStock = runningStock;
      const mos = iVal > 0 ? Math.round((endStock / iVal) * 100) / 100 : null;
      runningStock = Math.max(0, runningStock - rVal + iVal);
      return {
        ...m,
        end_stock: endStock,
        mos: mos,
      };
    });
    return enriched.reverse();
  }

  get mos12ScaleMax(): number {
    const valid = this.monthlyTrend12
      .filter((m: any) => m.mos !== null && m.mos !== undefined)
      .map((m: any) => Number(m.mos || 0));
    const maxVal = Math.max(2.0, this.controlMos * 1.35, ...valid);
    return Math.ceil(maxVal * 2) / 2; // e.g. 2.0, 2.5, 3.0
  }

  mos12ToY(val: number): number {
    const clamped = Math.min(this.mos12ScaleMax, Math.max(0, val));
    return this.plot12Bottom - (clamped / this.mos12ScaleMax) * this.plot12H;
  }

  get controlMosY(): number {
    return this.mos12ToY(this.controlMos);
  }

  get mos12GridLines(): { val: number; y: number; label: string }[] {
    const lines = [];
    const step = this.mos12ScaleMax <= 2.5 ? 0.5 : 1.0;
    for (let v = step; v <= this.mos12ScaleMax; v += step) {
      lines.push({ val: v, y: this.mos12ToY(v), label: `${v.toFixed(1)} ด.` });
    }
    return lines;
  }

  getMosColor(val: number | null): string {
    if (val === null || val === undefined || !Number.isFinite(val)) return '#64748b';
    if (val > 1.5) return '#f472b6'; // สีชมพูอ่อน (> 1.5 เดือน เกินเกณฑ์ควบคุม)
    if (val < 0.5) return '#fde047'; // สีเหลืองอ่อน (< 0.5 เดือน สต็อกต่ำ)
    return '#86efac';               // สีเขียวอ่อน (0.5 - 1.5 เดือน ในเกณฑ์ควบคุม)
  }

  get mos12ChartItems(): any[] {
    const list = this.monthlyTrend12;
    if (!list.length) return [];
    const step = this.plot12W / list.length;
    return list.map((m: any, idx: number) => {
      const cx = this.plot12Left + (idx + 0.5) * step;
      let rawMos = m.mos !== null && m.mos !== undefined ? Number(m.mos) : null;
      // Resilient fallback: compute from end_stock / issue_value if backend mos was not cached
      if (this.data?.meta?.trend_mos_available !== false && !m.partial
          && (rawMos === null || !Number.isFinite(rawMos)) && m.issue_value && m.end_stock && Number(m.issue_value) > 0) {
        rawMos = Math.round((Number(m.end_stock) / Number(m.issue_value)) * 100) / 100;
      }
      const hasMos = rawMos !== null && Number.isFinite(rawMos) && rawMos >= 0;
      const y = hasMos ? this.mos12ToY(rawMos!) : this.plot12Bottom;
      const barH = Math.max(4, this.plot12Bottom - y);
      const barW = Math.min(28, Math.max(14, step * 0.48));
      const barX = cx - barW / 2;
      return {
        ...m,
        label: this.monthLabel(m.period),
        cx,
        rawMos,
        hasMos,
        barX,
        barY: y,
        barW,
        barH,
        y,
        color: this.getMosColor(rawMos),
      };
    });
  }

  get mos12LinePath(): string {
    const items = this.mos12ChartItems.filter(i => i.hasMos);
    if (items.length < 2) return '';
    return items.map((it, idx) => `${idx === 0 ? 'M' : 'L'} ${it.cx.toFixed(1)} ${it.y.toFixed(1)}`).join(' ');
  }

  latestMosText(): string {
    const items = this.mos12ChartItems.filter(i => i.hasMos);
    if (!items.length) return '-';
    const last = items[items.length - 1];
    return `${last.label}: ${this.number(last.rawMos, 2)} เดือน`;
  }

  get avg12MosNumber(): number {
    const items = this.mos12ChartItems.filter(i => i.hasMos);
    if (!items.length) return 0;
    const sum = items.reduce((acc, it) => acc + (it.rawMos || 0), 0);
    return Math.round((sum / items.length) * 100) / 100;
  }

  get avg12MosFormatted(): string {
    const items = this.mos12ChartItems.filter(i => i.hasMos);
    if (!items.length) return '-';
    return this.number(this.avg12MosNumber, 2);
  }

  get isAvg12InControl(): boolean {
    return this.avg12MosNumber <= this.controlMos && this.avg12MosNumber >= 0.5;
  }

  get avg12ControlStatusText(): string {
    const avg = this.avg12MosNumber;
    if (avg === 0) return '-';
    if (avg > this.controlMos) return 'สูงกว่าเกณฑ์ควบคุม (> 1.50 ด.)';
    if (avg < 0.5) return 'ต่ำกว่าเกณฑ์ (< 0.50 ด.)';
    return 'อยู่ในเกณฑ์ควบคุม (≤ 1.50 ด.)';
  }

  get avg12MosColor(): string {
    return this.getMosColor(this.avg12MosNumber);
  }

  avg12MosText(): string {
    return `${this.avg12MosFormatted} เดือน`;
  }

  /** อ่านรอบดึงข้อมูลจากเซิร์ฟเวอร์ ไม่ตรึงไว้ในหน้าจอ */
  syncHours(status: any): string {
    const seconds = Number(status?.interval_seconds || 0);
    if (!seconds) return '-';
    const hours = seconds / 3600;
    return hours >= 1 ? `${Number.isInteger(hours) ? hours : hours.toFixed(1)} ชม.` : `${Math.round(seconds / 60)} นาที`;
  }

  formatNextSync(isoStr: string | null): string {
    if (!isoStr) return 'กำลังคำนวณ...';
    try {
      const dt = new Date(isoStr);
      return dt.toLocaleTimeString('th-TH', { hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoStr;
    }
  }

  // Cash flow scale (Receipt vs Issue)
  get maxCashFlow(): number {
    const values = this.monthlyTrend.flatMap((m: any) => [Number(m.receipt_value || 0), Number(m.issue_value || 0)]);
    const maxVal = Math.max(10_000_000, ...values);
    return Math.ceil(maxVal / 25_000_000) * 25_000_000; // e.g. 100M
  }

  cashToY(val: number): number {
    const clamped = Math.min(this.maxCashFlow, Math.max(0, val));
    return this.plotBottom - (clamped / this.maxCashFlow) * this.plotH;
  }

  get cashGridLines(): { val: number; label: string; y: number }[] {
    const step = this.maxCashFlow / 4;
    const lines = [];
    for (let v = step; v <= this.maxCashFlow; v += step) {
      lines.push({ val: v, label: `฿${(v / 1_000_000).toFixed(0)}M`, y: this.cashToY(v) });
    }
    return lines;
  }

  get chartItems(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.plotW / list.length;

    return list.map((m: any, idx: number) => {
      const cx = this.plotLeft + (idx + 0.5) * step;
      const mosVal = Number(m.mos || 0);
      const isCapped = mosVal > this.mosScaleMax;
      const mosBarY = this.mosToY(mosVal);
      const mosBarH = Math.max(2, this.plotBottom - mosBarY);

      const rVal = Number(m.receipt_value || 0);
      const iVal = Number(m.issue_value || 0);
      const rY = this.cashToY(rVal);
      const rH = Math.max(2, this.plotBottom - rY);
      const iY = this.cashToY(iVal);
      const iH = Math.max(2, this.plotBottom - iY);

      return {
        ...m,
        label: this.monthLabel(m.period),
        cx,
        // MOS Chart
        mosVal,
        isCapped,
        mosX: cx - 14,
        mosY: mosBarY,
        mosW: 28,
        mosH: mosBarH,
        mosColor: this.mosColor(mosVal),
        // Cash Flow Chart (Dual bars)
        rX: cx - 13,
        rY,
        rW: 12,
        rH,
        iX: cx + 1,
        iY,
        iW: 12,
        iH,
        receiptCompact: this.compactMoney(rVal),
        issueCompact: this.compactMoney(iVal),
      };
    });
  }

  compactMoney(val: number): string {
    if (!val) return '฿0';
    if (val >= 1_000_000) return `฿${(val / 1_000_000).toFixed(1)}M`;
    if (val >= 1_000) return `฿${(val / 1_000).toFixed(0)}k`;
    return `฿${val.toFixed(0)}`;
  }

  mosColor(mos: number): string {
    if (!mos || !Number.isFinite(mos)) return '#64748b';
    if (mos < 7 / 30) return '#f43f5e';
    if (mos <= this.targetDays / 30) return '#10b981';
    return '#f59e0b'; // ส้ม = สต็อกเกินเกณฑ์ (> 2.0 เดือน)
  }

  mosStatusText(mos: number): string {
    if (!mos || !Number.isFinite(mos)) return 'ไม่มีข้อมูลการจ่าย';
    if (mos < 7 / 30) return 'เหลือน้อยกว่า 7 วัน ควรตรวจสอบ';
    if (mos <= this.targetDays / 30) return 'ไม่เกินเกณฑ์ที่กำหนด';
    return 'สำรองเกินเกณฑ์';
  }

  monthLabel(periodStr: string): string {
    if (!periodStr || periodStr.length < 6) return periodStr || '-';
    return `${periodStr.substring(4, 6)}/${periodStr.substring(2, 4)}`;
  }

  // เรียกผ่านเมธอดนี้แทนการผูก selectedTable=key ตรง ๆ ในเทมเพลต แล้วสั่ง
  // detectChanges() ให้ชัดเจน กันปัญหาคลิกแท็บแล้วตารางไม่เปลี่ยนตามที่เจอมา
  selectTable(key: string): void {
    this.selectedTable = key;
    this.sortKey = null; // แต่ละแท็บมีคอลัมน์ไม่เหมือนกัน สลับแท็บแล้วล้างการจัดเรียงเดิม
    this.cdr.detectChanges();
  }

  sortBy(column: ColumnDefinition): void {
    if (this.sortKey === column.key) {
      this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
      this.sortKey = column.key;
      // ตัวเลข/มูลค่า: เริ่มจากมากไปน้อย (ดูอันดับสูงสุดก่อน) ตัวหนังสือ: เริ่ม ก-ฮ
      this.sortDirection = this.isNumber(column.type) ? 'desc' : 'asc';
    }
    this.cdr.detectChanges();
  }

  sortIndicator(column: ColumnDefinition): string {
    if (this.sortKey !== column.key) return '';
    return this.sortDirection === 'asc' ? ' ▲' : ' ▼';
  }

  private sortRawValue(row: any, column: ColumnDefinition): number | string {
    const value = row?.[column.key];
    if (column.type === 'quantity') {
      return Array.isArray(value) ? value.reduce((sum: number, item: any) => sum + Number(item?.quantity || 0), 0) : 0;
    }
    if (this.isNumber(column.type)) {
      if (value === null || value === undefined) return -Infinity; // N/A ไปอยู่ท้ายสุดเมื่อเรียงจากมากไปน้อย
      const num = Number(value);
      return Number.isFinite(num) ? num : -Infinity;
    }
    return String(value ?? '').toLowerCase();
  }

  goToBucketTable(bucketKey: string): void {
    const tableKey = this.bucketTableMap[bucketKey];
    if (!tableKey) return;
    this.goToKpiTable(tableKey);
  }

  // การ์ดสรุปด้านบนแต่ละใบเชื่อมกับตารางรายการยาด้านล่างที่ประกอบขึ้นเป็นตัวเลขนั้น
  goToKpiTable(tableKey: string): void {
    this.selectTable(tableKey);
    if (this.viewMode === 'dashboard') {
      this.router.navigate(['/purchasing'], { queryParams: { tab: tableKey } });
    } else {
      document.getElementById('drug-review-table')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  money(value: any): string { return `฿${new Intl.NumberFormat('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value||0))}`; }
  number(value: any, digits = 0): string { return new Intl.NumberFormat('th-TH',{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(Number(value||0)); }
  actual(value: any): string { return new Intl.NumberFormat('th-TH',{maximumFractionDigits:4}).format(Number(value||0)); }
  quantities(items: any): string { return Array.isArray(items) && items.length ? items.map(item => `${this.actual(item.quantity)} ${item.unit || 'ไม่ระบุหน่วย'}`).join(' + ') : '—'; }
  date(value: any): string { if (!value) return '-'; const date = new Date(`${value}T00:00:00`); return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat('th-TH',{day:'numeric',month:'short',year:'numeric'}).format(date); }
  period(value: any): string { const text=String(value||''); if(!/^\d{6}$/.test(text)) return text||'-'; return new Intl.DateTimeFormat('th-TH',{month:'short',year:'numeric'}).format(new Date(+text.slice(0,4),+text.slice(4,6)-1,1)); }
  usagePeriodRange(): string { const periods=this.data?.meta?.usage_periods||[]; if(!periods.length) return '-'; return periods.length===1 ? this.period(periods[0]) : `${this.period(periods[0])} ถึง ${this.period(periods[periods.length-1])}`; }
  cell(row: any, column: ColumnDefinition): string {
    const value = row?.[column.key];
    if (value === null || value === undefined || value === '') {
      // days/number1 (จำนวนวัน/เดือนคงคลัง) เป็น null เฉพาะกรณีอัตราเบิกจ่ายเบาบางเกินกว่า
      // จะประมาณได้อย่างน่าเชื่อถือ (ดู MOS_UNRELIABLE_DAYS ฝั่ง backend) แยกจากค่าว่างทั่วไป
      return (column.type === 'days' || column.type === 'number1') ? 'N/A*' : '-';
    }
    if (column.type === 'money') return this.money(value);
    if (column.type === 'quantity') return this.quantities(value);
    if (column.type === 'number') return this.number(value);
    if (column.type === 'number1') return this.number(value,1);
    if (column.type === 'actual') return this.actual(value);
    if (column.type === 'days') return `${this.number(value,1)} วัน`;
    if (column.type === 'date') return this.date(value);
    return String(value);
  }
  isNumber(type: string): boolean { return ['money','quantity','number','number1','actual','days'].includes(type); }
  bucketWidth(value: any): number { const total = (this.data?.stock_buckets || []).reduce((sum: number,item: any)=>sum+Number(item.value||0),0); return total ? Number(value||0)/total*100 : 0; }
  departmentWidth(value: any): number { const max = Math.max(1, ...(this.data?.department_usage || []).map((item: any) => Number(item.value || 0))); return Number(value || 0) / max * 100; }

  expandedQuality: number | null = null;

  toggleQuality(index: number): void {
    this.expandedQuality = this.expandedQuality === index ? null : index;
    this.cdr.detectChanges();
  }

  qualitySampleColumns(item: any): string[] {
    return item?.sample?.length ? Object.keys(item.sample[0]) : [];
  }

  private readonly qualityColumnLabels: Record<string, string> = {
    SOURCE_LOTNO:'ล็อตที่บันทึก', SOURCE_RAW_QTY:'จำนวนจ่ายดิบ', SOURCE_PARENT_QTY:'จำนวนในใบจ่ายหลัก',
    SOURCE_MOS_REASON:'สาเหตุที่รอตรวจสอบ', SOURCE_FIFO_VALUE:'ต้นทุน FIFO ที่บันทึก', SOURCE_COST_STATUS:'สถานะตรวจต้นทุน',
    SOURCE_COST_LOTNO:'ล็อตอ้างอิงราคา', SOURCE_COST_BASIS:'แหล่งต้นทุน',
    WORKING_CODE: 'รหัสยา', name: 'ชื่อยา', RCV_NO: 'เลขที่ใบรับ', suffix: 'ลำดับ',
    IRNO: 'เลขที่ใบเบิก', SUFFIX: 'ลำดับ', DATE_RCV: 'วันที่รับ', TOTAL_VALUE: 'มูลค่ารับ',
    PERIOD_RPT: 'งวดเดือน', VALUE: 'มูลค่า', PACK_COST: 'ราคาต่อหน่วยบรรจุ', QTY_RCV: 'จำนวนรับ',
    QTY_DIS: 'จำนวนจ่าย', QTY_ONHAND: 'จำนวนคงคลัง', VALUE_ONHAND: 'มูลค่าคงคลัง',
    LOTNO: 'เลขรุ่นการผลิต', LOT_NO: 'เลขรุ่นการผลิต', EXPIRE_DATE: 'วันหมดอายุ', DIS: 'รหัสหน่วยเบิก', PACK_SIZE: 'ขนาดบรรจุ',
  };

  qualityColumnLabel(key: string): string {
    return this.qualityColumnLabels[key] || key;
  }

  get displayedDepartments(): any[] {
    const all = this.data?.department_usage || [];
    let filtered = all;
    if (this.departmentSearch.trim()) {
      const q = this.departmentSearch.trim().toLowerCase();
      filtered = all.filter((d: any) => String(d.department || '').toLowerCase().includes(q));
    }
    if (!this.showAllDepartments && !this.departmentSearch.trim()) {
      return filtered.slice(0, 10);
    }
    return filtered;
  }

  toggleShowAllDepartments(): void {
    this.showAllDepartments = !this.showAllDepartments;
    this.cdr.detectChanges();
  }

  openDepartmentModal(dept: any): void {
    this.selectedDepartment = dept;
    this.modalDrugSearch = '';
    document.body.style.overflow = 'hidden';
    this.cdr.detectChanges();
  }

  closeDepartmentModal(): void {
    this.selectedDepartment = null;
    this.modalDrugSearch = '';
    document.body.style.overflow = '';
    this.cdr.detectChanges();
  }

  leaderDeptModal: any = null;

  openDeptBreakdown(dept: any, scope: string, scopeTitle: string): void {
    this.leaderDeptModal = {
      dept,
      scope,
      scopeTitle,
      loading: true,
      data: null,
      search: '',
    };
    document.body.style.overflow = 'hidden';
    this.cdr.detectChanges();

    this.api.requisitionLeadersDeptBreakdown({
      months: this.leadersMonths,
      div: dept.division || '',
      dept: dept.dept || '',
      sec: dept.section || '',
      scope: scope,
    }).subscribe({
      next: (res: any) => {
        if (this.leaderDeptModal) {
          this.leaderDeptModal.loading = false;
          if (res?.status === 'success') {
            this.leaderDeptModal.data = res.data;
          }
          this.cdr.detectChanges();
        }
      },
      error: () => {
        if (this.leaderDeptModal) {
          this.leaderDeptModal.loading = false;
          this.cdr.detectChanges();
        }
      }
    });
  }

  closeDeptBreakdown(): void {
    this.leaderDeptModal = null;
    document.body.style.overflow = '';
    this.cdr.detectChanges();
  }

  get filteredDeptBreakdownItems(): any[] {
    if (!this.leaderDeptModal?.data?.items) return [];
    const q = (this.leaderDeptModal.search || '').trim().toLowerCase();
    if (!q) return this.leaderDeptModal.data.items;
    return this.leaderDeptModal.data.items.filter((it: any) =>
      String(it.stock_code || '').toLowerCase().includes(q) ||
      String(it.name || '').toLowerCase().includes(q) ||
      String(it.unit || '').toLowerCase().includes(q)
    );
  }

  @HostListener('window:keydown.escape')
  onEscapePress(): void {
    if (this.selectedDepartment) {
      this.closeDepartmentModal();
    }
    if (this.leaderDeptModal) {
      this.closeDeptBreakdown();
    }
  }

  get filteredModalDrugs(): any[] {
    if (!this.selectedDepartment?.top_drugs) return [];
    if (!this.modalDrugSearch.trim()) return this.selectedDepartment.top_drugs;
    const q = this.modalDrugSearch.trim().toLowerCase();
    return this.selectedDepartment.top_drugs.filter((d: any) =>
      String(d.code || '').toLowerCase().includes(q) ||
      String(d.name || '').toLowerCase().includes(q)
    );
  }

  deptMonthlyWidth(val: any, trend: any[]): number {
    const max = Math.max(1, ...(trend || []).map((t: any) => Number(t.value || 0)));
    return Math.max(2, (Number(val || 0) / max) * 100);
  }
}
