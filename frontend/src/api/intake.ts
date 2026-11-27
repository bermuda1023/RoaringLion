import { API_BASE, fetchJson, postJson, type ContractFull, type LossFileRecord } from './client';
import type { GraphSpec, RunManifest } from '../types/contracts';

export type IntakeDoor = 'DROP_IN' | 'EMAIL' | 'FOLDER';
export type IntakeDraftStatus = 'PENDING_REVIEW' | 'CONFIRMED' | 'DISCARDED';
export type IntakeFileKind = 'YELT' | 'ELT' | 'PDF' | 'EXCEL' | 'TEXT';

export interface IntakeLayerDraft {
  layerCode: string;
  name: string;
  layerType: 'OCC_XL' | 'AGG_XL' | 'ILW_OPTION';
  limit: number;
  attachment: number;
  depositPremium: number;
  rol?: number | null;
  writtenLine: number;
  signedLine: number;
  reinstatements: number;
  confidence: number;
}

export interface IntakeLossHistoryRow {
  year?: number | null;
  occurrenceDate?: string | null;
  eventName: string;
  groundUpLossM: number;
  currency: string;
  regionPerilKey?: string | null;
  note: string;
}

export interface IntakeExtractedData {
  cedentName: string;
  programmeName: string;
  broker: string;
  inceptionDate?: string | null;
  expiryDate?: string | null;
  treatyYear: number;
  renewalPeriod: string;
  currency: string;
  regionPerils: string[];
  perils: string[];
  layers: IntakeLayerDraft[];
  lossHistory: IntakeLossHistoryRow[];
  fieldConfidence: Record<string, number>;
  warnings: string[];
  rawExcerpt: string;
}

export interface SubmissionDraftOut {
  draftId: string;
  door: IntakeDoor;
  status: IntakeDraftStatus;
  targetProgrammeId?: string | null;
  targetProgrammeLabel?: string | null;
  confirmedProgrammeId?: string | null;
  sourceFilename: string;
  sourceFileType: IntakeFileKind;
  sourceRef: string;
  sourceHash: string;
  simSetId?: string | null;
  simSetName?: string | null;
  lossFiles: LossFileRecord[];
  overallConfidence: number;
  extracted: IntakeExtractedData;
  graph: GraphSpec;
  createdAt: string;
  updatedAt: string;
  decidedAt?: string | null;
}

export interface SubmissionConfirmRequest {
  mode?: 'CREATE_NEW' | 'ATTACH_EXISTING' | null;
  targetProgrammeId?: string | null;
  extracted?: IntakeExtractedData | null;
  graph?: GraphSpec | null;
}

export interface SubmissionConfirmResponse {
  draft: SubmissionDraftOut;
  contract: ContractFull;
  action: 'CREATED_DRAFT_PROGRAMME' | 'UPDATED_PROGRAMME';
  manifest: RunManifest;
}

export interface EmailDoorStatus {
  connected: boolean;
  inboxAddress?: string | null;
  provider: string;
  message: string;
}

export interface EmailPollResult {
  connected: boolean;
  inboxAddress?: string | null;
  processedCount: number;
  skippedCount: number;
  drafts: SubmissionDraftOut[];
}

export interface WatchedProgrammeFolder {
  programmeId: string;
  cedentName: string;
  programmeName: string;
  watchedFolder: string;
  resolvedExists: boolean;
  pendingFiles: number;
}

export interface FolderDoorStatus {
  rootDir: string;
  exists: boolean;
  watchedProgrammes: WatchedProgrammeFolder[];
}

export interface FolderScanResult {
  rootDir: string;
  scannedProgrammes: number;
  pickedUpCount: number;
  skippedCount: number;
  drafts: SubmissionDraftOut[];
}

export interface IntakeStatusResponse {
  email: EmailDoorStatus;
  folder: FolderDoorStatus;
  pendingCount: number;
  maxUploadMb: number;
}

async function putJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    let msg = `API ${res.status}: ${text}`;
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed?.detail === 'string') msg = parsed.detail;
    } catch {
      // keep raw text
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

export const intakeApi = {
  getStatus: () => fetchJson<IntakeStatusResponse>('/api/intake/status'),

  listDrafts: (params?: { status?: IntakeDraftStatus; programmeId?: string; door?: IntakeDoor }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set('status', params.status);
    if (params?.programmeId) qs.set('programmeId', params.programmeId);
    if (params?.door) qs.set('door', params.door);
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return fetchJson<SubmissionDraftOut[]>(`/api/intake/drafts${suffix}`);
  },

  uploadDropIn: async (file: File, programmeId?: string | null): Promise<SubmissionDraftOut> => {
    const fd = new FormData();
    fd.append('file', file);
    if (programmeId) fd.append('programme_id', programmeId);
    const res = await fetch(`${API_BASE}/api/intake/drop-in`, {
      method: 'POST',
      body: fd,
    });
    if (!res.ok) {
      const text = await res.text();
      let msg = `Upload failed (${res.status}): ${text}`;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed?.detail === 'string') msg = parsed.detail;
      } catch {
        // keep raw text
      }
      throw new Error(msg);
    }
    return res.json() as Promise<SubmissionDraftOut>;
  },

  confirmDraft: (draftId: string, body: SubmissionConfirmRequest = {}) =>
    postJson<SubmissionConfirmResponse>(`/api/intake/drafts/${draftId}/confirm`, body),

  discardDraft: (draftId: string) =>
    postJson<SubmissionDraftOut>(`/api/intake/drafts/${draftId}/discard`, {}),

  pollEmail: () => postJson<EmailPollResult>('/api/intake/email/poll', {}),

  setWatchedFolder: (programmeId: string, watchedFolder: string | null) =>
    putJson<ContractFull>(`/api/intake/programmes/${programmeId}/watched-folder`, { watchedFolder }),

  scanFolders: (programmeId?: string) =>
    postJson<FolderScanResult>(
      programmeId ? `/api/intake/folder/scan?programmeId=${encodeURIComponent(programmeId)}` : '/api/intake/folder/scan',
      {},
    ),
};
