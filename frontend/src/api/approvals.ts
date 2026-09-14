import { api, longRequest } from './client'
import type { Approval } from './types'
export async function getApproval(id: string) { return (await api.get<Approval>(`/approvals/${encodeURIComponent(id)}`)).data }
export async function decideApproval(id: string, decision: 'approve' | 'reject') {
  return (await api.post<Approval>(`/approvals/${encodeURIComponent(id)}/${decision}`, undefined, longRequest)).data
}
