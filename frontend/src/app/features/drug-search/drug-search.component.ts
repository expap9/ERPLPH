import { Location } from '@angular/common';
import { ChangeDetectorRef, Component, HostListener, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { ApiService } from '../../core/api.service';

@Component({
  selector: 'app-drug-search',
  imports: [FormsModule],
  templateUrl: './drug-search.component.html',
  styleUrl: './drug-search.component.css',
})
export class DrugSearchComponent implements OnInit {
  query = '';
  status = 'all';
  // ERPLPH: กรองประเภทของ (ยา เวชภัณฑ์ พัสดุ ครุภัณฑ์ อาหาร งานจ้าง)
  group = '';
  groupOptions: any[] = [];
  page = 1;
  loading = false;
  error = '';
  results: any;
  detail: any;
  detailLoading = false;

  // Simple helper suggestions
  suggestions: any[] = [];
  showSuggestions = false;
  selectedIndex = -1;
  private searchTimer: any = null;

  rawKind = 'inventory';
  rawQuery = '';
  raw: any;
  rawLoading = false;
  expandedRecord = -1;
  investigation: any;
  investigationSaving = false;
  investigationMessage = '';

  // Procurement & Primary Vendor
  procurementNote = '';
  procurementSaving = false;
  procurementMessage = '';
  selectedSystemVendor = '';

  // Sub-tabs below monthly movement
  activeTab: 'po_status' | 'timeline' = 'po_status';
  poActionLoading = false;
  poActionMessage = '';
  showAddPoModal = false;
  newPo: any = {
    po_no: '',
    po_date: new Date().toISOString().slice(0, 10),
    vendor_name: '',
    trade_name: '',
    order_qty: 0,
    unit: '',
    unit_price: 0,
    status: 'pending_director',
    note: '',
  };
  receiveModalPo: any = null;
  receiveData: any = { rcv_no: '', lot_no: '', rcv_date: new Date().toISOString().slice(0, 10) };

  // Collapsible sections
  showCalculation = false;
  showFacts = false;
  showPastVendors = false; // ค่าเริ่มต้นคือย่อไว้ เพื่อให้เห็นการ์ดสรุปและตารางรับจ่ายได้ครบในหน้าจอ

  scrollToSection(sectionId: string): void {
    const el = document.getElementById(sectionId);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  readonly investigationStatuses = [
    { value: 'pending', label: 'รอตรวจสอบ' },
    { value: 'confirmed', label: 'ยืนยันว่าข้อมูลถูกต้อง' },
    { value: 'explained', label: 'ชี้แจงสาเหตุแล้ว' },
    { value: 'action', label: 'มีแผนแก้ไข/กำลังดำเนินการ' },
  ];

  constructor(
    private readonly api: ApiService,
    private readonly route: ActivatedRoute,
    private readonly cdr: ChangeDetectorRef,
    private readonly location: Location,
  ) {}

  ngOnInit(): void {
    this.api.scopes().subscribe({ next: options => { this.groupOptions = options?.groups || []; this.cdr.detectChanges(); }, error: () => {} });
    this.route.queryParamMap.subscribe(params => {
      this.query = params.get('q') || this.query;
      this.group = params.get('group') || this.group;
      this.search(1);
    });
  }

  onQueryInput(): void {
    if (this.searchTimer) {
      clearTimeout(this.searchTimer);
    }
    const trimmed = (this.query || '').trim();
    if (trimmed.length < 2) {
      this.suggestions = [];
      this.showSuggestions = false;
      this.selectedIndex = -1;
      return;
    }
    this.searchTimer = setTimeout(() => {
      this.api.searchDrugs(trimmed, this.status, 1, 8, this.group).subscribe({
        next: data => {
          this.suggestions = data?.items || [];
          this.showSuggestions = this.suggestions.length > 0;
          this.selectedIndex = -1;
          this.cdr.detectChanges();
        },
        error: () => {
          this.suggestions = [];
          this.showSuggestions = false;
        }
      });
    }, 150);
  }

  onInputFocus(): void {
    if (this.suggestions.length > 0 && (this.query || '').trim().length >= 2) {
      this.showSuggestions = true;
    }
  }

  selectSuggestion(item: any): void {
    this.query = item.name;
    this.showSuggestions = false;
    this.suggestions = [];
    this.open(item.WORKING_CODE);
  }

  onKeyDown(event: KeyboardEvent): void {
    if (!this.showSuggestions || this.suggestions.length === 0) {
      if (event.key === 'Enter') {
        this.search(1);
      }
      return;
    }

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      this.selectedIndex = Math.min(this.selectedIndex + 1, this.suggestions.length - 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (this.selectedIndex >= 0 && this.suggestions[this.selectedIndex]) {
        this.selectSuggestion(this.suggestions[this.selectedIndex]);
      } else {
        this.showSuggestions = false;
        this.search(1);
      }
    } else if (event.key === 'Escape') {
      this.showSuggestions = false;
    }
  }

  @HostListener('document:click')
  onDocClick(): void {
    this.showSuggestions = false;
  }

  documents: any[] = [];
  openDocuments = new Set<string>();

  /** A document number carries digits and length >= 3. */
  private looksLikeDocumentNumber(text: string): boolean {
    const tokens = text.split(' ');
    for (const token of tokens) {
      if (token.length >= 3 && /\d/.test(token)) {
        return true;
      }
    }
    return false;
  }

  toggleDocument(doc: any): void {
    const key = `${doc.type}:${doc.number}`;
    if (this.openDocuments.has(key)) this.openDocuments.delete(key);
    else this.openDocuments.add(key);
  }

  isDocumentOpen(doc: any): boolean {
    return this.openDocuments.has(`${doc.type}:${doc.number}`);
  }

  poStatusLabel(status: string): string {
    return {
      received: 'รับครบแล้ว',
      partially_received: 'รับบางส่วน',
      director_approved: 'ผอ. ลงนามแล้ว รอของ',
      pending_director: 'รอ ผอ. ลงนาม',
    }[status] || status || '-';
  }

  search(page = 1): void {
    const trimmed = (this.query || '').trim();
    this.showSuggestions = false;
    this.page = page;
    this.loading = true;
    this.error = '';
    this.detail = null;
    this.documents = [];
    this.openDocuments.clear();

    // Documents resolve first: whether any paperwork matched decides if the
    // drug list may jump straight into a single drug's detail page.
    const documents$ = this.looksLikeDocumentNumber(trimmed)
      ? this.api.searchDocuments(trimmed).pipe(catchError(() => of({ documents: [] })))
      : of({ documents: [] });

    documents$.subscribe((docData: any) => {
      this.documents = docData?.documents || [];
      // A single hit is what the user typed; open it rather than make them click.
      if (this.documents.length === 1) this.toggleDocument(this.documents[0]);
      this.runDrugSearch(trimmed, page);
    });
  }

  private runDrugSearch(trimmed: string, page: number): void {
    this.api.searchDrugs(trimmed, this.status, page, 24, this.group).subscribe({
      next: data => {
        this.results = data;
        this.loading = false;
        // A document number matched: show the paperwork, not a drug list.
        if (this.documents.length) {
          this.cdr.detectChanges();
          return;
        }
        // Direct jump into detail if single result or exact code match
        if (data?.items?.length === 1) {
          this.open(data.items[0].WORKING_CODE);
          return;
        }
        if (trimmed) {
          const exactCode = data?.items?.find((i: any) => String(i.WORKING_CODE).trim() === trimmed);
          if (exactCode) {
            this.open(exactCode.WORKING_CODE);
            return;
          }
        }
        this.cdr.detectChanges();
      },
      error: err => {
        this.loading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      },
    });
  }

  open(code: string): void {
    this.showSuggestions = false;
    this.detailLoading = true;
    this.error = '';
    this.api.drugDetail(code).subscribe({
      next: data => {
        this.detail = data;
        this.prepareInvestigation(data);
        this.procurementNote = data?.procurement_info?.primary_vendor?.note || data?.procurement_info?.note || '';
        this.procurementMessage = '';
        this.selectedSystemVendor = '';
        this.detailLoading = false;
        this.rawKind = 'inventory';
        this.cdr.detectChanges();
        this.loadRaw(1);
        window.scrollTo({ top: 0, behavior: 'smooth' });
      },
      error: err => {
        this.detailLoading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      },
    });
  }

  closeDetail(): void {
    this.detail = null;
    this.raw = null;
    this.investigation = null;
    this.procurementMessage = '';
  }

  goBack(): void {
    if (window.history.length > 1) {
      this.location.back();
    } else {
      this.closeDetail();
    }
  }

  setPrimaryVendor(vendor: any): void {
    if (!this.detail) return;
    this.procurementSaving = true;
    this.procurementMessage = '';
    const payload = {
      vendor_name: vendor.vendor_name,
      supplier_code: vendor.supplier_code || '',
      trade_name: vendor.trade_name || (this.detail.trade_names?.[0] || ''),
      tpuid: vendor.tpuid || (this.detail.tpuids?.[0] || ''),
      note: this.procurementNote,
      is_manual: 1,
    };
    this.api.savePrimaryVendor(this.detail.code, payload).subscribe({
      next: data => {
        this.procurementSaving = false;
        this.procurementMessage = 'บันทึกบริษัทหลักเรียบร้อยแล้ว';
        if (this.detail.procurement_info) {
          this.detail.procurement_info.primary_vendor = data;
          for (const v of this.detail.procurement_info.past_year_vendors || []) {
            v.is_primary = (v.vendor_name === data.vendor_name);
          }
        }
        this.cdr.detectChanges();
        setTimeout(() => { this.procurementMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.procurementSaving = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      },
    });
  }

  saveProcurementNote(): void {
    if (!this.detail) return;
    this.procurementSaving = true;
    this.procurementMessage = '';
    const curPrimary = this.detail.procurement_info?.primary_vendor || {};
    const payload = {
      vendor_name: curPrimary.vendor_name || '',
      supplier_code: curPrimary.supplier_code || '',
      trade_name: curPrimary.trade_name || (this.detail.trade_names?.[0] || ''),
      tpuid: curPrimary.tpuid || (this.detail.tpuids?.[0] || ''),
      note: this.procurementNote,
      is_manual: curPrimary.is_manual ?? 1,
    };
    this.api.savePrimaryVendor(this.detail.code, payload).subscribe({
      next: data => {
        this.procurementSaving = false;
        this.procurementMessage = 'บันทึกเงื่อนไข/หมายเหตุการจัดซื้อแล้ว';
        if (this.detail.procurement_info) {
          if (!this.detail.procurement_info.primary_vendor) {
            this.detail.procurement_info.primary_vendor = data;
          } else {
            this.detail.procurement_info.primary_vendor.note = data.note;
          }
          this.detail.procurement_info.note = data.note;
        }
        this.cdr.detectChanges();
        setTimeout(() => { this.procurementMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.procurementSaving = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      },
    });
  }

  setSystemVendorAsPrimary(): void {
    if (!this.detail || !this.selectedSystemVendor) return;
    const sysVendors = this.detail.procurement_info?.system_vendors || [];
    const found = sysVendors.find((v: any) => v.vendor_name === this.selectedSystemVendor);
    this.setPrimaryVendor({
      vendor_name: this.selectedSystemVendor,
      supplier_code: found?.vendor_code || '',
      trade_name: this.detail.trade_names?.[0] || '',
      tpuid: this.detail.tpuids?.[0] || '',
    });
  }

  saveInvestigation(): void {
    if (!this.detail || !this.investigation) return;
    this.investigationSaving = true; this.investigationMessage = ''; this.error = '';
    this.api.saveInvestigation(this.detail.code, this.investigation).subscribe({
      next: data => { this.investigationSaving = false; this.investigation = data.investigation; this.investigationMessage = data.message; this.cdr.detectChanges(); },
      error: err => { this.investigationSaving = false; this.error = this.errorMessage(err); this.cdr.detectChanges(); },
    });
  }

  selectRaw(kind: string): void { this.rawKind = kind; this.rawQuery = ''; this.expandedRecord = -1; this.loadRaw(1); }
  loadRaw(page = 1): void {
    if (!this.detail) return;
    this.rawLoading = true;
    this.api.drugRecords(this.detail.code, this.rawKind, page, this.rawQuery).subscribe({
      next: data => { this.raw = data; this.rawLoading = false; this.cdr.detectChanges(); },
      error: err => { this.rawLoading = false; this.error = this.errorMessage(err); this.cdr.detectChanges(); },
    });
  }

  selectTab(tab: 'po_status' | 'timeline'): void {
    this.activeTab = tab;
  }

  getPoVendor(item: any): string {
    return item?.vendor_name || this.detail?.procurement_info?.primary_vendor?.vendor_name || 'ไม่ระบุผู้ขาย';
  }

  getPoTradeName(item: any): string {
    return item?.trade_name || this.detail?.procurement_info?.primary_vendor?.trade_name || '';
  }

  getPoTooltip(item: any): string {
    if (!item) return '';
    const poNo = item.po_no || '';
    const lines: string[] = [];
    if (poNo && poNo !== '-') lines.push(`ใบ PO: ${poNo}`);
    const vendor = this.getPoVendor(item);
    if (vendor && vendor !== 'ไม่ระบุผู้ขาย') lines.push(`บริษัทผู้ขาย: ${vendor}`);
    const trade = this.getPoTradeName(item);
    if (trade) lines.push(`ชื่อการค้า: ${trade}`);
    const code = item.supplier_code || this.detail?.procurement_info?.primary_vendor?.supplier_code || '';
    if (code) lines.push(`รหัสผู้ขาย: ${code}`);
    const price = item.unit_cost || item.unit_price || 0;
    if (price > 0) lines.push(`ราคาต่อหน่วย: ฿${price.toLocaleString('th-TH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} / ${item.unit || 'หน่วย'}`);
    const rcv = item.rcv_no || item.received_rcv_no || '';
    if (rcv && rcv !== '-') lines.push(`เลขที่ใบรับ: ${rcv}`);
    const dateStr = item.date_rcv || item.received_date || '';
    if (dateStr) lines.push(`วันที่ตรวจรับ: ${this.date(dateStr)}`);
    const lot = item.lot_no || item.received_lot_no || '';
    if (lot && lot !== '-') lines.push(`Lot: ${lot}`);
    return lines.length ? lines.join('\n') : (poNo ? `ใบ PO: ${poNo}` : '');
  }

  togglePoDispatch(po: any): void {
    if (!this.detail) return;
    this.poActionLoading = true;
    this.poActionMessage = '';
    const newStatus = !po.is_sent_to_vendor;
    this.api.updatePoDispatch(po.po_no, newStatus, po.sent_to_vendor_note || '').subscribe({
      next: () => {
        this.poActionLoading = false;
        po.is_sent_to_vendor = newStatus ? 1 : 0;
        if (newStatus && !po.sent_to_vendor_date) {
          po.sent_to_vendor_date = new Date().toISOString().slice(0, 10);
        }
        this.poActionMessage = newStatus ? `ติ๊กส่งข้อมูลใบสั่งซื้อ ${po.po_no} ให้บริษัทแล้ว` : `ยกเลิกสถานะส่งข้อมูลใบสั่งซื้อ ${po.po_no}`;
        this.reloadPoData();
        setTimeout(() => { this.poActionMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.poActionLoading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      }
    });
  }

  /** POs whose note the user reopened for editing, keyed like the table rows. */
  private readonly notesBeingEdited = new Set<string>();

  private poKey(po: any): string {
    return `${po?.po_no}_${po?.working_code}`;
  }

  /** An empty note has nothing to show, so it stays an input until first saved. */
  isEditingNote(po: any): boolean {
    return this.notesBeingEdited.has(this.poKey(po)) || !(po?.sent_to_vendor_note || '').trim();
  }

  editPoNote(po: any): void {
    this.notesBeingEdited.add(this.poKey(po));
  }

  savePoNote(po: any): void {
    if (!this.detail) return;
    this.poActionLoading = true;
    this.poActionMessage = '';
    this.api.updatePoDispatch(po.po_no, !!po.is_sent_to_vendor, po.sent_to_vendor_note || '').subscribe({
      next: () => {
        this.poActionLoading = false;
        this.notesBeingEdited.delete(this.poKey(po));
        this.poActionMessage = `บันทึกรายละเอียดใบสั่งซื้อ ${po.po_no} เรียบร้อยแล้ว`;
        this.reloadPoData();
        setTimeout(() => { this.poActionMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.poActionLoading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      }
    });
  }

  openAddPoModal(): void {
    const primary = this.detail?.procurement_info?.primary_vendor;
    this.newPo = {
      po_no: '',
      po_date: new Date().toISOString().slice(0, 10),
      vendor_name: primary?.vendor_name || this.detail?.vendors?.[0] || '',
      trade_name: primary?.trade_name || this.detail?.trade_names?.[0] || '',
      order_qty: 0,
      unit: this.detail?.summary?.stock_quantities?.[0]?.unit || 'BOT',
      unit_price: 0,
      status: 'pending_director',
      sent_to_vendor_note: '',
    };
    this.showAddPoModal = true;
  }

  submitAddPo(): void {
    if (!this.detail || !this.newPo.po_no.trim()) return;
    this.poActionLoading = true;
    this.api.saveDrugPo(this.detail.code, this.newPo).subscribe({
      next: () => {
        this.poActionLoading = false;
        this.showAddPoModal = false;
        this.poActionMessage = `เพิ่มใบสั่งซื้อ ${this.newPo.po_no} แล้ว`;
        this.reloadPoData();
        setTimeout(() => { this.poActionMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.poActionLoading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      }
    });
  }

  openReceiveModal(po: any): void {
    this.receiveModalPo = po;
    this.receiveData = {
      rcv_no: '',
      lot_no: '',
      rcv_date: new Date().toISOString().slice(0, 10)
    };
  }

  submitReceivePo(): void {
    if (!this.receiveModalPo) return;
    this.poActionLoading = true;
    this.api.markPoReceived(
      this.receiveModalPo.po_no,
      this.receiveData.rcv_no,
      this.receiveData.lot_no,
      this.receiveData.rcv_date
    ).subscribe({
      next: () => {
        this.poActionLoading = false;
        this.poActionMessage = `ตรวจรับเข้าคลังเรียบร้อยสำหรับใบสั่งซื้อ ${this.receiveModalPo.po_no}`;
        this.receiveModalPo = null;
        this.reloadPoData();
        setTimeout(() => { this.poActionMessage = ''; this.cdr.detectChanges(); }, 4000);
      },
      error: err => {
        this.poActionLoading = false;
        this.error = this.errorMessage(err);
        this.cdr.detectChanges();
      }
    });
  }

  reloadPoData(): void {
    if (!this.detail) return;
    this.api.getDrugPos(this.detail.code).subscribe({
      next: data => {
        if (this.detail) {
          this.detail.po_tracking = data;
        }
        this.cdr.detectChanges();
      },
      error: () => {}
    });
  }

  money(value: any): string { return `฿${new Intl.NumberFormat('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(value||0))}`; }
  number(value: any, digits = 0): string { return new Intl.NumberFormat('th-TH',{minimumFractionDigits:digits,maximumFractionDigits:digits}).format(Number(value||0)); }
  actual(value: any): string {
    const num = Number(value || 0);
    const maxDigits = Number.isInteger(num) ? 0 : 2;
    return new Intl.NumberFormat('th-TH', { minimumFractionDigits: 0, maximumFractionDigits: maxDigits }).format(num);
  }
  quantities(items: any): string {
    if (!Array.isArray(items) || !items.length) return '0';
    return items.map(item => {
      const qty = this.actual(item.quantity);
      const u = item.unit || 'ไม่ระบุหน่วย';
      if (item.pack_unit && item.pack_quantity) {
        return `${qty} ${u} (${this.actual(item.pack_quantity)} ${item.pack_unit})`;
      }
      return `${qty} ${u}`;
    }).join(' + ');
  }
  baseQuantities(items: any): string {
    if (!Array.isArray(items) || !items.length) return '0';
    return items.map(item => {
      const rawUnit = String(item.unit || 'ไม่ระบุหน่วย').replace(/\s*\([^)]*\)/g, '').trim();
      return `${this.actual(item.quantity)} ${rawUnit || 'ไม่ระบุหน่วย'}`;
    }).join(' + ');
  }
  date(value: any): string { if (!value) return '-'; const text=String(value); let d:Date; if(/^\d{8}$/.test(text)){d=new Date(+text.slice(0,4),+text.slice(4,6)-1,+text.slice(6,8));}else{d=new Date(`${text}T00:00:00`);} return Number.isNaN(d.getTime())?text:new Intl.DateTimeFormat('th-TH',{day:'numeric',month:'short',year:'numeric'}).format(d); }
  period(value: any): string { const text=String(value||''); if(!/^\d{6}$/.test(text)) return text||'-'; return new Intl.DateTimeFormat('th-TH',{month:'short',year:'numeric'}).format(new Date(+text.slice(0,4),+text.slice(4,6)-1,1)); }
  rawSummary(record: any): string {
    const v=record.values||{};
    if(this.rawKind==='receipt') return `${this.date(v.DATE_RCV)} · รับ ${v.QTY_RCV||0} ${v.STDIRUNITCODE||''} · ใบรับ ${v.RCV_NO||'-'} · รุ่นการผลิต ${v.LOT_NO||'-'}`;
    if(this.rawKind==='distribution') return `${this.period(v.PERIOD_RPT)} · จ่าย ${v.QTY_DIS||0} ${v.ISSUEUNITCODE||''} · ใบเบิก ${v.IRNO||'-'} · หน่วยงาน ${v.DIS||'-'}`;
    return `คงเหลือ ${v.QTY_ONHAND||0} ${v.STDIRUNITCODE||''} · รุ่นการผลิต ${v.LOTNO||'-'} · หมดอายุ ${this.date(v.EXPIRE_DATE)}`;
  }
  rawValue(value: any): string { const text=String(value??'').trim(); return text||'ว่าง'; }
  private prepareInvestigation(data: any): void {
    const saved = data?.investigation || {};
    const answers = { ...(saved.answers || {}) };
    for (const item of data?.question_items || []) {
      answers[item.id] = {
        question: item.text,
        status: answers[item.id]?.status || 'pending',
        answer: answers[item.id]?.answer || '',
      };
    }
    this.investigation = {
      overall_status: saved.overall_status || 'pending',
      respondent: saved.respondent || '',
      reviewed_at: saved.reviewed_at || '',
      answers,
    };
  }
  private errorMessage(error: any): string { return error?.error?.message || error?.message || 'อ่านข้อมูลไม่สำเร็จ'; }

  // ==========================================
  // KPI Drilldown Modal State & SVG Charts
  // ==========================================
  activeKpiModal: 'inventory' | 'receipt' | 'distribution' | 'issue_rate' | 'mos' | null = null;
  kpiMetric: 'qty' | 'value' = 'qty';
  kpiDeptSearch: string = '';
  activeKpiHover: any = null;
  selectedReceiptMonth: string | null = null;

  openKpiModal(type: 'inventory' | 'receipt' | 'distribution' | 'issue_rate' | 'mos'): void {
    this.activeKpiModal = type;
    this.activeKpiHover = null;
    this.selectedReceiptMonth = null;
    this.kpiDeptSearch = '';
    this.cdr.detectChanges();
  }

  closeKpiModal(): void {
    this.activeKpiModal = null;
    this.activeKpiHover = null;
    this.selectedReceiptMonth = null;
    this.cdr.detectChanges();
  }

  selectKpiTab(type: 'inventory' | 'receipt' | 'distribution' | 'issue_rate' | 'mos'): void {
    this.activeKpiModal = type;
    this.activeKpiHover = null;
    this.cdr.detectChanges();
  }

  setKpiHover(item: any): void {
    this.activeKpiHover = item;
    this.cdr.detectChanges();
  }

  clearKpiHover(): void {
    this.activeKpiHover = null;
    this.cdr.detectChanges();
  }

  toggleReceiptMonth(period: string): void {
    this.selectedReceiptMonth = this.selectedReceiptMonth === period ? null : period;
    this.cdr.detectChanges();
  }

  shortMonth(value: any): string {
    const text = String(value || '');
    if (!/^\d{6}$/.test(text)) return text || '-';
    const y = (+text.slice(0, 4) + 543) % 100;
    const m = +text.slice(4, 6);
    const th = ['', 'ม.ค.', 'ก.พ.', 'มี.ค.', 'เม.ย.', 'พ.ค.', 'มิ.ย.', 'ก.ค.', 'ส.ค.', 'ก.ย.', 'ต.ค.', 'พ.ย.', 'ธ.ค.'];
    return `${th[m] || m}'${y.toString().padStart(2, '0')}`;
  }

  get drugUnit(): string {
    return this.detail?.summary?.stock_quantities?.[0]?.unit ||
           this.detail?.summary?.receipt_quantities?.[0]?.unit ||
           this.detail?.summary?.issue_quantities?.[0]?.unit || 'หน่วย';
  }

  get monthlyTrend(): any[] {
    return this.detail?.monthly_trend || [];
  }

  get departmentList(): any[] {
    return this.detail?.department_distribution || [];
  }

  get filteredDepartments(): any[] {
    const list = this.departmentList;
    if (!this.kpiDeptSearch.trim()) return list;
    const q = this.kpiDeptSearch.trim().toLowerCase();
    return list.filter(d => (d.dept_name || '').toLowerCase().includes(q) || (d.dept_code || '').toLowerCase().includes(q));
  }

  get auditSignals(): any {
    return this.detail?.audit_signals || {
      overall_risk: 'normal',
      risk_label: 'ปกติ',
      risk_color: 'emerald',
      findings: ['ข้อมูลการกระจายยาเป็นไปตามปกติ'],
    };
  }

  get allReceiptItems(): any[] {
    const items: any[] = [];
    for (const m of (this.detail?.monthly_movement || [])) {
      if (this.selectedReceiptMonth && m.period !== this.selectedReceiptMonth) continue;
      for (const r of (m.receipt_items || [])) {
        items.push({ ...r, period: m.period });
      }
    }
    return items;
  }

  // --- Chart Coordinates & Dimensions ---
  readonly kpiSvgW = 820;
  readonly kpiSvgH = 250;
  readonly kpiPlotLeft = 65;
  readonly kpiPlotRight = 790;
  readonly kpiPlotTop = 25;
  readonly kpiPlotBottom = 205;

  get kpiPlotW(): number { return this.kpiPlotRight - this.kpiPlotLeft; }
  get kpiPlotH(): number { return this.kpiPlotBottom - this.kpiPlotTop; }

  // 1. Inventory Chart
  get maxStockVal(): number {
    const vals = this.monthlyTrend.map(m => this.kpiMetric === 'qty' ? Number(m.stock_qty || 0) : Number(m.stock_value || 0));
    const max = Math.max(10, ...vals);
    return Math.ceil(max * 1.18);
  }

  stockToY(val: number): number {
    const clamped = Math.min(this.maxStockVal, Math.max(0, val));
    return this.kpiPlotBottom - (clamped / (this.maxStockVal || 1)) * this.kpiPlotH;
  }

  get stockGridLines(): { val: number; y: number; label: string }[] {
    const max = this.maxStockVal;
    const lines = [];
    const step = max / 4;
    for (let i = 1; i <= 4; i++) {
      const v = step * i;
      lines.push({
        val: v,
        y: this.stockToY(v),
        label: this.kpiMetric === 'qty' ? this.number(v) : `฿${this.number(v)}`,
      });
    }
    return lines;
  }

  get stockPoints(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.kpiPlotW / Math.max(1, list.length - 1);
    return list.map((m, idx) => {
      const cx = this.kpiPlotLeft + idx * step;
      const rawVal = this.kpiMetric === 'qty' ? Number(m.stock_qty || 0) : Number(m.stock_value || 0);
      const cy = this.stockToY(rawVal);
      return {
        ...m,
        idx,
        cx,
        cy,
        rawVal,
        label: this.shortMonth(m.period),
      };
    });
  }

  get stockLinePath(): string {
    const pts = this.stockPoints;
    if (pts.length < 2) return '';
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.cx.toFixed(1)} ${p.cy.toFixed(1)}`).join(' ');
  }

  get stockAreaPath(): string {
    const pts = this.stockPoints;
    if (pts.length < 2) return '';
    const line = this.stockLinePath;
    const last = pts[pts.length - 1];
    const first = pts[0];
    return `${line} L ${last.cx.toFixed(1)} ${this.kpiPlotBottom} L ${first.cx.toFixed(1)} ${this.kpiPlotBottom} Z`;
  }

  // 2. Receipts Bar Chart
  get maxReceiptVal(): number {
    const vals = this.monthlyTrend.map(m => this.kpiMetric === 'qty' ? Number(m.receipt_qty || 0) : Number(m.receipt_value || 0));
    const max = Math.max(10, ...vals);
    return Math.ceil(max * 1.2);
  }

  receiptToY(val: number): number {
    const clamped = Math.min(this.maxReceiptVal, Math.max(0, val));
    return this.kpiPlotBottom - (clamped / (this.maxReceiptVal || 1)) * this.kpiPlotH;
  }

  get receiptGridLines(): { val: number; y: number; label: string }[] {
    const max = this.maxReceiptVal;
    const lines = [];
    const step = max / 4;
    for (let i = 1; i <= 4; i++) {
      const v = step * i;
      lines.push({
        val: v,
        y: this.receiptToY(v),
        label: this.kpiMetric === 'qty' ? this.number(v) : `฿${this.number(v)}`,
      });
    }
    return lines;
  }

  get receiptBars(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.kpiPlotW / list.length;
    return list.map((m, idx) => {
      const cx = this.kpiPlotLeft + (idx + 0.5) * step;
      const rawVal = this.kpiMetric === 'qty' ? Number(m.receipt_qty || 0) : Number(m.receipt_value || 0);
      const y = this.receiptToY(rawVal);
      const barH = Math.max(rawVal > 0 ? 4 : 0, this.kpiPlotBottom - y);
      const barW = Math.min(26, Math.max(10, step * 0.5));
      const barX = cx - barW / 2;
      return {
        ...m,
        cx,
        barX,
        barY: y,
        barW,
        barH,
        rawVal,
        label: this.shortMonth(m.period),
      };
    });
  }

  // 3. Distribution & Issue Bar Chart
  get maxIssueVal(): number {
    const vals = this.monthlyTrend.map(m => this.kpiMetric === 'qty' ? Number(m.issue_qty || 0) : Number(m.issue_value || 0));
    const max = Math.max(10, ...vals);
    return Math.ceil(max * 1.25);
  }

  issueToY(val: number): number {
    const clamped = Math.min(this.maxIssueVal, Math.max(0, val));
    return this.kpiPlotBottom - (clamped / (this.maxIssueVal || 1)) * this.kpiPlotH;
  }

  get issueGridLines(): { val: number; y: number; label: string }[] {
    const max = this.maxIssueVal;
    const lines = [];
    const step = max / 4;
    for (let i = 1; i <= 4; i++) {
      const v = step * i;
      lines.push({
        val: v,
        y: this.issueToY(v),
        label: this.kpiMetric === 'qty' ? this.number(v) : `฿${this.number(v)}`,
      });
    }
    return lines;
  }

  get issueBars(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.kpiPlotW / list.length;
    return list.map((m, idx) => {
      const cx = this.kpiPlotLeft + (idx + 0.5) * step;
      const rawVal = this.kpiMetric === 'qty' ? Number(m.issue_qty || 0) : Number(m.issue_value || 0);
      const y = this.issueToY(rawVal);
      const barH = Math.max(rawVal > 0 ? 4 : 0, this.kpiPlotBottom - y);
      const barW = Math.min(26, Math.max(10, step * 0.5));
      const barX = cx - barW / 2;
      return {
        ...m,
        cx,
        barX,
        barY: y,
        barW,
        barH,
        rawVal,
        label: this.shortMonth(m.period),
      };
    });
  }

  // 4. Issue Rate Trend (Line with 3M Moving Average)
  get maxRateVal(): number {
    const vals = this.monthlyTrend.flatMap(m => [Number(m.issue_qty || 0), Number(m.moving_avg_issue_3m || 0)]);
    const max = Math.max(10, ...vals);
    return Math.ceil(max * 1.25);
  }

  rateToY(val: number): number {
    const clamped = Math.min(this.maxRateVal, Math.max(0, val));
    return this.kpiPlotBottom - (clamped / (this.maxRateVal || 1)) * this.kpiPlotH;
  }

  get rateGridLines(): { val: number; y: number; label: string }[] {
    const max = this.maxRateVal;
    const lines = [];
    const step = max / 4;
    for (let i = 1; i <= 4; i++) {
      const v = step * i;
      lines.push({ val: v, y: this.rateToY(v), label: this.number(v) });
    }
    return lines;
  }

  get ratePoints(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.kpiPlotW / Math.max(1, list.length - 1);
    return list.map((m, idx) => {
      const cx = this.kpiPlotLeft + idx * step;
      const actualY = this.rateToY(Number(m.issue_qty || 0));
      const ma3Y = this.rateToY(Number(m.moving_avg_issue_3m || 0));
      return {
        ...m,
        idx,
        cx,
        actualY,
        ma3Y,
        label: this.shortMonth(m.period),
      };
    });
  }

  get rateActualPath(): string {
    const pts = this.ratePoints;
    if (pts.length < 2) return '';
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.cx.toFixed(1)} ${p.actualY.toFixed(1)}`).join(' ');
  }

  get rateMa3Path(): string {
    const pts = this.ratePoints;
    if (pts.length < 2) return '';
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.cx.toFixed(1)} ${p.ma3Y.toFixed(1)}`).join(' ');
  }

  // 5. MOS Bar Chart with Control Thresholds
  get maxMosVal(): number {
    const valid = this.monthlyTrend.filter(m => m.mos !== null && m.mos !== undefined).map(m => Number(m.mos || 0));
    const max = Math.max(2.2, ...valid);
    return Math.ceil(max * 1.2 * 2) / 2;
  }

  mosToY(val: number): number {
    const clamped = Math.min(this.maxMosVal, Math.max(0, val));
    return this.kpiPlotBottom - (clamped / (this.maxMosVal || 1)) * this.kpiPlotH;
  }

  get mosTarget1_0_Y(): number { return this.mosToY(1.0); }
  get mosControl1_5_Y(): number { return this.mosToY(1.5); }

  get mosGridLines(): { val: number; y: number; label: string }[] {
    const lines = [];
    const step = this.maxMosVal <= 3 ? 0.5 : 1.0;
    for (let v = step; v <= this.maxMosVal; v += step) {
      lines.push({ val: v, y: this.mosToY(v), label: `${v.toFixed(1)} ด.` });
    }
    return lines;
  }

  getMosBarColor(mos: number | null): string {
    if (mos === null || mos === undefined || !Number.isFinite(mos)) return '#64748b';
    if (mos < 0.5) return '#ef4444'; // Red (วิกฤตยาขาด)
    if (mos < 1.0) return '#f97316'; // Orange (ต่ำกว่าเกณฑ์ 1 เดือน)
    if (mos <= 1.5) return '#10b981'; // Emerald (1.0 - 1.5 เดือน ในเกณฑ์ควบคุม)
    if (mos <= 2.0) return '#f59e0b'; // Amber (1.5 - 2.0 เดือน เฝ้าระวังสะสม)
    return '#ec4899'; // Pink/Rose (> 2.0 เดือน ค้างสต็อกเกินเกณฑ์)
  }

  getMosStatusText(mos: number | null): string {
    if (mos === null || mos === undefined || !Number.isFinite(mos)) return 'ไม่พบข้อมูลอัตราจ่าย';
    if (mos < 0.5) return 'วิกฤตยาใกล้ขาด (< 0.5 ด.)';
    if (mos < 1.0) return 'ต่ำกว่าเกณฑ์ 1 เดือน (< 1.0 ด.)';
    if (mos <= 1.5) return 'เหมาะสมในเกณฑ์ควบคุม (1.0 - 1.5 ด.)';
    if (mos <= 2.0) return 'เฝ้าระวังคงคลังสะสม (1.5 - 2.0 ด.)';
    return 'คงคลังเกินเกณฑ์ (> 2.0 ด.)';
  }

  get mosBars(): any[] {
    const list = this.monthlyTrend;
    if (!list.length) return [];
    const step = this.kpiPlotW / list.length;
    return list.map((m, idx) => {
      const cx = this.kpiPlotLeft + (idx + 0.5) * step;
      const rawMos = m.mos !== null && m.mos !== undefined ? Number(m.mos) : null;
      const hasMos = rawMos !== null && Number.isFinite(rawMos) && rawMos >= 0;
      const y = hasMos ? this.mosToY(rawMos!) : this.kpiPlotBottom;
      const barH = Math.max(hasMos ? 4 : 0, this.kpiPlotBottom - y);
      const barW = Math.min(26, Math.max(10, step * 0.5));
      const barX = cx - barW / 2;
      return {
        ...m,
        cx,
        barX,
        barY: y,
        barW,
        barH,
        rawMos,
        hasMos,
        color: this.getMosBarColor(rawMos),
        statusText: this.getMosStatusText(rawMos),
        label: this.shortMonth(m.period),
      };
    });
  }
}
