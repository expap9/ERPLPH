import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';

@Injectable({ providedIn: 'root' })
export class ApiService {
  constructor(private readonly http: HttpClient) {}

  status(): Observable<any> { return this.http.get('/api/status'); }
  pull(payload: any = {}): Observable<any> { return this.http.post('/api/db/pull', payload); }
  pullMonitor(): Observable<any> { return this.http.post('/api/monitor/pull', {}); }
  clearAllData(): Observable<any> { return this.http.post('/api/json/clear', {}); }

  dbConfig(): Observable<any> { return this.http.get('/api/db/config'); }
  saveDbConfig(payload: any): Observable<any> { return this.http.post('/api/db/config', payload); }
  testDb(payload: any): Observable<any> { return this.http.post('/api/db/test', payload); }
  checkSchema(payload: any): Observable<any> { return this.http.post('/api/db/schema-check', payload); }

  senderSettings(): Observable<any> { return this.http.get('/api/sender/settings'); }
  saveSenderSettings(payload: any): Observable<any> { return this.http.post('/api/sender/settings', payload); }
  setSenderToken(environment: string, token: string): Observable<any> {
    return this.http.post('/api/sender/token', { environment, token });
  }
  clearSenderToken(environment: string): Observable<any> {
    return this.http.post('/api/sender/token', { environment, clear: true });
  }
  senderPreflight(payload: any): Observable<any> { return this.http.post('/api/sender/preflight', payload); }
  senderImportAccess(payload: any): Observable<any> { return this.http.post('/api/sender/import-access', payload); }
  sendMophFile(fileKey: string, payload: any): Observable<any> {
    return this.http.post(`/api/sender/send/${encodeURIComponent(fileKey)}`, payload);
  }
  senderHistory(limit = 50): Observable<any> {
    return this.http.get('/api/sender/history', { params: new HttpParams().set('limit', limit) });
  }

  tableMappings(): Observable<any> { return this.http.get('/api/db/table-mappings'); }
  saveTableMappings(mappings: Record<string, string>): Observable<any> {
    return this.http.put('/api/db/table-mappings', { mappings });
  }
  resetTableMappings(): Observable<any> { return this.http.post('/api/db/table-mappings/reset', {}); }
  sqlPreview(file = ''): Observable<any> {
    const params = file ? new HttpParams().set('file', file) : undefined;
    return this.http.get('/api/db/sql-preview', { params });
  }

  monitor(targetDays = 30, usageMonths = 6, expiryDays = 240, group = '', store = ''): Observable<any> {
    let params = new HttpParams()
      .set('target_days', targetDays)
      .set('usage_months', usageMonths)
      .set('expiry_days', expiryDays);
    if (group) params = params.set('group', group);
    if (store) params = params.set('store', store);
    return this.http.get('/api/monitor/summary', { params });
  }
  /** ตัวเลือกขอบเขตของ ERPLPH — ประเภทของและคลังที่มีข้อมูล */
  scopes(): Observable<any> { return this.http.get('/api/scopes'); }
  uploadMonitor(files: Record<string, File | null>): Observable<any> {
    const form = new FormData();
    if (files['receipt']) form.append('receipt_file', files['receipt']);
    if (files['distribution']) form.append('distribution_file', files['distribution']);
    if (files['inventory']) form.append('inventory_file', files['inventory']);
    return this.http.post('/api/monitor/upload', form);
  }
  procurementPlan(): Observable<any> {
    return this.http.get('/api/procurement/plan-summary');
  }
  procurementPlanFile(): Observable<any> {
    return this.http.get('/api/procurement/plan-file');
  }
  uploadProcurementPlan(file: File): Observable<any> {
    const form = new FormData();
    form.append('plan_file', file);
    return this.http.post('/api/procurement/plan-file', form);
  }
  restoreProcurementPlan(name: string): Observable<any> {
    return this.http.post('/api/procurement/plan-file', { restore: name });
  }
  getMonitorAutoSyncStatus(): Observable<any> {
    return this.http.get('/api/monitor/auto-sync/status');
  }
  triggerMonitorAutoSync(): Observable<any> {
    return this.http.post('/api/monitor/auto-sync/trigger', {});
  }

  searchDocuments(query = '', limit = 25): Observable<any> {
    const params = new HttpParams().set('q', query).set('limit', limit);
    return this.http.get('/api/documents/search', { params });
  }

  searchDrugs(query = '', status = 'all', page = 1, perPage = 24, group = ''): Observable<any> {
    let params = new HttpParams().set('q', query).set('status', status).set('page', page).set('per_page', perPage);
    if (group) params = params.set('group', group);
    return this.http.get('/api/drugs/search', { params });
  }
  drugDetail(code: string): Observable<any> { return this.http.get(`/api/drugs/${encodeURIComponent(code)}`); }
  drugRecords(code: string, kind: string, page = 1, query = ''): Observable<any> {
    const params = new HttpParams().set('page', page).set('per_page', 30).set('q', query);
    return this.http.get(`/api/drugs/${encodeURIComponent(code)}/records/${encodeURIComponent(kind)}`, { params });
  }
  saveInvestigation(code: string, payload: any): Observable<any> {
    return this.http.post(`/api/drugs/${encodeURIComponent(code)}/investigation`, payload);
  }
  savePrimaryVendor(code: string, payload: any): Observable<any> {
    return this.http.post(`/api/drugs/${encodeURIComponent(code)}/primary-vendor`, payload);
  }

  getDrugPos(code: string): Observable<any> {
    return this.http.get(`/api/drugs/${encodeURIComponent(code)}/pos`);
  }
  saveDrugPo(code: string, payload: any): Observable<any> {
    return this.http.post(`/api/drugs/${encodeURIComponent(code)}/pos`, payload);
  }
  updatePoDispatch(poNo: string, isSent: boolean, note: string): Observable<any> {
    return this.http.post(`/api/pos/${encodeURIComponent(poNo)}/dispatch`, {
      is_sent_to_vendor: isSent,
      sent_to_vendor_note: note,
    });
  }
  markPoReceived(poNo: string, rcvNo: string = '', lotNo: string = '', rcvDate: string = ''): Observable<any> {
    return this.http.post(`/api/pos/${encodeURIComponent(poNo)}/receive`, {
      rcv_no: rcvNo,
      lot_no: lotNo,
      rcv_date: rcvDate,
    });
  }
  getPoAlerts(): Observable<any> {
    return this.http.get('/api/pos/alerts');
  }
}
