export type DocumentType = 'terms' | 'privacy' | 'cookies' | 'refund';
export type Rating = 'favorable' | 'neutral' | 'concerning' | 'invasive';
export type ReviewMode = 'rules_only' | 'hybrid';
export type ProfileId = 'balanced' | 'privacy_first';
export interface Auth { user: { id: string; username: string }; csrf_token: string }
export interface Job { id: string; state: 'queued' | 'running' | 'done' | 'partial' | 'failed'; stage: string; attempts: number; result_id: string | null; error: string | null; created_at: string }
export interface Finding { category: number; rating: Rating; severity: number; quote: string; start: number; end: number; explanation: string; source: 'rule' | 'library_exact' | 'ai'; rule_id: string; ai_call_id?: string | null }
export interface Clause { id: number; start: number; end: number; text: string; substantive: boolean; unrated_reason: string | null; findings: Finding[] }
export interface Category { id: number; name: string; applicable: boolean; max_severity: number; unknown: boolean; accepted_count: number }
export interface Score { best: number; worst: number; grade: string | null; coverage: number; scope_consistent: boolean }
export interface ScoreProfile { score: Score; weights: Record<number, number> }
export interface ScoreProfiles { revision: string | null; balanced: ScoreProfile; privacy_first?: ScoreProfile }
export interface Baseline { engine: Record<string, string>; clauses: Clause[]; categories: Category[]; score: Score; supported_groups: number[]; score_profiles?: ScoreProfiles }
export interface AIProvenance { id: string; provider: string; model: string; prompt_revision: string; snapshot: string; clause_ids: number[] }
export interface Report extends Baseline { id: string; document_id: string; version_id: string; title: string; document_type: DocumentType; created_at: string; captured_at: string; requested_mode: ReviewMode; effective_mode: ReviewMode; text: string; consent_provider?: string | null; consent_at?: string | null; review_status?: string | null; degradation_reason?: string | null; degradation_message?: string | null; rules_baseline?: Baseline | null; unresolved_count?: number; accepted_ai_findings?: number; ai_provenance?: AIProvenance[] }
export interface HistoryItem { id: string; title: string; document_type: DocumentType; created_at: string; requested_mode?: ReviewMode; effective_mode: ReviewMode; review_status?: string | null; degradation_reason?: string | null; coverage: number; grade: string | null; worst: number; best: number }
export interface History { items: HistoryItem[]; total: number; limit: number; offset: number }
export interface PolicyCandidate { url: string; title: string; document_type: DocumentType }
export interface DiscoveryResult { candidates: PolicyCandidate[]; limitations: string[] }
export interface CaptureMetadata { source_url: string; final_url: string; content_type: string; raw_sha256: string; text_sha256: string }
export interface Report { source_kind?: 'paste' | 'url'; capture?: CaptureMetadata | null }
export interface Report { version_number?: number; revision_number?: number }
export interface Job { operation?: 'new' | 'refresh' | 'reanalysis'; checked_at?: string | null; capture_changed?: boolean | null; result_reused?: boolean }
export interface SavedVersion { id: string; version_number: number; parent_id: string | null; captured_at: string; content_hash: string; extractor: string; analysis_count: number; latest_analysis_id: string | null }
export interface VersionHistory { items: SavedVersion[]; total: number; limit: number; offset: number; last_check: { checked_at: string; changed: boolean; reused: boolean; job_id: string } | null }
export interface SavedRevision { id: string; revision_number: number; created_at: string; requested_mode: ReviewMode; effective_mode: ReviewMode; degradation_reason: string | null }
export interface RevisionHistory { items: SavedRevision[]; total: number; limit: number; offset: number }
export interface HistoryItem { document_id?: string; version_id?: string; version_number?: number; revision_number?: number }
export interface Job { document_id?: string | null }
export interface ComparedReport { id: string; title: string; version_number: number; revision_number: number; requested_mode: ReviewMode; effective_mode: ReviewMode; degradation_message: string | null; score: Score }
export interface ComparedPassage { left: Clause | null; right: Clause | null; text_change: 'unchanged' | 'modified' | 'added' | 'removed'; interpretation_changed: boolean; similarity: number | null }
export interface ComparisonResult { id: string; kind: 'versions' | 'policies'; profile: ProfileId; left: ComparedReport; right: ComparedReport; summary: { source_text_changed: boolean; text_changes: number; interpretation_changes: number }; limits: { similarity_limited: boolean }; passages: ComparedPassage[]; total: number; limit: number; offset: number }
export interface ComparisonHistory { items: { id: string; left_title: string; right_title: string; created_at: string; profile: ProfileId }[]; total: number }
export interface DeletionReceipt { document_id: string; state: 'pending' | 'complete'; error_code: string | null; created_at: string; completed_at: string | null }
