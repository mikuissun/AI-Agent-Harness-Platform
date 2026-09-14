export type Status = 'PENDING' | 'RUNNING' | 'INTERRUPTED' | 'COMPLETED' | 'FAILED'
export interface Task { id: number; title: string; instruction: string; status: Status; created_at?: string }
export interface TraceEvent { sequence: number; iteration: number; event_type: string; name: string; success: boolean; summary: string }
export interface Run {
  run_id: string; thread_id: string; task_id: number; status: Status; output: string | null; error: string | null;
  pending_approval_id: string | null; trace: TraceEvent[];
  usage: { calls: number; prompt_tokens: number; completion_tokens: number }
}
export interface Approval {
  approval_id: string; run_id: string; task_id: number; tool_name: string; risk_level: string;
  status: 'PENDING' | 'APPROVED' | 'EXECUTED' | 'FAILED' | 'REJECTED';
  arguments_summary: { path?: string; characters?: number; sha256?: string };
}
